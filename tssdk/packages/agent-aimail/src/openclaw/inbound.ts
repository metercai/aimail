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
import * as fs from 'node:fs'
import * as path from 'node:path'
import {
  processInboundMail,
  routeAddressFromHeaders,
  logAimailDispatch,
  startAgentPullEntries,
  loadAgentConfig,
  listAgentConfigs,
  listSystemDirs,
  startSystemPull,
  resolveAgentPullSettings,
  startAgentSystemGroups,
  freshDeliveryTarget,
  countActiveAgentSystemBindings,
  AGENT_SCOPE_SYSTEM_PREFIX,
  type AgentConfig,
  type AgentPullHandle,
  type AgentPullOverrides,
  type InboundPayload,
  type RequestClient,
  readLocalMeta,
  stampSessionMeta,
} from '../core/index.js'
import { parentMessageId, decideInboundDelivery } from '../runtime/index.js'
import type { WorkspaceRef } from '../runtime/types.js'
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry'
import { resolveByRecipient } from '../mail/index.js'
import { getAgentSystemId } from '../core/agent-system-id.js'
import { threadRoot } from '../runtime/session-router.js'
import { createHash } from 'node:crypto'
import {
  configuredAgentIds,
  gatewayPort,
  hooksPath,
  readPointer,
  resolveAgentId,
} from './identity.js'

/**
 * Host-side dedupe key for one dispatch.
 *
 * openclaw keys its replay cache on the dispatch scope, which includes the target session — so
 * a key that ignores the target would make a retry *after the target changed* (e.g. the recorded
 * session was deleted and we re-route) return the cached 200 **without running anything**: the
 * pull loop then acks a mail that never landed. The target is part of the key for that reason.
 */
export function dispatchIdempotencyKey(messageId: string, sessionKey: string | undefined): string {
  const mid = String(messageId ?? '').trim()
  if (!mid) return ''
  return `${mid}|${sessionKey ? `session:${sessionKey}` : 'isolated'}`.slice(0, 128)
}

/** Hooks policy the session-key decision needs (matches `readHooksConfig`). */
export interface HookSessionPolicy {
  allowSessionKey: boolean
  prefixes: string[]
}

/**
 * NOTE (owner 2026-11-27): routing is NOT decided here any more. This adapter used to keep
 * `selectSessionKey()` — a local E1/E2 heuristic that never compared anchors, so a reply
 * whose parent session lived in another anchor was dispatched into it (§9.2 I2 violation)
 * and `anchor-moved` could never be recorded. The decision now comes from the shared
 * `runtime/routing.ts decideInboundDelivery()`; this module only MAPPES the resulting
 * route onto the host handle (`session_key`). Do not reintroduce a local ranking.
 * The old behaviour (recorded handle wins over a thread guess) is covered by
 * test/core/runtime-routing.test.ts, which owns the edge ranking for every host.
 */

/**
 * The agent id used for BOTH the session key and the dispatch body.
 *
 * `resolveDispatchAgentId` returns `''` when the host config names no agent (its own test ④
 * pins that "do not guess"). Passing that empty value into the session key produced
 * `agent::aimail:<hash>` — a key for an agent that does not exist — while the dispatch body
 * was silently defaulted to `main`, so the key and the run disagreed. One resolution, used
 * twice, is what keeps them equal.
 */
