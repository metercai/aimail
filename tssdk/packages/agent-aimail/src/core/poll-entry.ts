/**
 * poll-entry.ts — agent-scope scheduled polling entry (auto-pull wiring, slice 8).
 *
 * The **last wire** that the adapter install/initialization wrap-up needs to connect: the agent-scope
 * key obtained from address-level activation cannot get push (the gateway writes `webhook_url = NULL`
 * for that address at redemption time),
 * so it can only pull on a schedule. Previously `pullList/pullAck/startPolling` had libraries and unit tests, **zero callers**,
 * The "agent autonomous access" promised in the documentation is missing this wire in production — this module is it.
 *
 * Semantics (same family as startPolling, no duplicate implementation):
 *   - **Only enabled for agent-level activation**: binding is a product of address code redemption ⇒ enabled; system/bridge scenarios
 *     (system registered by platform, goes through push) ⇒ not enabled. The criterion is an **existing fact**, not a guess:
 *     Address codes can only be redeemed under the `shared_addr_*` host system (a gateway-side guard
 *     rejects every other system_id at redemption)
 *     ⇒ binding `system_id` starts with that prefix ⇔ the key was redeemed from an address code (agent scope;
 *     the pull endpoint under agent scope only serves the key's own address).
 *     regen/product creation paths **never** produce that prefix (`products.rs:83` explicitly excludes it)
 *     ⇒ system/bridge bindings will not be misjudged.
 *   - **No ack on failure / idempotent deduplication**: guaranteed by `startPolling` (if onEmail throws an error or
 *     rejects ⇒ that delivery is not acked, re-pulled in the next round; dedup key = delivery id).
 *   - **Stoppable / configurable interval**: handle `stop()` (AbortController) + stops on process exit
 *     (timer unref); interval/batch/master switch via env or explicit override.
 *   - **No timer leaks**: One binding per loop, handle convergence at startup.
 */
import type { AgentConfig } from './types.js'
import { listAgentConfigs } from './config.js'
import { findBindingByEmail } from './address-code.js'
import { listSystemDirs } from './system-config.js'
import type { PollStats, PullDeliveryResult, RequestClient } from './address-code.js'
import { startSystemPull } from './system-pull.js'
import {
  bindingProvenance,
  isRefusedByGateway,
  refuseBindingAfterGatewayRejection,
} from './binding-provenance.js'
import { buildAgentSystemClient, isV2AgentScopeBinding } from './agent-system-client.js'
import { PULL_MODE_ENV, parsePullMode, type PullMode } from './pull-contract.js'
import { getAgentSystemId } from './agent-system-id.js'

/**
 * System id prefix for address-level activation artifacts — gateway-side fact (evidence in module header):
 * Only activation codes under `shared_addr_*` host systems can be redeemed, and successful redemption lands on that sid.
 */
export const AGENT_SCOPE_SYSTEM_PREFIX = 'shared_addr_'

/** Default polling interval (30s): email is not a real-time channel, but it must be faster than human patience. */
export const DEFAULT_PULL_INTERVAL_MS = 30_000

/** Default batch size per round (gateway limit 200, pullList converges itself). */
export const DEFAULT_PULL_LIMIT = 20

/** Lower bound 1s: denser is just hitting the gateway, no business reason. */
const MIN_PULL_INTERVAL_MS = 1_000

/** Master switch environment variable: 0/false/off/no (case-insensitive) ⇒ off. Default on. */
const PULL_ENABLE_ENV = 'AIMAIL_PULL'
const PULL_INTERVAL_ENV = 'AIMAIL_PULL_INTERVAL_MS'
const PULL_LIMIT_ENV = 'AIMAIL_PULL_LIMIT'

/** whether this binding is an address-level (agent-scope) activation artifact — decided by existing facts, not guessed.
 *
 * TWO predicates (V2 fix, B1):
 *   - V1: `shared_addr_*` host system ⇔ activation-code redemption artifact
 *     (gateway-side guard; evidence in module header);
 *   - V2: the binding carries an `agent_system_id` — an address activated under
 *     the V2 (AgentSystemId aggregation) contract. Its `system_id` is a plain
 *     `system-*` GatewaySystemId and will NEVER start with the V1 prefix, so
 *     the V1 predicate alone structurally rejected every V2 binding — the
 *     no-pointer pull never armed ("no agent-scope binding", QA B1).
 */
