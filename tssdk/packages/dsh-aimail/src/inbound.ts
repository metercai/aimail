/**
 * dsh-aimail inbound — AIMail inbound endpoint for this profile.
 *
 * node:http listener (headless-friendly). Receives bridge-forwarded raw
 * webhook bodies at POST {path} (default /aimail/inbound):
 *   recipient routing (resolveByRecipient: exact → persona-strip fallback)
 *   → HMAC verify (X-Webhook-Signature vs webhook_secret from agentmail.json)
 *   → TS preprocess chain (mail-core, DSH-PREPROCESS-CONTRACT.md)
 *   → ping/pong intercept (three-stage logs, swallowed)
 *   → un-intercepted: deliver to the bound dsh session (live followup, cold
 *     resume) or spawn a fresh disposable session when unbound — context
 *     continuity is aimail's (local meta threading), not the session's.
 *     200 ack on delivery; 503 on session-create failure (bridge retries).
 *
 * Pull entry (2026-09-27): an address activated by an activation CODE has no
 * push path (the gateway stores webhook_url=NULL for it), so this plugin also
 * polls its own mailbox on a timer and delivers through the SAME chain above
 * (`deliverInbound`). Enabled only for agent-scope bindings — the decision and
 * the loop live in mail-core (`startAgentPullEntries` / `startPolling`).
 */
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import { randomUUID } from 'node:crypto'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { processInboundMail, verifySignature, routeAddressFromHeaders, updateAgentConfig, loadAgentConfig, saveAgentConfig, startAgentPullEntries, type AgentConfig, type AgentPullHandle, type AgentPullOverrides, INBOUND_PATH, INBOUND_PORTS, type InboundPayload } from '@aimail/mail-core'
import { notifyInboundForSystem, formatInboundNotifyLine, isInboundNotifyWarning } from '@aimail/mail-core'
import type { MailService } from './mail-service.js'

export const name = 'mail-inbound'
export const inject = ['mail', 'agents']

export interface Config {
  /** Listen host (default 127.0.0.1). */
  host?: string
  /** Listen port (default AIMAIL_INBOUND_PORT or 9099). */
  port?: number
  /** Deliver path (default /aimail/inbound). */
  path?: string
}

interface DeliveryOutcome {
  status: string
  detail?: string
}

/** The resolved target of one inbound payload (binding + routed address). */
export interface InboundTarget {
  cfg: AgentConfig
  agentAddr: string
}

