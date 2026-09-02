/**
 * openclaw-aimail inbound — registers an HTTP route inside the gateway
 * process (no new port; bridge push target unchanged).
 *
 * Handler chain (mirrors dsh inbound):
 *   recipient resolution (resolveByRecipient: exact → persona-strip fallback,
 *   so mail to a role alias routes to the owning agent)
 *   → verifySignature (byte-exact HMAC, mail-core)
 *   → processInboundMail (13-step chain + ping/pong intercept)
 *   → agent turn with the full enriched JSON payload (rendering parity =
 *     json.dumps equivalence, established acceptance bar).
 *
 * Delivery: api.runtime.subagent.run (session-scoped run) primary,
 * api.runtime.gateway.request (explicit agent targeting) fallback (R3).
 */
import type { IncomingMessage, ServerResponse } from 'node:http'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { verifySignature, processInboundMail, routeAddressFromHeaders, type InboundPayload } from '@aimail/mail-core'
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry'
import { resolveByRecipient } from '@aimail/mail'
import { readPointer } from './identity.js'

export const INBOUND_PATH = '/aimail/inbound'

function writeJson(res: ServerResponse, code: number, body: unknown): void {
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(JSON.stringify(body))
}

function readBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

/**
 * Deliver an enriched inbound payload to the owning agent's session via the
 * gateway's internal /hooks/agent endpoint (loopback + hook token; fixed
 * sessionKey so all mail converges on one agent session; deliver:false —
 * the agent replies via send_mail). subagent.run/chat.send were tried first
 * historically but require operator.write scope the plugin does not have.
 */
async function deliverToAgent(
  api: OpenClawPluginApi,
  opts: { agentId: string; message: string },
): Promise<{ status: string; detail: string }> {
  void api
  const hooksToken = readHooksToken()
  const r = await fetch('http://127.0.0.1:18789/hooks/agent', {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(hooksToken ? { Authorization: `Bearer ${hooksToken}` } : {}),
    },
    body: JSON.stringify({
      message: opts.message,
      name: 'agentmail',
      sessionKey: 'agent:main:hook:amail',
      deliver: false,
    }),
  })
  if (!r.ok) {
    return { status: 'dispatch_failed', detail: `hooks/agent HTTP ${r.status}` }
  }
  return { status: 'delivered', detail: 'hooks/agent accepted' }
}

function readHooksToken(): string {
  try {
    const home = process.env.HOME ?? process.env.USERPROFILE ?? ''
    const cfgPath = path.join(home, '.openclaw', 'openclaw.json')
    const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf-8')) as {
      hooks?: { token?: string }
    }
    return String(cfg.hooks?.token ?? '')
  } catch {
    return ''
  }
}

export function createInboundHandler(api: OpenClawPluginApi) {
  return async (
    req: IncomingMessage,
    res: ServerResponse,
  ): Promise<void> => {
    try {
      if (req.method !== 'POST') {
        writeJson(res, 405, { status: 'method_not_allowed' })
        return
      }
      const rawBody = await readBody(req)
      let payload: InboundPayload
      try {
        payload = JSON.parse(rawBody.toString('utf-8')) as InboundPayload
      } catch {
        writeJson(res, 400, { status: 'bad_json' })
        return
      }

      // Inbound routing (Q3 — mirror Python bridge routing): the per-delivery
      // target is authoritative. The bridge injects X-AIMail-Email (legacy
      // X-Amail-Email fallback) on each single-delivery POST; payload.to is
      // the FILTERED full list (external recipients first), so to[0] is often
      // an external address. Use the header when present; only iterate toRaw
      // when the header is absent (batch deliveries carry no such header).
      const headers = {
        ...(req.headers as Record<string, string | string[]>),
        ...(payload.headers ?? {}),
      } as Record<string, unknown>
      const routeAddr = routeAddressFromHeaders(headers)
      const toRaw = Array.isArray(payload.to)
        ? payload.to
        : typeof payload.to === 'string'
          ? [payload.to]
          : []
      const routeCandidates: unknown[] = routeAddr ? [routeAddr] : toRaw
      let agentAddr = ''
      let cfg: Awaited<ReturnType<typeof resolveByRecipient>>
      for (const t of routeCandidates) {
        const addr = String(t).trim()
        if (!addr.includes('@')) continue
        const c = await resolveByRecipient(addr)
        if (c) {
          cfg = c
          agentAddr = addr
          break
        }
      }
      if (!cfg) {
        // Fall back to the pointer email when no recipient matched (e.g. the
        // bridge forwarded a bare address) — keep no_agent intercept semantics.
        const ptr = await readPointer()
        if (ptr.email) cfg = await resolveByRecipient(ptr.email)
        if (cfg && !agentAddr) agentAddr = ptr.email ?? ''
      }
      if (!cfg) {
        writeJson(res, 200, {
          status: 'no_agent',
          detail: `no binding for ${routeAddr || toRaw.join(',')}`,
        })
        return
      }

      // HMAC verify (per-address webhook_secret)
      const sig = (req.headers['x-webhook-signature'] as string) ?? ''
      if (!verifySignature(rawBody, sig, cfg.webhook_secret ?? '')) {
        writeJson(res, 401, { status: 'bad_signature' })
        return
      }

      // TS preprocess chain (13 steps) + ping/pong intercept
      const result = await processInboundMail(
        payload,
        headers as Record<string, string>,
        { systemId: cfg.system_id, email: cfg.email },
      )
      if (result === null) {
        writeJson(res, 200, { status: 'intercepted' })
        return
      }

      // Agent turn with the full enriched JSON payload
      const agentId = cfg.agent_id || 'main'
      const out = await deliverToAgent(api, {
        agentId,
        message: JSON.stringify({ ...result, to: agentAddr }),
      })
      writeJson(res, 200, out)
    } catch (e) {
      writeJson(res, 500, {
        status: 'error',
        detail: e instanceof Error ? e.message : String(e),
      })
    }
  }
}