export function isAgentScopeBinding(cfg: AgentConfig | undefined | null): boolean {
  if (!cfg) return false
  if (!cfg.system_id || !cfg.api_key) return false
  return isV1AgentScopeBinding(cfg) || isV2AgentScopeBinding(cfg)
}

/** V1 predicate: activation-code host system (`shared_addr_*`). */
export function isV1AgentScopeBinding(cfg: AgentConfig | undefined | null): boolean {
  if (!cfg) return false
  return !!cfg.system_id && cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX)
}

// The aggregate client constructor and the V2 predicate live in `agent-system-client.ts`: one
// construction point for every aggregate pull (its header set — ASID + anchor claims — is the
// contract, not a local detail). Defect 1 (2026-11-25) was a second, hand-built aggregate client
// without the anchor header ⇒ 403 and zero delivery.
export { buildAgentSystemClient, isV2AgentScopeBinding }

/**
 * Count the ACTIVE V2 bindings this AgentSystemId owns (optionally on one
 * gateway). Hosts use this for the standardized reconcile log line
 * `SSE reconcile: <streams> gateway stream(s) from <bindings> binding(s)`
 * (L2 S2/S4 read this line; REQ-6 diagnostics). Paused addresses and
 * bindings without a key are not active; copied binding files are deduped by
 * address.
 */
export async function countActiveAgentSystemBindings(
  agentSystemId: string,
  gatewayUrl?: string,
): Promise<number> {
  const asid = String(agentSystemId ?? '').trim()
  if (!asid) return 0
  const wantGateway = (gatewayUrl ?? '').replace(/\/+$/, '')
  const seen = new Set<string>()
  for (const sid of await listSystemDirs()) {
    for (const c of await listAgentConfigs(sid)) {
      if (!isV2AgentScopeBinding(c) || c.agent_system_id !== asid) continue
      if (c.paused === true || !c.api_key) continue
      if (wantGateway && (c.gateway_url ?? '').replace(/\/+$/, '') !== wantGateway) continue
      const email = String(c.email ?? '').trim().toLowerCase()
      if (email) seen.add(email)
    }
  }
  return seen.size
}

/**
 * A' grouping (V2, plan §6.1/§6.2): the ONE production entry for
 * AgentSystemId aggregate pulls. Groups the given V2 agent-scope bindings
 * by (AgentSystemId, gateway_url) and starts ONE `startSystemPull` per group
 * (rep = the first ACTIVE binding in the group, sorted by address for
 * determinism). The gateway authorizes every address owned by the
 * AgentSystemId with any member's agent-scope key
 * (`require_agent_system_owner`, gateway http.rs) — so one stream per group,
 * never one per binding (that was the pre-V2 topology and the multi-stream
 * defect B1/B2 aimed at).
 *
 * Group-level settings (§6.2): `resolveAgentPullSettings(rep)` — a disabled
 * group (AIMAIL_PULL=0) starts NO stream + audit line; interval/limit/mode are
 * wired into the stream. All-paused groups are filtered out of the pool
 * upstream (or here) — a paused group must not arm.
 *
 * Delivery (§6.3, fresh lookup per delivery): a runtime pause/archive takes
 * effect on the very next delivery — paused ⇒ skip('paused'), unknown
 * target ⇒ skip('unknown') (no throw, no error, mail left pending).
 */
export interface StartAgentSystemGroupsOptions {
  onEmail: (cfg: AgentConfig, mail: PulledMail) => void | Promise<void | PullDeliveryResult>
  env?: NodeJS.ProcessEnv
  overrides?: AgentPullOverrides
  log?: (line: string) => void
  /** Test seam: build the aggregate client for a group (rep). */
  clientFor?: (rep: AgentConfig) => RequestClient
}

