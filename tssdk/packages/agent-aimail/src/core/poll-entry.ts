/**
 * poll-entry.ts — agent-scope scheduled polling entry (auto-pull wiring, slice 8).
 *
 * The **last wire** that the adapter install/initialization wrap-up needs to connect: the agent-scope obtained from address-level activation
 * key obtained from address-level activation cannot get push (the gateway writes `webhook_url = NULL` for that address, see
 * `aimail-advanced src/advanced/api/address.rs` system_domains INSERT),
 * so it can only pull on a schedule. Previously `pullList/pullAck/startPolling` had libraries and unit tests, **zero callers**,
 * The "agent autonomous access" promised in the documentation is missing this wire in production — this module is it.
 *
 * Semantics (same family as startPolling, no duplicate implementation):
 *   - **Only enabled for agent-level activation**: binding is a product of address code redemption ⇒ enabled; system/bridge scenarios
 *     (system registered by platform, goes through push) ⇒ not enabled. The criterion is an **existing fact**, not a guess:
 *     Address codes can only be redeemed under the `shared_addr_*` host system —
 *     `aimail-advanced src/advanced/api/address.rs:308`
 *     `if !sid.starts_with("shared_addr_") { return Ok(Err(ActErr::Invalid)); }`
 *     ⇒ binding `system_id` starts with that prefix ⇔ the key was redeemed from an address code (agent scope;
 *     see the header of `src/advanced/api/pull_intercept.rs`: agent scope ⇒ only serves its own address).
 *     regen/product creation paths **never** produce that prefix (`products.rs:83` explicitly excludes it)
 *     ⇒ system/bridge bindings will not be misjudged.
 *   - **No ack on failure / idempotent deduplication**: guaranteed by `startPolling` (if onEmail throws an error or
 *     rejects ⇒ that delivery is not acked, re-pulled in the next round; dedup key = delivery id).
 *   - **Stoppable / configurable interval**: handle `stop()` (AbortController) + stops on process exit
 *     (timer unref); interval/batch/master switch via env or explicit override.
 *   - **No timer leaks**: One binding per loop, handle convergence at startup.
 */
import type { AgentConfig } from './types.js'
import { GatewayClient } from './gateway.js'
import { listAgentConfigs } from './config.js'
import { listSystemDirs } from './auto-bind.js'
import { startPolling, type PollStats, type RequestClient } from './address-code.js'

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

/** whether this binding is an address-level (agent-scope) activation artifact — decided by existing facts, not guessed. */
export function isAgentScopeBinding(cfg: AgentConfig | undefined | null): boolean {
  if (!cfg) return false
  if (!cfg.system_id || !cfg.api_key) return false
  return cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX)
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
}

export interface AgentPullOverrides {
  enabled?: boolean
  intervalMs?: number
  limit?: number
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
  if (!cfg) return { enabled: false, reason: 'no-binding', intervalMs, limit }
  if (!isAgentScopeBinding(cfg)) {
    return { enabled: false, reason: 'not-agent-scope', intervalMs, limit }
  }
  const off = overrides.enabled === undefined ? envFlagOff(env[PULL_ENABLE_ENV]) : overrides.enabled === false
  if (off) return { enabled: false, reason: 'disabled-by-config', intervalMs, limit }
  return { enabled: true, reason: 'enabled', intervalMs, limit }
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
   * Delivery into THIS adapter's existing inbound chain. Receives the
   * binding it belongs to (so per-address routing stays authoritative).
   */
  onEmail: (cfg: AgentConfig, mail: PulledMail) => void | Promise<void>
  env?: NodeJS.ProcessEnv
  overrides?: AgentPullOverrides
  /** Reporting line (default console.log) — one per binding + one when off. */
  log?: (line: string) => void
  /** Test seam: build the request client for a binding. */
  clientFor?: (cfg: AgentConfig) => RequestClient
}

/** Options for one binding (same knobs as the batch starter, minus enumeration). */
export type StartAgentPullEntryOptions = Omit<StartAgentPullEntriesOptions, 'systemId'>

/**
 * Start the poll loop for ONE agent-scope binding — the hot-add unit.
 *
 * Activation happens long after the host booted, and the batch starter only
 * enumerates bindings once. `startAgentLevel` calls this from the
 * `binding-created` hook so the new mailbox starts receiving in the SAME
 * process, without a host restart. Semantics match the batch version (disabled
 * ⇒ null, a throw from `onEmail` ⇒ no ack).
 */
export async function startAgentPullEntry(
  cfg: AgentConfig,
  opts: StartAgentPullEntryOptions,
): Promise<AgentPullHandle | null> {
  const env = opts.env ?? process.env
  const log = opts.log ?? ((line: string) => console.log(line))
  const settings = resolveAgentPullSettings(cfg, env, opts.overrides ?? {})
  if (!settings.enabled) {
    // Only report the NOT-agent-scope decision for a binding that exists;
    // silence for push-side bindings would hide a mis-wiring.
    log(
      `[aimail-pull] ${cfg.email}: not polling (${settings.reason}) — ` +
        'push/system binding unchanged',
    )
    return null
  }
  const client = opts.clientFor
    ? opts.clientFor(cfg)
    : new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
  const controller = new AbortController()
  const live: PollStats = { pulled: 0, acked: 0, errors: 0 }
  const done = startPolling(
    client,
    async (mail) => {
      // Count first: startPolling awaits this, a throw ⇒ un-acked ⇒ re-pull.
      live.pulled += 1
      await opts.onEmail(cfg, {
        id: mail.id,
        email: mail.email,
        headers: (mail.headers ?? {}) as Record<string, unknown>,
        body: mail.body,
      })
    },
    {
      intervalMs: settings.intervalMs,
      limit: settings.limit,
      signal: controller.signal,
    },
  )
    .then((stats) => {
      live.pulled = stats.pulled
      live.acked = stats.acked
      live.errors = stats.errors
      return stats
    })
    .catch((e: unknown) => {
      log(`[aimail-pull] ${cfg.email}: loop ended with error: ${String(e)}`)
      return live
    })
  log(
    `[aimail-pull] ${cfg.email}: polling enabled every ${settings.intervalMs}ms ` +
      `(limit ${settings.limit}, agent-scope key)`,
  )
  return {
    email: cfg.email,
    started: true,
    reason: settings.reason,
    intervalMs: settings.intervalMs,
    limit: settings.limit,
    stop: () => controller.abort(),
    done,
    stats: () => ({ ...live }),
  }
}

/**
 * Start one poll loop per **agent-scope** binding, delivering into the
 * caller's inbound chain. Returns the handles (empty ⇒ nothing enabled,
 * which is the push/systems case) — never throws for the disabled case.
 */
export async function startAgentPullEntries(
  opts: StartAgentPullEntriesOptions,
): Promise<AgentPullHandle[]> {
  const log = opts.log ?? ((line: string) => console.log(line))
  const sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
  const bindings: AgentConfig[] = []
  for (const sid of sids) {
    for (const cfg of await listAgentConfigs(sid)) bindings.push(cfg)
  }
  const handles: AgentPullHandle[] = []
  for (const cfg of bindings) {
    const h = await startAgentPullEntry(cfg, opts)
    if (h !== null) handles.push(h)
  }
  if (handles.length === 0) {
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
