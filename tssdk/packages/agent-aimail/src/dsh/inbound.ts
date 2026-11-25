/**
 * dsh delivery core — the ONE inbound delivery implementation for this host (SSE pull only).
 *
 * There is no HTTP listener and no HMAC path any more (SSE-V2, owner ruling 2026-11-25): mail arrives
 * because `mail-hub` pulls the gateway stream, and every pulled mail enters `deliverInbound()` below.
 *
 * Why this file exists separately from `mail-hub`: the hub owns *streams/bindings* (which address is
 * armed, pause/resume, status), while delivery owns *routing + session placement* (contract §9):
 *
 *   E1 direct reply → the session that sent the parent mail (hard, inside its anchor)
 *   E2 thread      → the session this thread was last delivered to (same anchor)
 *   E3 otherwise   → a fresh session in the bound workspace
 *
 * C16 (anchor isolation) and C17 (`session-gone` redirect) are decided in `runtime/routing.ts` and
 * recorded on the dispatch line, so ops can see why a mail did — or did not — go to its session.
 *
 * Before this refactor the shipped path used a second, weaker router (`routeThread`: thread-level
 * only, no E1, no dispatch line) while this module — the one the fixtures tested — was not mounted.
 * One path, one decision, one log now.
 */
import { randomUUID } from 'node:crypto'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

import {
  combineSkillNames,
  loadAgentConfig,
  logAimailDispatch,
  packageResourcesRoot,
  processInboundMail,
  routeAddressFromHeaders,
  saveAgentConfig,
  setSkillsProvider,
  setSoulProvider,
  stampSessionMeta,
  updateAgentConfig,
  type AgentConfig,
  type InboundPayload,
  type InboundResult,
} from '../core/index.js'
import { installActivationSkillSync } from '../core/agent-level.js'
import { decideInboundDelivery, normalizeMessageId, threadRoot, type WorkspaceRef } from '../runtime/index.js'
import { resolveDshSkillRoots } from './skills.js'
import type { MailService } from './mail-service.js'
/** The resolved target of one inbound payload (binding + routed address). */
export interface InboundTarget {
  cfg: AgentConfig
  agentAddr: string
}

/** Result of the delivery chain (routing → session placement → dispatch log). */
export interface InboundOutcome {
  status: string
  detail?: string | undefined
  /**
   * Did the mail reach a dsh session? `false` ⇒ the pulled delivery must NOT be
   * acked (it re-pulls next round).
   */
  ok: boolean
}

/** The dsh agent service surface this layer needs (ctx.agents). */
export interface AgentsService {
  get(id: unknown): Agent | undefined
  resume(opts: unknown): Promise<{ agent: Agent }>
  create(opts: {
    sessionId: string
    meta?: { cwd?: string }
    agentOptions?: unknown
  }): Promise<{ agent: Agent; dispose(): Promise<void> }>
}

/**
 * Everything the caller already knows and this layer must not re-derive.
 *
 * `mail-hub` resolves the real workspace (realpath of the binding's workspace) and owns the agents
 * service, so passing them in keeps ONE anchor source of truth instead of two guesses.
 */
export interface DeliveryEnv {
  workspace?: WorkspaceRef
  agents?: AgentsService
  agentOptions?: unknown
  /** Host liveness probe for a recorded session (mail-hub: session cwd == this workspace). */
  sessionExists?: (sessionId: string) => Promise<boolean>
  /** Disable creating a fresh session (used when the host cannot allocate). */
  allowCreate?: boolean
}

/**
 * Recipient routing — the ONE routing step for pulled mail.
 *
 * The per-delivery address is authoritative; `payload.to` is the FILTERED full list (external
 * recipients first), so `to[0]` is often an external address. Prefer the routing header.
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

/** Ack rule for this layer: only a delivery that reached a session may be acked. */
export function toInboundResult(out: InboundOutcome): InboundResult {
  if (out.detail === undefined) {
    return out.ok ? { status: 'accepted' } : { status: 'rejected' }
  }
  return out.ok
    ? { status: 'accepted', detail: out.detail }
    : { status: 'rejected', detail: out.detail }
}