export async function startAgentSystemGroups(
  pool: readonly AgentConfig[],
  opts: StartAgentSystemGroupsOptions,
): Promise<AgentPullHandle[]> {
  const log = opts.log ?? ((line: string) => console.log(line))
  const env = opts.env ?? process.env
  // Dedup over copied files (same address can exist in several system dirs).
  const dedup = new Map<string, AgentConfig>()
  for (const c of pool) {
    const e = (c.email ?? '').trim().toLowerCase()
    if (e && !dedup.has(e)) dedup.set(e, c)
  }
  // Group by (AgentSystemId, gateway_url). V2 bindings always carry an
  // agent_system_id; defensive filter keeps a stray V1 out of the groups.
  const groups = new Map<string, AgentConfig[]>()
  for (const c of [...dedup.values()].sort((a, b) => (a.email ?? '').localeCompare(b.email ?? ''))) {
    if (!isV2AgentScopeBinding(c) || !c.api_key) continue
    const key = `${c.agent_system_id}\u0000${c.gateway_url}`
    const g = groups.get(key)
    if (g) g.push(c)
    else groups.set(key, [c])
  }
  const handles: AgentPullHandle[] = []
  for (const [key, members] of groups) {
    const asid = key.slice(0, key.indexOf('\u0000'))
    const active = members.filter((m) => m.paused !== true)
    if (active.length === 0) {
      log(
        `[aimail-pull] no pointer — agent:${asid} all members paused — ` +
          `no stream (members: ${members.map((m) => m.email).join(', ')})`,
      )
      continue
    }
    const rep = active[0]
    const settings = resolveAgentPullSettings(rep, env, opts.overrides ?? {})
    if (!settings.enabled) {
      log(`[aimail-pull] no pointer — agent:${asid} pull disabled by config (${settings.reason}) — no stream`)
      continue
    }
    const client = opts.clientFor
      ? opts.clientFor(rep)
      : buildAgentSystemClient(rep.gateway_url, rep.api_key, asid, rep.email, members)
    const pullHandle = await startSystemPull({
      systemId: rep.email,
      agentSystemId: asid,
      gatewayUrl: rep.gateway_url,
      apiKey: rep.api_key,
      ...(opts.log ? { log: opts.log } : {}),
      ...(opts.clientFor ? { client: client } : {}),
      intervalMs: settings.intervalMs,
      limit: settings.limit,
      mode: settings.mode,
      onEmail: async (mail) => {
        // Fresh per-delivery target (P0-6/P0-7): the arm-time pool is a
        // snapshot — a runtime pause/archive must take effect NOW.
        const { cfg: fresh, skip } = await freshDeliveryTarget(mail.email)
        if (skip) return skip
        await opts.onEmail(fresh ?? rep, {
          id: mail.id,
          email: mail.email,
          headers: mail.headers,
          body: mail.body,
        })
      },
    })
    const live: PollStats = { pulled: 0, acked: 0, errors: 0 }
    void pullHandle.done.then((stats: unknown) => {
      if (stats && typeof stats === 'object' && 'pulled' in stats) {
        const s = stats as PollStats
        live.pulled = s.pulled
        live.acked = s.acked
        live.errors = s.errors
        return
      }
      // The pull settled with a failure value. An auth fatal means the GATEWAY rejected this
      // group's key: that is the authoritative verdict on the binding's claims, so the members stop
      // being armable in this process instead of being retried forever as "configured".
      if (stats instanceof Error && stats.name === 'PullAuthError') {
        for (const m of members) {
          refuseBindingAfterGatewayRejection(m.email)
        }
        log(
          `[aimail-pull] agent:${asid} gw=${rep.gateway_url}: gateway rejected the key ` +
            `(${stats.message}) — members=[${members.map((m) => m.email).join(', ')}] are no longer ` +
            'armed; re-bind with an activation code',
        )
      }
    })
    log(
      `[aimail-pull] no pointer — aggregate agent:${asid} gw=${rep.gateway_url} ` +
        `members=[${members.map((m) => m.email).join(', ')}] (rep ${rep.email}, ` +
        `${active.length}/${members.length} active)`,
    )
    handles.push({
      email: `agent:${asid}`,
      started: true,
      reason: 'enabled',
      intervalMs: settings.intervalMs,
      limit: settings.limit,
      stop: () => pullHandle.stop(),
      done: pullHandle.done as unknown as Promise<PollStats>,
      stats: () => ({ ...live }),
    })
  }
  return handles
}

