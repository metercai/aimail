/**
 * dsh-aimail inbound — AgentMail inbound endpoint for this profile.
 *
 * node:http listener (headless-friendly). Receives bridge-forwarded raw
 * webhook bodies at POST {path} (default /agentmail/deliver):
 *   recipient routing (resolveByRecipient: exact → persona-strip fallback)
 *   → HMAC verify (X-Webhook-Signature vs webhook_secret from agentmail.json)
 *   → TS preprocess chain (mail-core, DSH-PREPROCESS-CONTRACT.md)
 *   → ping/pong intercept (three-stage logs, swallowed)
 *   → un-intercepted: deliver to the bound dsh session (live followup, cold
 *     resume) or spawn a fresh disposable session when unbound — context
 *     continuity is aimail's (local meta threading), not the session's.
 *     200 ack on delivery; 503 on session-create failure (bridge retries).
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { randomUUID } from 'node:crypto'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { processInboundMail, verifySignature, routeAddressFromHeaders, type InboundPayload } from '@aimail/mail-core'
import type { MailService } from './mail-service.js'

export const name = 'mail-inbound'
export const inject = ['mail', 'agents']

export interface Config {
  /** Listen host (default 127.0.0.1). */
  host?: string
  /** Listen port (default AIMAIL_INBOUND_PORT or 9099). */
  port?: number
  /** Deliver path (default /agentmail/deliver). */
  path?: string
}

interface DeliveryOutcome {
  status: string
  detail?: string
}

function writeJson(res: ServerResponse, code: number, body: DeliveryOutcome): void {
  const text = JSON.stringify(body)
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(text)
}

function readBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

export function apply(ctx: Context, config: Config = {}): () => void {
  const mail = ctx.get('mail') as MailService | undefined
  if (mail === undefined) {
    throw new Error('mail-inbound requires the mail service: mount dsh-aimail/mail-service first')
  }
  const host = config.host ?? '127.0.0.1'
  const port = config.port ?? Number(process.env.AIMAIL_INBOUND_PORT ?? 9099)
  const deliverPath = config.path ?? '/agentmail/deliver'

  const server = createServer(async (req, res) => {
    try {
      if (req.method !== 'POST' || (req.url ?? '').split('?')[0] !== deliverPath) {
        writeJson(res, 404, { status: 'not_found' })
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
      const toRaw = Array.isArray(payload.to) ? payload.to : typeof payload.to === 'string' ? [payload.to] : []
      const routeCandidates: unknown[] = routeAddr ? [routeAddr] : toRaw
      let cfg
      let agentAddr = ''
      for (const t of routeCandidates) {
        const addr = String(t).trim()
        if (!addr.includes('@')) continue
        const c = await mail.resolveByRecipient(addr)
        if (c) {
          cfg = c
          agentAddr = addr
          break
        }
      }
      if (!cfg) {
        writeJson(res, 200, { status: 'no_agent', detail: `no binding for ${routeAddr || toRaw.join(',')}` })
        return
      }

      // HMAC verify (per-address webhook_secret)
      const sig = (req.headers['x-webhook-signature'] as string) ?? ''
      if (!verifySignature(rawBody, sig, cfg.webhook_secret ?? '')) {
        writeJson(res, 401, { status: 'bad_signature' })
        return
      }

      // TS preprocess chain (13 steps) + ping/pong intercept
      const result = await processInboundMail(payload, headers as Record<string, string>, {
        systemId: cfg.system_id,
        email: cfg.email,
      })
      if (result === null) {
        writeJson(res, 200, { status: 'intercepted' })
        return
      }

      // Deliver to a dsh session:
      //  - cfg.session_id set + live  → followup that session (UI continuity)
      //  - cfg.session_id set + cold  → resume it, else fall through
      //  - unbound (or resume failed) → spawn a FRESH session. Context
      //    continuity is aimail's job (local meta threading + email_summary),
      //    not the session's — per the deployment decision each inbound email
      //    gets its own disposable session.
      const agents = ctx.get('agents') as {
        get(id: unknown): Agent | undefined
        resume(opts: unknown): Promise<{ agent: Agent }>
        create(opts: { sessionId: string; meta?: { cwd?: string } }): Promise<{ agent: Agent; dispose(): Promise<void> }>
      } | undefined
      if (agents === undefined) {
        writeJson(res, 200, { status: 'no_agents_service', detail: 'dsh-agent not mounted' })
        return
      }
      const boundId = cfg.session_id ?? ''
      const message = createUserMessage({
        content: [{ type: 'text', text: JSON.stringify({ ...result, to: agentAddr }) }],
        source: { kind: 'user' },
      })
      const live = boundId ? agents.get(boundId) : undefined
      if (live) {
        live.followup(message)
        writeJson(res, 200, { status: 'delivered', detail: 'followup queued' })
        return
      }
      if (boundId) {
        try {
          const handle = await agents.resume({ resumeSessionId: boundId })
          handle.agent.followup(message)
          writeJson(res, 200, { status: 'resumed', detail: 'cold session resumed + followup queued' })
          return
        } catch {
          // resume failed (no persistence, stale id) — fall through to a fresh session
        }
      }
      // Fresh disposable session for this email.
      const sessionId = randomUUID()
      try {
        const handle = await agents.create({ sessionId, meta: { cwd: process.cwd() } })
        handle.agent.followup(message)
        // The session is disposable: tear it down once the turn settles.
        void handle.agent.whenIdle().then(() => handle.dispose()).catch(() => {})
        writeJson(res, 200, { status: 'delivered', detail: `fresh session ${sessionId}` })
      } catch (e) {
        // 503 (not 2xx) so the bridge does NOT ack and will retry; a 200 here
        // would silently swallow the email.
        writeJson(res, 503, { status: 'session_create_failed', detail: e instanceof Error ? e.message : String(e) })
      }
    } catch (e) {
      writeJson(res, 500, { status: 'error', detail: e instanceof Error ? e.message : String(e) })
    }
  })

  server.listen(port, host)
  return () => {
    server.close()
  }
}