/** One inbound mail, end to end. Every path in this host goes through here. */
export async function deliverInbound(
  ctx: Context,
  target: InboundTarget,
  payload: InboundPayload,
  headers: Record<string, unknown>,
  env: DeliveryEnv = {},
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

  const found = env.agents ?? (ctx.get('agents') as AgentsService | undefined)
  if (found === undefined) {
    return { status: 'no_agents_service', detail: 'dsh-agent not mounted', ok: false }
  }
  // Bound once: the closure helpers below use it and the compiler cannot narrow through them.
  const agents: AgentsService = found
  // Model route: the deployment's default selection (base bundle's `agent-default-model` row).
  // Without it the turn dies with "no provider/model".
  const agentOptions =
    env.agentOptions ??
    (ctx.get('agentDefaultModel') as { currentSelection(): unknown } | undefined)?.currentSelection()
  const message = createUserMessage({
    content: [{ type: 'text', text: JSON.stringify({ ...result, to: agentAddr }) }],
    source: { kind: 'user' },
  })
  const root = normalizeMessageId(threadRoot(payload as never, headers))
  const workspace: WorkspaceRef = env.workspace ?? {
    id: String(cfg.workspace_id ?? '').trim() || process.cwd(),
    path: String(cfg.workspace_path ?? '').trim() || process.cwd(),
  }
  const route = await decideInboundDelivery({
    email: cfg.email,
    payload: payload as never,
    headers,
    workspace,
    ...(env.sessionExists ? { sessionExists: env.sessionExists } : {}),
  })
  // §16 C16 — the routing layer refuses a recorded session that lives in ANOTHER anchor and
  // re-ranks inside this one; `route.anchorMoved` is carried into the dispatch line below so ops
  // can tell "no direct reply existed" from "the direct reply was dropped (foreign anchor)".
  if (route.kind === 'direct' || route.kind === 'followup') {
    const delivered = await deliverTo(route.sessionId)
    if (delivered) return delivered
    // The recorded session is not there any more (deleted, or persistence is off). This attempt
    // IS the existence probe. §9.3 wants the redirect recorded.
    return await deliverFresh(`session-gone: ${route.sessionId} not resumable`)
  }
  if (route.kind === 'gone') {
    return await deliverFresh(`session-gone: ${route.previousSessionId} not resumable`)
  }
  if (env.allowCreate === false) {
    // The host cannot allocate a session (§11 creation is a preference, §16 C8 proof scope): the mail
    // is NOT delivered and must stay pending rather than be acked into a void.
    const out: InboundOutcome = {
      status: 'no_session_available',
      detail: 'no recorded session and session creation is disabled for this host',
      ok: false,
    }
    await logDispatch(out, 'no-session-available')
    return out
  }
  return await deliverFresh()

  /** Live session ⇒ followup; cold but resumable ⇒ resume; otherwise `undefined` (caller redirects). */
  async function deliverTo(sessionId: string): Promise<InboundOutcome | undefined> {
    const live = agents.get(sessionId)
    if (live) {
      live.followup(message)
      await stampThread(sessionId)
      const out: InboundOutcome = { status: 'delivered', detail: 'followup queued', ok: true }
      await logDispatch(out, 'thread-session', sessionId)
      return out
    }
    try {
      const handle = await agents.resume({ resumeSessionId: sessionId, agentOptions })
      handle.agent.followup(message)
      await stampThread(sessionId)
      const out: InboundOutcome = { status: 'resumed', detail: 'cold session resumed + followup queued', ok: true }
      await logDispatch(out, 'resumed', sessionId)
      return out
    } catch {
      return undefined
    }
  }

  /**
   * Re-point the thread at the session that actually took the mail (contract §9.4).
   *
   * This is our bookkeeping, not a delivery receipt: it records WHERE we put the mail so the next
   * mail of the thread resolves to the same session. It must never be read as proof that the host
   * stored it (that distinction is what keeps `unconfirmed` hosts honest).
   */
  async function stampThread(sessionId: string): Promise<void> {
    const patch = { sessionId, ...(workspace.path ? { workspacePath: workspace.path } : {}) }
    // §9.4: the THREAD pointer is the routing key and lives on the thread root.
    if (root) await stampSessionMeta(cfg.email, root, patch).catch(() => {})
    // …and per-message provenance (the shipped dsh behaviour): the same session is recorded on the
    // message that was just delivered, so a per-message lookup keeps working. It is NOT a receipt:
    // nothing here proves the host stored the mail.
    const messageId = normalizeMessageId(String(payload.message_id ?? ''))
    if (messageId && messageId !== root) {
      await stampSessionMeta(cfg.email, messageId, patch).catch(() => {})
    }
  }

  /** One dispatch line per delivery: `reason` is what the s7/s8 evidence greps for. */
  async function logDispatch(
    out: InboundOutcome,
    reason: string,
    sessionId?: string,
  ): Promise<void> {
    try {
      await logAimailDispatch(cfg.email, out.status, out.detail ?? '', agentAddr, {
        reason,
        ...(sessionId ? { sessionId } : {}),
        ...(root ? { threadRoot: root } : {}),
        workspacePath: workspace.path,
        ...(route.anchorMoved
          ? {
              anchor: 'anchor-moved',
              anchorMovedEdge: route.anchorMoved.edge,
              anchorMovedFrom: route.anchorMoved.space,
            }
          : {}),
      })
    } catch (e) {
      // Never fail a delivery because bookkeeping failed — but never hide it either: the s7/s8
      // evidence greps this file, so a silent write failure turns "no reason" into "no line".
      console.error(`[aimail] dispatch log write failed for ${cfg.email}: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  /** Fresh disposable session in the bound workspace (also the C17 redirect target). */
  async function deliverFresh(note?: string): Promise<InboundOutcome> {
    const sessionId = randomUUID()
    try {
      // Bind this session into the agent's config so the mail tools can resolve credentials
      // (resolveBySessionId matches the binding's session_id). Unbind again once the turn settles —
      // but only if the binding is still OURS (a concurrent email may have re-bound).
      await updateAgentConfig(cfg.system_id, cfg.email, { session_id: sessionId })
      const handle = await agents.create({
        sessionId,
        meta: { cwd: workspace.path },
        agentOptions,
      })
      handle.agent.followup(message)
      // Unbind once the turn settles, but only if the binding is still OURS. `whenIdle` is optional
      // on purpose: a host (or a test double) may not expose it, and that must never fail a delivery.
      const agent = handle.agent
      if (typeof agent.whenIdle === 'function') {
        void agent
          .whenIdle()
          .then(async () => {
            const cur = await loadAgentConfig(cfg.system_id, cfg.email)
            if (cur && cur.session_id === sessionId) {
              const { session_id: _drop, ...rest } = cur
              await saveAgentConfig(rest as typeof cur, cfg.system_id)
            }
          })
          .then(() => handle.dispose())
          .catch(() => {})
      }
      await stampThread(sessionId)
      const out: InboundOutcome = {
        status: note ? 'session_gone_redirected' : 'delivered',
        detail: `fresh session ${sessionId}${note ? ` — ${note}` : ''}`,
        ok: true,
      }
      // QA T2-4: the downgrade must be readable from the dispatch log, not only from the outcome
      // object — the s7 driver greps it.
      await logDispatch(out, note ? 'session-gone' : 'new-session', sessionId)
      return out
    } catch (e) {
      const failed: InboundOutcome = {
        status: 'session_create_failed',
        detail: e instanceof Error ? e.message : String(e),
        ok: false,
      }
      await logDispatch(failed, 'create-failed')
      return failed
    }
  }
}

/**
 * One pulled delivery, end to end — what `mail-hub` calls for every mail it drains.
 *
 * Throws when the chain did not deliver, because the shared pull client acks everything that returns
 * normally — a swallowed failure would ack a mail nobody accepted.
 */
export async function deliverPulled(
  ctx: Context,
  mail: MailService,
  cfg: AgentConfig,
  pulled: { id: number | string; email: string; headers?: Record<string, unknown>; body?: unknown },
  env: DeliveryEnv = {},
): Promise<InboundOutcome> {
  if (typeof pulled.body !== 'object' || pulled.body === null) {
    throw new Error(`pulled delivery ${pulled.id} for ${cfg.email} carries no JSON payload — not acking`)
  }
  // The delivery's own address is authoritative for routing (it is what the agent-scope key was
  // verified against server-side).
  const headers: Record<string, unknown> = {
    ...(pulled.headers ?? {}),
    'x-aimail-email': pulled.email,
  }
  const target = await resolveInboundTarget(mail, pulled.body as InboundPayload, headers)
  if (!target) {
    throw new Error(`pulled delivery ${pulled.id}: no binding for ${pulled.email} — not acking`)
  }
  const out = await deliverInbound(ctx, target, pulled.body as InboundPayload, headers, env)
  if (!out.ok) {
    throw new Error(
      `pulled delivery ${pulled.id}: inbound chain did not deliver (${out.status}: ${out.detail ?? ''}) — not acking`,
    )
  }
  return out
}

/**
 * Provider wiring that used to live inside the unmounted HTTP plugin (`apply`).
 *
 * On a shipped dsh these were never installed: `cordis.patch.yml` mounts `mail-service`, `tools` and
 * `mail-hub` only, so the whole `inbound` module — listener, pull loop AND these providers — was
 * dead. Moving them here makes the shipped path render SOUL and enumerate skills as designed.
 */
export function installDshInboundProviders(): void {
  const dshHome = process.env.DSH_HOME?.trim() || path.join(os.homedir(), '.dsh')
  const profile = process.env.DSH_PROFILE?.trim() || 'web'
  setSoulProvider(() => {
    const p = path.join(dshHome, 'profiles', profile, 'SOUL.md')
    try {
      return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''
    } catch {
      return ''
    }
  })
  // dsh skills: report every skill THIS profile's configuration can load (owner ruling 2026-10-23 —
  // shared roots included, not just per-agent ones). Root set + rank order mirror dsh's
  // FileSystemSkillProvider; names via the shared enumerator.
  setSkillsProvider(() => combineSkillNames(resolveDshSkillRoots()))
  // SDK-shipped activation skill → <dshHome>/skills/agentmail/ (idempotent; non-fatal: a missing
  // package resource is a packaging defect and must not block inbound handling).
  try {
    const skillDst = path.join(dshHome, 'skills', 'agentmail')
    fs.mkdirSync(skillDst, { recursive: true })
    const skillSrc = path.join(packageResourcesRoot(), 'skills')
    for (const f of ['SKILL.md', 'DESCRIPTION.md']) {
      const from = path.join(skillSrc, f)
      const to = path.join(skillDst, f)
      if (!fs.existsSync(from)) {
        console.error(
          `[aimail] skill resource missing: ${from} ` +
            '(repo: stage it via the package prepack; installed: reinstall the package)',
        )
        continue
      }
      if (fs.existsSync(to) && fs.readFileSync(from).equals(fs.readFileSync(to))) continue
      fs.copyFileSync(from, to)
    }
    installActivationSkillSync(path.join(dshHome, 'skills'))
  } catch {
    /* non-fatal; retried on next plugin start */
  }
}