/**
 * Delivery-level fresh lookup (P0-6 / P0-7 / plan §6.3, ONE source for the
 * tier-2, group-stream and per-binding wrappers): the arm-time pool is a
 * snapshot, so a pause/archive that happens WHILE the stream is running must
 * take effect on the very next delivery without any re-arm.
 *
 * `skip` is set when the mail must NOT be delivered right now:
 *   - no active binding for the addressed mail ⇒ skip('unknown') — replaces
 *     the former throw (still un-acked/re-pulled, but not counted as an error);
 *   - the binding is paused ⇒ skip('paused') — mail stays pending.
 * `cfg` is the CURRENT binding (authoritative for per-address routing), or
 * null when there is none.
 */
export async function freshDeliveryTarget(
  email: string,
): Promise<{ cfg: AgentConfig | null; skip: PullDeliveryResult | null }> {
  const want = String(email ?? '').trim().toLowerCase()
  if (!want) return { cfg: null, skip: { status: 'skip', reason: 'unknown' } }
  const loc = await findBindingByEmail(want)
  if (!loc.active) return { cfg: null, skip: { status: 'skip', reason: 'unknown' } }
  if (loc.active.paused === true) return { cfg: loc.active, skip: { status: 'skip', reason: 'paused' } }
  // A V2 binding that was not minted by the activation chain never receives mail, even if a stream
  // was armed before the file was swapped underneath us (this lookup is the per-delivery re-read).
  // The §7.1 shape (seven fields + stamp) is the V2 contract; a V1-shaped binding has no aggregate
  // stream to arm and is out of scope here.
  if (isV2AgentScopeBinding(loc.active)) {
    if (isRefusedByGateway(loc.active.email)) {
      return { cfg: null, skip: { status: 'skip', reason: 'unproven' } }
    }
    const verdict = bindingProvenance(loc.active as unknown as Record<string, unknown>)
    if (!verdict.ok) return { cfg: null, skip: { status: 'skip', reason: 'unproven' } }
  }
  return { cfg: loc.active, skip: null }
}

/** Skip-only projection of {@link freshDeliveryTarget} (P1-A). */
export async function deliverySkipCheck(email: string): Promise<PullDeliveryResult | null> {
  return (await freshDeliveryTarget(email)).skip
}

/** Explainable reason for the judgment result (asserted by both logs and unit tests). */
export type PullDecisionReason =
  | 'enabled'
  | 'no-binding'
  | 'not-agent-scope'
  | 'disabled-by-config'

export interface AgentPullSettings {
  enabled: boolean
  reason: PullDecisionReason
  intervalMs: number
  limit: number
  /** Resolved transport mode (AIMAIL_PULL_MODE; kill switch = "poll"). */
  mode: PullMode
}

export interface AgentPullOverrides {
  enabled?: boolean
  intervalMs?: number
  limit?: number
  mode?: PullMode
}

function envFlagOff(raw: string | undefined): boolean {
  if (raw === undefined) return false
  const v = raw.trim().toLowerCase()
  return v === '0' || v === 'false' || v === 'off' || v === 'no'
}

function clampInt(raw: string | number | undefined, def: number, min: number, max: number): number {
  const n = typeof raw === 'number' ? raw : Number(raw)
  if (!Number.isFinite(n) || n <= 0) return def
  return Math.max(min, Math.min(Math.trunc(n), max))
}

/**
 * Resolve whether/what to poll for one binding.
 *
 * Precedence: explicit override > env > default. `enabled` defaults to
 * true for an agent-scope binding (the only inbound path it has) and is
 * never true without one.
 */
