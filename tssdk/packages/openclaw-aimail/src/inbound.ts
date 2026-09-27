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
import {
  verifySignature,
  processInboundMail,
  routeAddressFromHeaders,
  logAimailDispatch,
  startAgentPullEntries,
  type AgentConfig,
  type AgentPullHandle,
  type AgentPullOverrides,
  type InboundPayload,
  INBOUND_PATH,
} from '@aimail/mail-core'
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry'
import { resolveByRecipient } from '@aimail/mail'
import { gatewayPort, hooksPath, readPointer, resolveAgentId } from './identity.js'

// 契约常量唯一副本 = @aimail/mail-core(src/contract.ts ← contract/aimail-contract.json)。
// 这里 re-export 保持本模块既有公开面(identity.ts / index.ts 从 './inbound.js' 取它)。
export { INBOUND_PATH }

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
 * gateway's internal /hooks/agent endpoint (loopback + hook token).
 * sessionKey = `agent:{cfg.agent_id}:hook:aimail` — routed to the bound
 * agent's session (multi-address: each registered agent gets its own
 * sessionKey; deliver:false — the agent replies via send_mail).
 * subagent.run/chat.send were tried first historically but require
 * operator.write scope the plugin does not have.
 */
export async function deliverToAgent(
  api: OpenClawPluginApi,
  opts: { agentId: string; message: string },
): Promise<{ status: string; detail: string }> {
  void api
  const hooksToken = readHooksToken()
  const agentId = opts.agentId || resolveAgentId() || 'main'
  const r = await fetch(`http://127.0.0.1:${gatewayPort()}${hooksPath()}/agent`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(hooksToken ? { Authorization: 'Bearer ' + hooksToken } : {}),
    },
    body: JSON.stringify({
      message: opts.message,
      name: 'aimail',
      // S1b (2026-09-27): 必须显式给 agentId —— 宿主文档的 Hook agent payload 规定
      // "agentId ... Must name a configured agent"、"Required when no implicit/retained
      // owner can be resolved"，不给就是 400(实测: 端点在了、认证过了, 仍 400)。
      agentId,
      // S1e (2026-09-27): **不送 caller sessionKey**。直连 /agent 上调用方自带 key 需要宿主
      // 显式 allowRequestSessionKey + 前缀白名单(文档 "Hook session and agent policy"),
      // 否则按合同 400(routing/session policy)。省略 ⇒ 宿主生成 hook:<uuid>,
      // 每次邮件都是干净的新 turn(本集成不需要会话复用)。
      deliver: false,
    }),
  })
  if (!r.ok) {
    // S1d (2026-09-27): 宿主文档明确要求 "Read the `error` before retrying" ——
    // 只记状态码会把"为什么被拒"丢掉(404/401/400 三轮都吃过这个亏)。
    let why = ''
    try {
      why = (await r.text()).slice(0, 300)
    } catch {
      why = ''
    }
    return { status: 'dispatch_failed', detail: `hooks/agent HTTP ${r.status}${why ? ': ' + why : ''}` }
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

/** The resolved target of one inbound payload (binding + routed address). */
export interface InboundTarget {
  cfg: AgentConfig
  agentAddr: string
}

/** Result of the shared inbound chain (pre-preprocess→deliver). */
export interface InboundOutcome {
  status: string
  detail?: string | undefined
  /**
   * Did the mail reach the owning agent's session? `false` means the caller
   * must NOT ack a pulled delivery (it re-pulls next round) — that is the
   * pull-side equivalent of the push side's non-2xx (bridge retry).
   */
  ok: boolean
}

/**
 * Recipient routing — the ONE routing step both entry paths share (push
 * handler + pull loop). The per-delivery target is authoritative: the bridge
 * injects X-AIMail-Email on each single-delivery POST; payload.to is the
 * FILTERED full list (external recipients first), so to[0] is often an
 * external address. Use the header when present; only iterate toRaw when the
 * header is absent (batch deliveries carry no such header).
 */
export async function resolveInboundTarget(
  payload: InboundPayload,
  headers: Record<string, unknown>,
): Promise<InboundTarget | undefined> {
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
  return cfg ? { cfg, agentAddr } : undefined
}

/**
 * Post-routing inbound chain: preprocess (13 steps + ping/pong intercept) →
 * deliver to the owning agent's session → dispatch log.
 *
 * ⚠ The TRUST step is deliberately not in here: pushed mail is verified by
 * the per-address HMAC (bridge↔endpoint boundary), pulled mail by the
 * gateway's own verification of the agent-scope key on `/admin/pending`.
 * Routing, enrichment and delivery are shared — one chain, two entries.
 */
export async function deliverInbound(
  api: OpenClawPluginApi,
  target: InboundTarget,
  payload: InboundPayload,
  headers: Record<string, unknown>,
): Promise<InboundOutcome> {
  const { cfg, agentAddr } = target
  const result = await processInboundMail(
    payload,
    headers as Record<string, string>,
    { systemId: cfg.system_id, email: cfg.email },
  )
  if (result === null) {
    // ping/pong & other intercepts: handled, nothing to deliver.
    return { status: 'intercepted', ok: true }
  }
  const agentId = cfg.agent_id || 'main'
  const out = await deliverToAgent(api, {
    agentId,
    message: JSON.stringify({ ...result, to: agentAddr }),
  })
  // E3-① (owner ruling 2026-09-27): record the dispatch outcome in the SAME per-agent
  // log the inbound line goes to. Measured on the CLI gate's J4e: without this line a
  // refused/suppressed dispatch was invisible — the log showed only {"event":"inbound"}
  // and nothing told the operator that no agent ever took the mail.
  await logAimailDispatch(cfg.email, out.status, out.detail, agentAddr)
  return { status: out.status, detail: out.detail, ok: out.status === 'delivered' }
}

/**
 * Pull entry (agent-scope bindings only) — the missing production wire for the
 * address-code flow: an address activated by an activation code has NO push
 * path (the gateway stores `webhook_url = NULL` for it), so the adapter must
 * fetch its own mail on a timer. Pulled mail enters `deliverInbound`, i.e. the
 * very same chain pushed mail takes.
 *
 * Not started for system/bridge bindings (those are push-served) — the
 * decision is mail-core's (`startAgentPullEntries`), never re-implemented
 * here. Returns the handles; the host stops them on its own shutdown (the
 * timers are unref'd, so the process can always exit).
 */
export async function startInboundPull(
  api: OpenClawPluginApi,
  opts: {
    log?: (line: string) => void
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
  } = {},
): Promise<AgentPullHandle[]> {
  const ptr = await readPointer()
  return await startAgentPullEntries({
    systemId: opts.systemId ?? ptr.system_id ?? '',
    ...(opts.log ? { log: opts.log } : {}),
    ...(opts.env ? { env: opts.env } : {}),
    ...(opts.overrides ? { overrides: opts.overrides } : {}),
    onEmail: async (cfg, mail) => {
      if (typeof mail.body !== 'object' || mail.body === null) {
        throw new Error(
          `pulled delivery ${mail.id} for ${cfg.email} carries no JSON payload — not acking`,
        )
      }
      // The delivery's own address is authoritative for routing (it is what
      // the agent-scope key was verified against server-side).
      const headers: Record<string, unknown> = {
        ...(mail.headers ?? {}),
        'x-aimail-email': mail.email,
      }
      const target = await resolveInboundTarget(mail.body as InboundPayload, headers)
      if (!target) {
        throw new Error(
          `pulled delivery ${mail.id}: no binding for ${mail.email} — not acking`,
        )
      }
      const out = await deliverInbound(api, target, mail.body as InboundPayload, headers)
      if (!out.ok) {
        throw new Error(
          `pulled delivery ${mail.id}: inbound chain did not deliver (${out.status}: ${out.detail ?? ''}) — not acking`,
        )
      }
    },
  })
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

      const headers = {
        ...(req.headers as Record<string, string | string[]>),
        ...(payload.headers ?? {}),
      } as Record<string, unknown>

      const target = await resolveInboundTarget(payload, headers)
      if (!target) {
        writeJson(res, 200, { status: 'no_agent', detail: 'no binding' })
        return
      }

      // HMAC verify (per-address webhook_secret) — the bridge↔endpoint trust
      // boundary. Pulled mail carries no bridge, so this step is push-only
      // (the gateway authenticated the agent-scope key instead).
      const sig = (req.headers['x-webhook-signature'] as string) ?? ''
      if (!verifySignature(rawBody, sig, target.cfg.webhook_secret ?? '')) {
        writeJson(res, 401, { status: 'bad_signature' })
        return
      }

      const out = await deliverInbound(api, target, payload, headers)
      writeJson(res, 200, { status: out.status, detail: out.detail })
    } catch (e) {
      writeJson(res, 500, {
        status: 'error',
        detail: e instanceof Error ? e.message : String(e),
      })
    }
  }
}