/** Result of the shared inbound chain (routing→deliver). */
export interface InboundOutcome {
  status: string
  detail?: string | undefined
  /**
   * Did the mail reach a dsh session? `false` ⇒ a pulled delivery must NOT be
   * acked (it re-pulls next round) — the pull-side equivalent of the push
   * side's 503.
   */
  ok: boolean
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

/**
 * Recipient routing — the ONE routing step both entries share (push handler +
 * pull loop). The per-delivery target is authoritative: the bridge injects
 * X-AIMail-Email on each single-delivery POST (legacy); payload.to is the
 * FILTERED full list (external recipients first), so to[0] is often an external
 * address. Use the header when present; only iterate toRaw when the header is
 * absent (batch deliveries carry no such header).
 */
export async function resolveInboundTarget(
  mail: MailService,
  payload: InboundPayload,
  headers: Record<string, unknown>,
): Promise<InboundTarget | undefined> {
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
  return cfg ? { cfg, agentAddr } : undefined
}

/**
 * Post-routing inbound chain: preprocess (13 steps + ping/pong) → deliver to a
 * dsh session. Shared by the push handler and the pull loop.
 *
 * ⚠ The TRUST step is not here: pushed mail is verified by the per-address HMAC
 * (bridge↔endpoint boundary), pulled mail by the gateway's own verification of
 * the agent-scope key. Routing, enrichment and delivery are one chain.
 */
export async function deliverInbound(
  ctx: Context,
  target: InboundTarget,
  payload: InboundPayload,
  headers: Record<string, unknown>,
): Promise<InboundOutcome> {
  const { cfg, agentAddr } = target
  // TS preprocess chain (13 steps) + ping/pong intercept
  const result = await processInboundMail(payload, headers as Record<string, string>, {
    systemId: cfg.system_id,
    email: cfg.email,
  })
  if (result === null) {
    return { status: 'intercepted', ok: true }
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
    create(opts: { sessionId: string; meta?: { cwd?: string }; agentOptions?: unknown }): Promise<{ agent: Agent; dispose(): Promise<void> }>
  } | undefined
  if (agents === undefined) {
    return { status: 'no_agents_service', detail: 'dsh-agent not mounted', ok: false }
  }
  // Model route: the deployment's default selection (base bundle's
  // `agent-default-model` row, e.g. deepseek-official/deepseek-v4-flash)
  // — same source the web UI's api-proxy uses for agents.create().
  // Without it the turn dies with "no provider/model".
  const agentOptions = (ctx.get('agentDefaultModel') as { currentSelection(): unknown } | undefined)
    ?.currentSelection()
  const boundId = cfg.session_id ?? ''
  const message = createUserMessage({
    content: [{ type: 'text', text: JSON.stringify({ ...result, to: agentAddr }) }],
    source: { kind: 'user' },
  })
  const live = boundId ? agents.get(boundId) : undefined
  if (live) {
    live.followup(message)
    return { status: 'delivered', detail: 'followup queued', ok: true }
  }
  if (boundId) {
    try {
      const handle = await agents.resume({ resumeSessionId: boundId, agentOptions })
      handle.agent.followup(message)
      return { status: 'resumed', detail: 'cold session resumed + followup queued', ok: true }
    } catch {
      // resume failed (no persistence, stale id) — fall through to a fresh session
    }
  }
  // Fresh disposable session for this email.
  const sessionId = randomUUID()
  try {
    // Bind this session into the agent's config so the mail tools can
    // resolve credentials (resolveBySessionId matches agentmail.json's
    // session_id). Unbind again once the turn settles — but only if the
    // binding is still OURS (a concurrent email may have re-bound).
    await updateAgentConfig(cfg.system_id, cfg.email, { session_id: sessionId })
    const handle = await agents.create({ sessionId, meta: { cwd: process.cwd() }, agentOptions })
    handle.agent.followup(message)
    void handle.agent.whenIdle()
      .then(async () => {
        const cur = await loadAgentConfig(cfg.system_id, cfg.email)
        if (cur && cur.session_id === sessionId) {
          const { session_id: _drop, ...rest } = cur
          await saveAgentConfig(rest as typeof cur, cfg.system_id)
        }
      })
      .then(() => handle.dispose())
      .catch(() => {})
    return { status: 'delivered', detail: `fresh session ${sessionId}`, ok: true }
  } catch (e) {
    // push side: 503 (not 2xx) so the bridge does NOT ack and will retry; a 200
    // here would silently swallow the email. Pull side: !ok ⇒ not acked.
    return {
      status: 'session_create_failed',
      detail: e instanceof Error ? e.message : String(e),
      ok: false,
    }
  }
}

/** The push handler (HTTP route) around the shared chain. */
export function createInboundHandler(ctx: Context, mail: MailService, deliverPath: string) {
  return async (req: IncomingMessage, res: ServerResponse): Promise<void> => {
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

      const headers = {
        ...(req.headers as Record<string, string | string[]>),
        ...(payload.headers ?? {}),
      } as Record<string, unknown>

      const target = await resolveInboundTarget(mail, payload, headers)
      if (!target) {
        writeJson(res, 200, { status: 'no_agent', detail: 'no binding' })
        return
      }

      // HMAC verify (per-address webhook_secret) — push-only trust step; pulled
      // mail is authenticated by the gateway on the agent-scope key.
      const sig = (req.headers['x-webhook-signature'] as string) ?? ''
      if (!verifySignature(rawBody, sig, target.cfg.webhook_secret ?? '')) {
        writeJson(res, 401, { status: 'bad_signature' })
        return
      }

      const out = await deliverInbound(ctx, target, payload, headers)
      // Non-2xx on delivery failure so the bridge retries (unchanged semantics).
      // `detail` is omitted when absent (exactOptionalPropertyTypes: an explicit
      // `undefined` would not satisfy DeliveryOutcome's optional string).
      const body: DeliveryOutcome =
        out.detail === undefined ? { status: out.status } : { status: out.status, detail: out.detail }
      writeJson(res, out.ok ? 200 : 503, body)
    } catch (e) {
      writeJson(res, 500, { status: 'error', detail: e instanceof Error ? e.message : String(e) })
    }
  }
}

/**
 * Pull entry (agent-scope bindings only) — the missing production wire for the
 * address-code flow. Pulled mail enters `deliverInbound`: the very same chain
 * pushed mail takes (routing → preprocess → session delivery). Never started
 * for system/bridge bindings (push-served): the decision lives in mail-core.
 */
export async function startInboundPull(
  ctx: Context,
  opts: {
    log?: (line: string) => void
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
  } = {},
): Promise<AgentPullHandle[]> {
  const mail = ctx.get('mail') as MailService | undefined
  if (mail === undefined) {
    throw new Error('mail-inbound requires the mail service: mount dsh-aimail/mail-service first')
  }
  return await startAgentPullEntries({
    // Scope = the plugin's own system when it has one, else every system
    // (the per-address binding file is the authority — single source).
    systemId: opts.systemId ?? mail.systemId,
    ...(opts.log ? { log: opts.log } : {}),
    ...(opts.env ? { env: opts.env } : {}),
    ...(opts.overrides ? { overrides: opts.overrides } : {}),
    onEmail: async (cfg, pulled) => {
      if (typeof pulled.body !== 'object' || pulled.body === null) {
        throw new Error(
          `pulled delivery ${pulled.id} for ${cfg.email} carries no JSON payload — not acking`,
        )
      }
      // The delivery's own address is authoritative for routing (it is what the
      // agent-scope key was verified against server-side).
      const headers: Record<string, unknown> = {
        ...(pulled.headers ?? {}),
        'x-aimail-email': pulled.email,
      }
      const target = await resolveInboundTarget(mail, pulled.body as InboundPayload, headers)
      if (!target) {
        throw new Error(`pulled delivery ${pulled.id}: no binding for ${pulled.email} — not acking`)
      }
      const out = await deliverInbound(ctx, target, pulled.body as InboundPayload, headers)
      if (!out.ok) {
        throw new Error(
          `pulled delivery ${pulled.id}: inbound chain did not deliver (${out.status}: ${out.detail ?? ''}) — not acking`,
        )
      }
    },
  })
}