export function resolveAgentPullSettings(
  cfg: AgentConfig | undefined | null,
  env: NodeJS.ProcessEnv = process.env,
  overrides: AgentPullOverrides = {},
): AgentPullSettings {
  const intervalMs = clampInt(
    overrides.intervalMs ?? env[PULL_INTERVAL_ENV],
    DEFAULT_PULL_INTERVAL_MS,
    MIN_PULL_INTERVAL_MS,
    24 * 3600_000,
  )
  const limit = clampInt(overrides.limit ?? env[PULL_LIMIT_ENV], DEFAULT_PULL_LIMIT, 1, 200)
  const mode: PullMode = overrides.mode || parsePullMode(env[PULL_MODE_ENV])
  if (!cfg) return { enabled: false, reason: 'no-binding', intervalMs, limit, mode }
  if (!isAgentScopeBinding(cfg)) {
    return { enabled: false, reason: 'not-agent-scope', intervalMs, limit, mode }
  }
  const off = overrides.enabled === undefined ? envFlagOff(env[PULL_ENABLE_ENV]) : overrides.enabled === false
  if (off) return { enabled: false, reason: 'disabled-by-config', intervalMs, limit, mode }
  return { enabled: true, reason: 'enabled', intervalMs, limit, mode }
}

/** One pulled delivery (batch body + its per-delivery routing facts). */
export interface PulledMail {
  id: number
  email: string
  headers: Record<string, unknown>
  body: unknown
}

/** The adapter's own inbound chain, entered with a pulled mail. */
export type PullInboundDelivery = (mail: PulledMail) => void | Promise<void>

export interface AgentPullHandle {
  /** The binding this loop serves (undefined when nothing was started). */
  email: string
  started: boolean
  reason: PullDecisionReason
  intervalMs: number
  limit: number
  /** Stop the loop (idempotent; resolves `done`). */
  stop(): void
  /** Settles when the loop exits (stats accumulated so far). */
  done: Promise<PollStats>
  /** Live stats view for observability/tests. */
  stats(): PollStats
}

export interface StartAgentPullEntriesOptions {
  /** System scope: '' ⇒ every system under ~/.aimail/systems. */
  systemId?: string
  /**
   * Explicit single-address filter (the host declares itself single-agent).
   * Undefined/empty ⇒ no filter (a scoped call starts one loop per
   * agent-scope binding in the system — declared fan-out). Non-empty ⇒
   * exactly ONE loop for the agent-scope binding whose address matches
   * (case-insensitive, deduped over copied files); zero matches ⇒ loud log +
   * NO loop (never a silent fallback to the full scan).
   */
  onlyEmail?: string
  /**
   * Delivery into THIS adapter's existing inbound chain. Receives the
   * binding it belongs to (so per-address routing stays authoritative).
   * May return the typed skip result (P1-A): void/undefined = accepted.
   */
  onEmail: (cfg: AgentConfig, mail: PulledMail) => void | Promise<void | PullDeliveryResult>
  env?: NodeJS.ProcessEnv
  overrides?: AgentPullOverrides
  /** Reporting line (default console.log) — one per binding + one when off. */
  log?: (line: string) => void
  /** Test seam: build the request client for a binding. */
  clientFor?: (cfg: AgentConfig) => RequestClient
}

/** Options for one binding (same knobs as the batch starter, minus enumeration). */
export type StartAgentPullEntryOptions = Omit<StartAgentPullEntriesOptions, 'systemId' | 'onlyEmail'>