export function effectiveDispatchAgentId(agentId: string | undefined): string {
  const given = String(agentId ?? '').trim()
  return given || resolveAgentId() || 'main'
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
  opts: {
    agentId: string
    message: string
    sessionKey?: string
    /**
     * Ask the host to REUSE the resolved session across runs (`sessionMode: "persistent"`).
     * Without it the host defaults to `isolated` — a fresh run session per call — so a
     * caller-supplied key alone does NOT bring a reply back to the session that sent the mail
     * (contract §9 E1). Only meaningful with `sessionKey`: the host answers 400 otherwise.
     */
    persist?: boolean
    /** Host-side dedupe so a retried pull round does not run the same mail twice. */
    idempotencyKey?: string
  },
): Promise<{ status: string; detail: string }> {
  void api
  const hooksToken = readHooksToken()
  const agentId = effectiveDispatchAgentId(opts.agentId)
  const r = await fetch(`http://127.0.0.1:${gatewayPort()}${hooksPath()}/agent`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      ...(hooksToken ? { Authorization: 'Bearer ' + hooksToken } : {}),
    },
    body: JSON.stringify({
      message: opts.message,
      name: 'aimail',
      // S1b (2026-09-27): Must explicitly provide agentId —— the host documentation's Hook agent payload specifies
      // "agentId ... Must name a configured agent"、"Required when no implicit/retained
      // owner can be resolved", not providing it results in 400 (tested: endpoint exists, auth passed, still 400).
      agentId,
      ...(opts.sessionKey ? { sessionKey: opts.sessionKey } : {}),
      // Session reuse (contract §9 E1): the host's `sessionMode` defaults to `isolated` — a fresh
      // run session per call, measured on openclaw 2026.11.5 (`sessionTarget: isolated`,
      // gateway/hooks.ts). Carrying the key is therefore NOT enough for a reply to land back in
      // its own session; the caller must also ask for `persistent`. The host requires a key with
      // it (400 "sessionKey is required when sessionMode is persistent"), hence both are
      // conditional on the key.
      ...(opts.sessionKey && opts.persist ? { sessionMode: 'persistent' } : {}),
      ...(opts.idempotencyKey ? { idempotencyKey: opts.idempotencyKey } : {}),
      // S1e (2026-09-27): **Do not send caller sessionKey** unless the host explicitly enables
      // hooks.allowRequestSessionKey (checked at the call site). Direct /agent calls with the
      // caller's own key require allowRequestSessionKey + a prefix whitelist ("Hook session and
      // agent policy"); otherwise the host answers 400.
      deliver: false,
    }),
  })
  if (!r.ok) {
    // S1d (2026-09-27): Host documentation explicitly requires "Read the `error` before retrying" ——
    // only logging status code loses "why it was rejected" (404/401/400 all suffered this loss in three rounds).
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

function readHooksConfig(): { token: string; allowSessionKey: boolean; prefixes: string[] } {
  try {
    const home = process.env.HOME ?? process.env.USERPROFILE ?? ''
    const raw = fs.readFileSync(path.join(home, '.openclaw', 'openclaw.json'), 'utf-8')
    const cfg = JSON.parse(raw) as {
      hooks?: { token?: string; allowRequestSessionKey?: boolean; allowedSessionKeyPrefixes?: string[] }
    }
    return {
      token: String(cfg.hooks?.token ?? ''),
      allowSessionKey: cfg.hooks?.allowRequestSessionKey === true,
      prefixes: Array.isArray(cfg.hooks?.allowedSessionKeyPrefixes)
        ? cfg.hooks!.allowedSessionKeyPrefixes!.filter((p): p is string => typeof p === 'string')
        : [],
    }
  } catch {
    return { token: '', allowSessionKey: false, prefixes: [] }
  }
}

function threadSessionKey(agentId: string, payload: InboundPayload, headers: Record<string, unknown>): string {
  const root = threadRoot(payload as never, headers) || String(payload.message_id ?? '')
  return `agent:${agentId}:aimail:${createHash('sha256').update(root).digest('hex').slice(0, 24)}`
}

function readHooksToken(): string {
  return readHooksConfig().token
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
  return cfg ? { cfg, agentAddr } : undefined
}

/**
 * Dispatch target for one inbound delivery — the ONE place a binding's
 * identity is translated into the host's agent namespace.
 *
 * System/bridge bindings (push-served) carry the HOST agent id in `agent_id`:
 * `openclaw aimail register` writes it from the host's own identity, so it
 * stays authoritative and the push path keeps exactly the value it always had.
 *
 * Address-level bindings do NOT: `shared_addr_*` only exists because an
 * activation CODE was consumed, and there `agent_id` is written from the
 * ADDRESS's local part (mail-core address-code.ts) — an ADDRESS-level value,
 * not a host agent. Feeding it to the host anyway is what made the address-code
 * flow red on 0.1.25 (E2-pull/openclaw): the mail WAS polled and taken, but
 * `/hooks/agent` answered `400 unknown agentId "agenta"`, `deliverInbound`
 * returned `ok:false`, and the delivery was deliberately not acked — re-pulled
 * every round and never reaching an agent turn.
 *
 * So address-level bindings resolve here, at the address level: the delivery's
 * own address local part when the host really has such an agent, else the
 * host's declared/default agent. Never a local part the host does not know (a
 * rejected dispatch stays a non-ack, never a silent drop).
 * Exported for the L1 pin (test/dispatch-target.test.ts).
 */
export function resolveDispatchAgentId(cfg: AgentConfig, agentAddr: string): string {
  const fromBinding = (cfg.agent_id ?? '').trim()
  // Same fact mail-core's pull gate uses (poll-entry.ts isAgentScopeBinding):
  // `shared_addr_*` ⇔ activation-code product ⇔ address-scope binding.
  // V2 fact: `agent_system_id` marks an address-level (aggregation) binding.
  // V1 kept the `shared_addr_*` prefix as the same signal; accept both so an
  // upgrade does not change dispatch behavior.
  const addressScope = Boolean(cfg.agent_system_id)
    || (!!cfg.system_id && cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX))
  if (!addressScope) return fromBinding
  const addr = String(agentAddr ?? '')
  const local = (addr.includes('@') ? addr.split('@')[0] ?? '' : addr).trim()
  if (local && configuredAgentIds().includes(local)) return local
  return resolveAgentId()
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
  // Dispatch target: `resolveDispatchAgentId` translates the binding into the
  // host's agent namespace (address-level bindings must not hand the host an
  // address local part — the 0.1.25 E2-pull defect). One rule, both entries.
  const agentId = resolveDispatchAgentId(cfg, agentAddr)
  // The key must name the agent the dispatch will actually run under (P1-1: an empty id made
  // the key `agent::aimail:…` while the body fell back to `main`).
  const keyAgentId = effectiveDispatchAgentId(agentId)
  const policy = readHooksConfig()
  // §9.2 I1 first: when the mail answers one WE sent, it must land in the session that sent it.
  // The parent's local meta carries that session's routable handle (`session_key`, stamped by
  // send_mail); an unknown parent is not a direct reply (never inferred from the reference chain).
  const parent = parentMessageId(payload as never, headers)
  const parentMeta = parent ? await readLocalMeta(cfg.email, parent) : undefined
  // ONE decision for every host: runtime/routing.ts `decideInboundDelivery()` (E1/E2/E3 + C16/I2).
  // This adapter used to keep its own `selectSessionKey()`, which only looked at the recorded
  // session_key and never compared anchors — so a reply whose parent session lived in ANOTHER
  // anchor was dispatched straight into it (contract §9.2 I2 forbids exactly that), and the
  // `anchor-moved` bookkeeping could never appear (L2 S8: "no marker", "edge='-'").
  const workspace: WorkspaceRef = {
    id: String(cfg.workspace_id ?? '').trim() || String(cfg.workspace_path ?? '').trim(),
    path: String(cfg.workspace_path ?? '').trim(),
    ...(cfg.workspace_title ? { title: String(cfg.workspace_title) } : {}),
  }
  const route = await decideInboundDelivery({
    email: cfg.email,
    payload: payload as never,
    headers,
    workspace,
  })
  const derivedKey = threadSessionKey(keyAgentId, payload as never, headers)
  const recordedKey = parentMeta?.session_key ? String(parentMeta.session_key).trim() : ''
  // The host's routable handle for a recorded session is its session_key. After the anchor check a
  // foreign session is no longer proposed at all (route becomes 'new'/'gone' with anchorMoved), so
  // falling back to the thread-derived key is the correct re-rank inside the CURRENT anchor.
  const sessionKey =
    route.kind === 'direct' || route.kind === 'followup'
      ? recordedKey || derivedKey
      : derivedKey
  const decision = {
    sessionKey,
    source: route.kind === 'direct' ? 'direct-reply' : 'thread',
    ...(route.kind === 'direct' && !recordedKey ? { dropped: 'sessionKey-missing-in-meta' } : {}),
  } as const
  const requestKey = policy.allowSessionKey
    && (policy.prefixes.length === 0 || policy.prefixes.some((p) => sessionKey.startsWith(p)))
  const idempotencyKey = dispatchIdempotencyKey(
    String(payload.message_id ?? ''),
    requestKey ? sessionKey : undefined,
  )
  const out = await deliverToAgent(api, {
    agentId,
    message: JSON.stringify({ ...result, to: agentAddr }),
    // The key locates the session; `persist` is what makes the host REUSE it instead of
    // opening a fresh isolated run (C8 / §9 E1). Both travel only when the host opted in.
    ...(requestKey ? { sessionKey, persist: true } : {}),
    // A pull retry must not run the same mail twice — but the key includes the target, so a retry
    // after the target changed is NOT served from the host's replay cache (see
    // dispatchIdempotencyKey: that cache hit was an ack with zero landing).
    ...(idempotencyKey ? { idempotencyKey } : {}),
  })
  // E3-① (owner ruling 2026-09-27): record the dispatch outcome in the SAME per-agent
  // log the inbound line goes to. Measured on the CLI gate's J4e: without this line a
  // refused/suppressed dispatch was invisible — the log showed only {"event":"inbound"}
  // and nothing told the operator that no agent ever took the mail.
  await logAimailDispatch(cfg.email, out.status, out.detail, agentAddr, {
    // The EFFECTIVE id, i.e. the one the dispatch body actually carried. Logging the raw value here
    // made the log disagree with the request it described (`agentId: ""` next to a key naming
    // `main`) — QA flagged it; both facts are useful, so keep the raw one under its own name.
    agentId: keyAgentId,
    dispatchAgentIdRaw: agentId,
    // QA P1-1: say WHERE the id came from — a binding-derived id and the default-agent fallback
    // look identical in the log otherwise.
    resolvedFrom: agentId ? 'binding' : 'default',
    sessionKey,
    sessionKeyRequested: requestKey,
    // Evidence for C8: a reply can only return to its session when the host reused it.
    sessionModeRequested: requestKey ? 'persistent' : 'isolated',
    // Which edge chose the session: E1 (direct reply to a mail we sent) or E2 (thread lineage).
    sessionKeySource: decision.source,
    // §9.2 I2 / §9.3: the routing layer abandons an edge whose target lives in ANOTHER anchor and
    // re-ranks here; the abandonment MUST be readable by ops and MUST name the edge + foreign anchor.
    ...(route.kind === 'direct' || route.kind === 'followup' || route.kind === 'new' || route.kind === 'gone'
      ? route.anchorMoved
        ? {
            anchor: 'anchor-moved',
            anchorMovedEdge: route.anchorMoved.edge,
            anchorMovedFrom: route.anchorMoved.space,
          }
        : {}
      : {}),
    ...(decision.dropped ? { directReplyDropped: decision.dropped } : {}),
    // Contract §16 C17 证明范围 / 附 E: this host's 200 means "queued", not "landed". Recording it
    // keeps the ack honest — nothing here may be read as proof that the turn ran.
    deliveryConfirmation: 'admission-only',
    bindingAgentId: cfg.agent_id ?? '',
    bindingSystemId: cfg.system_id,
    agentSystemId: cfg.agent_system_id ?? '',
  })
  if (out.status === 'delivered' && String(payload.message_id ?? '').trim()) {
    // Remember where this mail landed: the next reply to it is a direct reply (E1), not a
    // thread guess. Best-effort — a meta write must never fail a delivery.
    await stampSessionMeta(cfg.email, String(payload.message_id), {
      ...(requestKey ? { sessionKey } : {}),
    }).catch(() => {})
  }
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
/**
 * Shared pulled-delivery chain (tier-1/tier-2/no-pointer all funnel here):
 * body shape check → recipient routing → inbound chain. A non-JSON body is a
 * MALFORMED delivery (a real error: throw ⇒ un-acked ⇒ re-pull); a missing
 * target or a failed inbound chain is likewise not-acked (re-pulled) — never
 * silently dropped. The delivery-level skip (paused/unknown) is NOT here: it
 * is the caller's typed-result concern (P1-A), decided from a FRESH binding
 * lookup before this chain runs.
 */
async function deliverPulled(
  api: OpenClawPluginApi,
  mail: { id: number; email: string; headers: Record<string, unknown>; body: unknown },
): Promise<void> {
  if (typeof mail.body !== 'object' || mail.body === null) {
    throw new Error(`pulled delivery ${mail.id} for ${mail.email} carries no JSON payload — not acking`)
  }
  const headers: Record<string, unknown> = {
    ...(mail.headers ?? {}),
    'x-aimail-email': mail.email,
  }
  const target = await resolveInboundTarget(mail.body as InboundPayload, headers)
  if (!target) {
    throw new Error(`pulled delivery ${mail.id}: no binding for ${mail.email} — not acking`)
  }
  const out = await deliverInbound(api, target, mail.body as InboundPayload, headers)
  if (!out.ok) {
    throw new Error(
      `pulled delivery ${mail.id}: inbound chain did not deliver (${out.status}: ${out.detail ?? ''}) — not acking`,
    )
  }
}

/**
 * Pull entry (agent-scope bindings only) — the missing production wire for the
 * address-code flow: an address activated by an activation code has NO push
 * path (the gateway stores `webhook_url = NULL` for it), so the adapter must
 * fetch its own mail on a timer. Pulled mail enters `deliverInbound`, i.e. the
 * very same chain pushed mail takes.
 *
 * Tier decision (V2 contract §9):
 *   - system-scope (admin_key) pull was RETIRED 2026-11-26: one transport, the ASID aggregate;
 *   - pointer binding present ⇒ tier-2 AgentSystemId aggregate — with the
 *     §6.4 decision tree (P0-6/P0-7): disabled ⇒ 0 stream; paused ⇒ active
 *     sibling pool (0 stream if empty, else the group aggregate, pointer
 *     excluded); active ⇒ the existing tier-2 stream with settings + the
 *     delivery-level fresh lookup;
 *   - else ⇒ no-pointer fallback: startAgentPullEntries (branch guard: V2
 *     aggregate groups / V1 per-binding, plan §6.5).
 */
async function startInboundPullInner(
  api: OpenClawPluginApi,
  opts: {
    log?: (line: string) => void
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
    /** Test seam: build the aggregate/per-binding client (real path unchanged). */
    clientFor?: (rep: AgentConfig) => RequestClient
  } = {},
): Promise<AgentPullHandle[]> {
  const ptr = await readPointer()
  // B2 (no system): one AgentSystemId aggregate stream (tier-2 / pointer).
  // The pointer is a DISCOVERY entry, not an agent switch (P0-7): a paused
  // pointer address must NOT starve the other active addresses of the same
  // (AgentSystemId, Gateway) — they keep exactly ONE aggregate stream (rep =
  // an active sibling), and the pointer's own mail is skipped at delivery
  // (fresh lookup ⇒ skip('paused'), left pending until resume).
  const ptrCfg = ptr.system_id && ptr.email
    ? await loadAgentConfig(ptr.system_id, ptr.email).catch(() => undefined)
    : undefined
  if (ptrCfg?.api_key) {
    const env = opts.env ?? process.env
    const settings = resolveAgentPullSettings(ptrCfg, env, opts.overrides ?? {})
    if (!settings.enabled) {
      // Global switch (AIMAIL_PULL=0 / disabled): one decision for the whole
      // profile — tier-2, grouping and per-binding all honor the same env.
      opts.log?.(
        `[aimail-pull] pointer agent:${ptrCfg.email} pull disabled by config ` +
          `(${settings.reason}) — no stream`,
      )
      return []
    }
    if (ptrCfg.paused === true) {
      // P0-7: pause the address, not the AgentSystemId. Scan the active
      // sibling pool of this (AgentSystemId, Gateway) and keep ONE aggregate
      // stream for it (the pointer's own mail is skipped at delivery level).
      const asid = ptrCfg.agent_system_id ?? (await getAgentSystemId())
      const rawPool: AgentConfig[] = []
      const sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
      for (const s of sids) {
        for (const c of await listAgentConfigs(s)) {
          const isSelf =
            c.system_id === ptrCfg.system_id &&
            (c.email ?? '').toLowerCase() === (ptrCfg.email ?? '').toLowerCase()
          if (isSelf) continue
          if (c.paused === true || !c.api_key) continue
          if (c.agent_system_id !== asid) continue
          if (c.gateway_url !== ptrCfg.gateway_url) continue
          rawPool.push(c)
        }
      }
      const poolSeen = new Set<string>()
      const pool: AgentConfig[] = []
      for (const c of rawPool) {
        const e = (c.email ?? '').toLowerCase()
        if (!e || poolSeen.has(e)) continue
        poolSeen.add(e)
        pool.push(c)
      }
      if (pool.length === 0) {
        opts.log?.(
          `[aimail-pull] pointer agent:${ptrCfg.email} paused, no active ` +
            'sibling — no stream (resume re-arms in-process)',
        )
        return []
      }
      const handles = await startAgentSystemGroups(pool, {
        onEmail: (_cfg, mail) => deliverPulled(api, mail),
        ...(opts.log ? { log: opts.log } : {}),
        ...(opts.env ? { env: opts.env } : {}),
        ...(opts.overrides ? { overrides: opts.overrides } : {}),
        ...(opts.clientFor ? { clientFor: opts.clientFor } : {}),
      })
      opts.log?.(
        `[aimail-pull] pointer agent:${ptrCfg.email} paused — aggregate via ` +
          `agent:${asid} gw=${ptrCfg.gateway_url} ` +
          `members=[${pool.map((m) => m.email).join(', ')}] (pointer excluded)`,
      )
      return handles
    }
    // Normal branch: the pointer address is active — the existing tier-2
    // aggregate stream (rep = pointer), now wired with the resolved settings
    // (P0-5/P1-C) and the delivery-level fresh lookup (P0-6).
    const h = await startSystemPull({
      systemId: ptrCfg.email,
      agentSystemId: ptrCfg.agent_system_id ?? (await getAgentSystemId()),
      gatewayUrl: ptrCfg.gateway_url,
      apiKey: ptrCfg.api_key,
      ...(opts.log ? { log: opts.log } : {}),
      ...(opts.clientFor ? { client: opts.clientFor(ptrCfg) } : {}),
      intervalMs: settings.intervalMs,
      limit: settings.limit,
      mode: settings.mode,
      onEmail: async (mail) => {
        // Delivery-level fresh lookup (P0-6): a runtime pause/archive takes
        // effect on the very next delivery — paused ⇒ skip('paused'),
        // unknown ⇒ skip('unknown') (typed result, not a throw).
        const { skip } = await freshDeliveryTarget(mail.email)
        if (skip) return skip
        await deliverPulled(api, mail)
      },
    })
    return [{
      email: `agent:${ptrCfg.email}`, started: true, reason: 'enabled',
      intervalMs: settings.intervalMs, limit: settings.limit,
      stop: h.stop, done: h.done as Promise<import('../core/index.js').PollStats>,
      stats: () => h.stats(),
    }]
  }
  return await startAgentPullEntries({
    systemId: opts.systemId ?? ptr.system_id ?? '',
    ...(opts.log ? { log: opts.log } : {}),
    ...(opts.env ? { env: opts.env } : {}),
    ...(opts.overrides ? { overrides: opts.overrides } : {}),
    onEmail: (_cfg, mail) => deliverPulled(api, mail),
  })
}

/**
 * Public pull entry: emit the standardized reconcile line L2 S2/S4 read.
 * `bindings` counts this AgentSystemId's active V2 bindings (paused/keyless
 * excluded), so a two-address host reports `from 2 binding(s)` while still
 * running one aggregate stream.
 */
export async function startInboundPull(
  api: OpenClawPluginApi,
  opts: {
    log?: (line: string) => void
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
    clientFor?: (rep: AgentConfig) => RequestClient
  } = {},
): Promise<AgentPullHandle[]> {
  const handles = await startInboundPullInner(api, opts)
  if (handles.length > 0) {
    const asid = await getAgentSystemId()
    const bindings = await countActiveAgentSystemBindings(asid)
    opts.log?.(
      `[aimail-pull] SSE reconcile: ${handles.length} gateway stream(s) from ${bindings} binding(s)`,
    )
  }
  return handles
}


// No HTTP inbound handler: the in-gateway push route was retired 2026-11-25 (SSE/pull only).
// Mail arrives through `startInboundPull` above; there is no endpoint to expose and no HMAC step.