export function apply(ctx: Context, config: Config = {}): () => void {
  const mail = ctx.get('mail') as MailService | undefined
  if (mail === undefined) {
    throw new Error('mail-inbound requires the mail service: mount dsh-aimail/mail-service first')
  }

  // SDK-shipped skill → <dshHome>/skills/agentmail/ (idempotent;
  // identical-content skip; dshHome resolution mirrors mail-service).
  // SKILL.md owns the inbound-message protocol (6-step flow) — a
  // different category from tool registration (tool usage). Symmetric
  // across openclaw/dsh/pi.
  try {
    const dshHome =
      process.env.AIMAIL_SYSTEM_HOME?.trim() ||
      process.env.DSH_HOME?.trim() ||
      path.join(os.homedir(), '.dsh')
    const skillSrc = path.join(
      path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'skills')
    const skillDst = path.join(dshHome, 'skills', 'agentmail')
    fs.mkdirSync(skillDst, { recursive: true })
    for (const f of ['SKILL.md', 'DESCRIPTION.md']) {
      const from = path.join(skillSrc, f)
      const to = path.join(skillDst, f)
      if (!fs.existsSync(from)) {
        // missing package resources = packaging/materialize defect: loud but must not block inbound handling
        console.error(`[dsh-aimail] skill resource missing: ${from} ` +
          '(repo: run scripts/materialize-resources.sh; installed: reinstall the package)')
        continue
      }
      if (fs.existsSync(to) && fs.readFileSync(from).equals(fs.readFileSync(to))) continue
      fs.copyFileSync(from, to)
    }
  } catch {
    // non-fatal; retried on next plugin start
  }
  const host = config.host ?? '127.0.0.1'
  const port = config.port ?? Number(process.env.AIMAIL_INBOUND_PORT ?? INBOUND_PORTS.dsh)
  const deliverPath = config.path ?? INBOUND_PATH

  const server = createServer(createInboundHandler(ctx, mail, deliverPath))

  // Pull loops (agent-scope bindings only). Held here so the fiber's dispose
  // stops them with the listener — the host lifecycle owns both.
  let pullHandles: AgentPullHandle[] = []

  server.listen(port, host, () => {
    // Inbound notification (owner ruling 2026-09-28, SDK 去桥化): the listener is
    // up, so this host tells the environment master (the `aimail` CLI) — once per
    // address it serves — that inbound is live:
    //     aimail address -a <addr> --inbound-live
    // The CLI owns the environment (routes included) and decides what that means;
    // the SDK speaks no bridge. Best-effort by contract: argv only, 4s timeout,
    // never blocks this callback, never throws into the host (a missing CLI is one
    // debug line — the host keeps serving and self-registering).
    void notifyInboundForSystem('live')
      .then((outcomes) => {
        for (const o of outcomes) {
          const line = `[dsh-aimail] ${formatInboundNotifyLine(o, 'live')}`
          if (isInboundNotifyWarning(o)) console.warn(line)
          else console.log(line)
        }
      })
      .catch((e: unknown) => {
        console.warn(
          `[dsh-aimail] inbound notify failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`,
        )
      })

    // Pull entry: inbound is live ⇒ the pulled mail can enter the same chain.
    // Only agent-scope (address-code) bindings arm a loop (mail-core decides).
    void startInboundPull(ctx, { log: (line) => console.log(line) })
      .then((handles) => {
        pullHandles = handles
      })
      .catch((e: unknown) => {
        console.warn(
          `[dsh-aimail] pull entry failed to start: ${e instanceof Error ? e.message : String(e)}`,
        )
      })
  })
  return () => {
    // Inbound down (owner ruling 2026-09-28): this host stops serving, so it says
    // so per address — `aimail address -a <addr> --inbound-down`. Best-effort,
    // fire-and-forget: the CLI decides, and a missing CLI changes nothing here.
    void notifyInboundForSystem('down')
      .then((outcomes) => {
        for (const o of outcomes) {
          const line = `[dsh-aimail] ${formatInboundNotifyLine(o, 'down')}`
          if (isInboundNotifyWarning(o)) console.warn(line)
          else console.log(line)
        }
      })
      .catch((e: unknown) => {
        console.warn(
          `[dsh-aimail] inbound down notify failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`,
        )
      })
    for (const h of pullHandles) h.stop()
    pullHandles = []
    server.close()
  }
}