/**
 * Start pull loops for the agent-scope bindings, delivering into the
 * caller's inbound chain. Returns the handles (empty ⇒ nothing enabled,
 * which is the push/systems case) — never throws for the disabled case.
 *
 * BRANCH GUARD (V2, plan §6.5): V2 bindings (agent_system_id) ALWAYS pull
 * through the AgentSystemId aggregate — `startAgentSystemGroups`, one stream
 * per (AgentSystemId, Gateway), never per binding. V1 bindings keep the
 * legacy per-binding loop. This closes the pre-V2 back doors: the scoped
 * fan-out branch and the unscoped single-binding branch could each start a
 * per-binding loop for a V2 binding (a second stream for the same group).
 *
 * `onlyEmail` (the host declares itself single-agent): empty/undefined ⇒ no
 * filter (a scoped call is a declared fan-out); non-empty ⇒ exactly ONE loop
 * for the matching address (case-insensitive, deduped over copied files);
 * zero matches ⇒ loud log + NO loop (never a silent fallback to the full
 * scan).
 *
 * An UNSCOPED call (`systemId` empty ⇒ the profile's pointer is missing)
 * forbids a silent full-scan fan-out (task-3 O1) for V1: without `onlyEmail`
 * exactly ONE V1 agent-scope binding on disk is unambiguous and starts; two
 * or more ⇒ loud log + NO loop. V2 is unaffected by the refusal (grouping is
 * self-scoped by AgentSystemId) but is still scoped to THIS profile's
 * AgentSystemId — other profiles' V2 bindings are never replayed into this
 * profile's inbound.
 *
 * Every started loop emits one audit line (py/TS verbatim):
 * `[aimail-pull] pull scope: system=<sid> email=<email> fanout=<true|false>`;
 * a group stream emits the A' audit line (members + rep, plan §6.6).
 */
export async function startAgentPullEntries(
  opts: StartAgentPullEntriesOptions,
): Promise<AgentPullHandle[]> {
  const log = opts.log ?? ((line: string) => console.log(line))
  const only = (opts.onlyEmail ?? '').trim().toLowerCase()
  const handles: AgentPullHandle[] = []
  let unscopedRefused = false

  // Agent-scope bindings in `pool` whose address matches `only`, deduped
  // over copied files for the same address (first in sort order).
  const targeted = (pool: AgentConfig[]): AgentConfig[] => {
    const matches = pool.filter((c) => (c.email ?? '').toLowerCase() === only)
    const seen = new Set<string>()
    const uniq: AgentConfig[] = []
    for (const c of matches) {
      const e = (c.email ?? '').toLowerCase()
      if (!seen.has(e)) {
        seen.add(e)
        uniq.push(c)
      }
    }
    return uniq
  }

  // V2 subset of a pool: V2 bindings scoped to THIS profile's
  // AgentSystemId (never another profile's mailboxes), deduped by address.
  //
  // …and the ONE place provenance is enforced, because arming is the point where a binding turns
  // into "we will accept mail for this address". A file that was not minted by
  // activateAddressCodePersist is REFUSED — no switch, no compat mode (nothing has shipped yet, so
  // an unstamped binding is hand-written or a writer bug), and the refusal names the reason.
  const v2Pool = async (pool: AgentConfig[]): Promise<AgentConfig[]> => {
    const asid = await getAgentSystemId()
    const seen = new Set<string>()
    const out: AgentConfig[] = []
    for (const c of pool) {
      if (!isV2AgentScopeBinding(c) || c.agent_system_id !== asid) continue
      const e = (c.email ?? '').toLowerCase()
      if (seen.has(e)) continue
      seen.add(e)
      if (isRefusedByGateway(c.email)) {
        log(
          `[aimail-pull] ${e}: binding REFUSED — the gateway already rejected this key ` +
            '(401/403); re-bind with an activation code (aimail_address)',
        )
        continue
      }
      const verdict = bindingProvenance(c as unknown as Record<string, unknown>)
      if (!verdict.ok) {
        // No compatibility mode: an unstamped binding cannot be a legacy artefact (nothing shipped
        // yet), so it is either hand-written or a writer bug. Refuse it and say why.
        log(
          `[aimail-pull] ${e}: binding REFUSED — provenance not proven (${verdict.reason}); ` +
            're-bind with an activation code (aimail_address)',
        )
        continue
      }
      out.push(c)
    }
    return out
  }

  /**
   * V1 (pre-AgentSystemId) bindings have no aggregate scope, and the per-address pull endpoints
   * were retired on 2026-11-26 — so the only honest outcome is "re-activate". Say it loudly and
   * start no stream: silently falling back would be a second pull transport that no longer exists
   * on the gateway.
   */
  const refuseV1 = (cfg: AgentConfig): void => {
    log(
      `[aimail-pull] ${cfg.email}: V1 binding (no agent_system_id) — per-address pull is retired; ` +
        're-activate the address with an activation code to join the aggregate (contract §5.3)',
    )
  }

  // Start the V2 subset through the ONE aggregate entry (A' grouping).
  const startGroups = async (pool: AgentConfig[]): Promise<void> => {
    const g = await v2Pool(pool)
    if (g.length === 0) return
    const groupHandles = await startAgentSystemGroups(g, opts)
    handles.push(...groupHandles)
    for (const h of groupHandles) {
      log(`[aimail-pull] pull scope: system=${opts.systemId ?? ''} email=${h.email} fanout=true`)
    }
  }

  if (opts.systemId) {
    if (only) {
      // Host declared single-agent: pin to the one matching address.
      const pool = (await listAgentConfigs(opts.systemId)).filter(isAgentScopeBinding)
      const uniq = targeted(pool)
      if (uniq.length === 0) {
        log(
          `[aimail-pull] pull scope: system=${opts.systemId} only_email=${opts.onlyEmail} — ` +
            'no binding matches; starting NO loop (mail stays ' +
            `pending; check pointer/binding) candidates=${pool.length}`,
        )
        return handles
      }
      // Branch guard: V2 target ⇒ its group aggregate, never a per-entry loop.
      if (isV2AgentScopeBinding(uniq[0])) {
        await startGroups(uniq)
      } else {
        refuseV1(uniq[0])
      }
    } else {
      // Scoped, no filter: declared fan-out over the system's bindings.
      // Branch guard: V2 agent-scope bindings go to the aggregate; everything
      // else (V1 agent-scope AND push-side) keeps the per-binding path — the
      // push-side "not-agent-scope" audit line must stay observable.
      const allCfgs = await listAgentConfigs(opts.systemId)
      const v2cfgs = allCfgs.filter(isAgentScopeBinding)
      for (const cfg of allCfgs.filter((c) => !isV2AgentScopeBinding(c))) {
        if (isAgentScopeBinding(cfg)) refuseV1(cfg)
      }
      await startGroups(v2cfgs)
    }
  } else {
    if (only) {
      const all: AgentConfig[] = []
      for (const sid of await listSystemDirs()) {
        for (const cfg of await listAgentConfigs(sid)) all.push(cfg)
      }
      const pool = all.filter(isAgentScopeBinding)
      const uniq = targeted(pool)
      if (uniq.length === 0) {
        log(
          `[aimail-pull] pull scope: system= only_email=${opts.onlyEmail} — ` +
            'no binding matches; starting NO loop (mail stays ' +
            `pending; check pointer/binding) candidates=${pool.length}`,
        )
        return handles
      }
      if (isV2AgentScopeBinding(uniq[0])) {
        await startGroups(uniq)
      } else {
        refuseV1(uniq[0])
      }
    } else {
      const all: AgentConfig[] = []
      for (const sid of await listSystemDirs()) {
        for (const cfg of await listAgentConfigs(sid)) all.push(cfg)
      }
      const agentScope = all.filter(isAgentScopeBinding)
      // V2: self-scoped by AgentSystemId — the O1 full-scan refusal does not
      // apply (grouping can only deliver THIS profile's own addresses).
      await startGroups(agentScope)
      // V1: the pre-V2 unscoped rules are unchanged.
      const v1 = agentScope.filter((c) => !isV2AgentScopeBinding(c))
      if (v1.length > 0) {
        unscopedRefused = unscopedRefused || v1.length > 1
        for (const cfg of v1) refuseV1(cfg)
      }
    }
  }
  if (handles.length === 0 && !unscopedRefused) {
    log(
      '[aimail-pull] no agent-scope binding — polling not started ' +
        '(push path unchanged; address-code activation enables it)',
    )
  }
  return handles
}

/** Stop every handle (host shutdown; idempotent). */
export function stopAgentPullEntries(handles: readonly AgentPullHandle[]): void {
  for (const h of handles) h.stop()
}
