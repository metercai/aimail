/**
 * Address-code consumption chain (open-application plan §2.4/§2.5, slice 7).
 *
 * Type-3 addresses (`{agentname}@{shared_domain}`) are consumed by the
 * AGENT itself — no CLI involvement:
 *
 *   apply-address (web) → activation code arrives by email
 *     → `activateAddressCode` (public endpoint; the code IS the credential)
 *     → `activateAddressCodePersist` (same + register-isomorphic persistence)
 *     → `startPolling` pulls its own mail (agent-scope key)
 *
 * The persist step writes the same files the register path produces
 * (auto-bind.ts saveBinding: email/gateway_url/domain/system_id/api_key
 * + system_name/manager_address when known), so every consumer
 * (tools, preprocess, pull) works with zero changes.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { fireBindingCreated } from './binding-hooks.js'
import {
  PULL_HOLD_MAX_MS,
  PULL_MODE_ENV,
  PULL_REPROBE_INTERVAL_MS,
  parsePullMode,
  pullDecideNextMode,
  pullPollBackoffSecs,
  type PullBatch,
  type PullMode,
  type StreamRoundResult,
} from './pull-contract.js'
import {
  type AgentConfig,
} from './types.js'
import {
  gatewayConfigPath,
  saveAgentConfig,
} from './config.js'
import { POINTER_FILE } from './contract.js'
import type { GatewayResponse } from './types.js'

/**
 * Minimal client surface these helpers need (GatewayClient satisfies it).
 * `baseUrl` is required: the persist chain derives `gateway_url` from it when
 * the caller omits `opts.gatewayUrl` — Python-side parity, where the binding
 * always carries the client's configured gateway URL.
 *
 * Stream methods are OPTIONAL: only the real GatewayClient implements them
 * (fetch + ReadableStream). A client without them (test fakes, SSE-less
 * hosts) is poll-only — the state machine goes straight to Poll.
 */
export interface RequestClient {
  /** Base URL the client talks to (GatewayClient stores it without trailing slash). */
  readonly baseUrl: string
  request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<GatewayResponse>
  /** `GET /stream` probe: true = 200 + text/event-stream (30s connect bound). */
  streamProbe?(): Promise<boolean>
  /** One SSE round; result semantics: see pull-contract.ts StreamRoundResult. */
  streamRound?(
    onBatches: (batches: PullBatch[]) => void | Promise<void>,
    signal?: AbortSignal,
    pingTimeoutMs?: number,
  ): Promise<StreamRoundResult>
}

export interface ActivateAddressCodeResult {
  success: boolean
  raw_key?: string | undefined
  system_id?: string | undefined
  email_address?: string | undefined
  expires_at?: string | undefined
  /**
   * The manager (safety officer) the gateway stored for this address during
   * activation. Carried into the binding so the agent-side tools address the
   * same mailbox the gateway recorded; empty when the gateway has none.
   */
  manager_address?: string | undefined
  error?: string | undefined
  status?: number | undefined
}

/**
 * Activate a shared_addr address code. The email is lowercased before
 * sending: the gateway stores the key's address lowercased and agent-scope
 * pull matches on exact-email SQL — a case mismatch would silently orphan
 * the agent's mail.
 */
export async function activateAddressCode(
  client: RequestClient,
  code: string,
  emailAddress: string,
): Promise<ActivateAddressCodeResult> {
  const res = await client.request('POST', '/api/v1/activate-address-code', {
    code,
    email_address: emailAddress.trim().toLowerCase(),
  })
  const rawKey = res.raw_key as string | undefined
  if (!rawKey) {
    return {
      success: false,
      error: String(res.error ?? 'activation failed'),
      status: typeof res.status === 'number' ? res.status : undefined,
    }
  }
  return {
    success: true,
    raw_key: rawKey,
    system_id: res.system_id as string | undefined,
    email_address: res.email_address as string | undefined,
    expires_at: res.expires_at as string | undefined,
    manager_address: (res.manager_address as string | undefined) || undefined,
  }
}

export interface ActivateAddressCodePersistOptions {
  /**
   * Gateway base URL recorded in the binding (the gateway the agent uses).
   * Optional: when omitted it is derived from `client.baseUrl` (Python parity
   * — `self.gateway_url`), so the binding always carries a `gateway_url`.
   */
  gatewayUrl?: string
  /**
   * Agent id recorded in the binding. Defaults to the address local-part
   * (the mailbox IS the agent — the address flow has no separate agent
   * concept).
   */
  agentId?: string
  /** Platform home dir; when set, the `.agentmail` discovery pointer is written there. */
  platformHome?: string
}

export interface ActivateAddressCodePersistResult extends ActivateAddressCodeResult {
  config_path?: string | undefined
  pointer_written?: boolean | undefined
  /** Absolute path of the landed pointer ('' when none was written). */
  pointer_path?: string | undefined
}

/**
 * Activate AND persist — the whole consumption chain in one call.
 *
 * Writes:
 * 1. `~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json` — fields
 *    isomorphic to the register path (saveBinding), plus `expires_at` so the
 *    agent can surface its own renewal window and `manager_address` (the value
 *    the gateway stored at activation, returned by the endpoint).
 * 2. `~/.aimail/systems/{sid}/aimail_gateway.json` — create-if-absent only
 *    (never clobbers an existing system file), agent scope: connection facts
 *    only, no admin key, 0600.
 * 3. `{platformHome}/.agentmail` pointer (best-effort) when given —
 *    discovery/log naming read it.
 */
export async function activateAddressCodePersist(
  client: RequestClient,
  code: string,
  emailAddress: string,
  opts: ActivateAddressCodePersistOptions,
): Promise<ActivateAddressCodePersistResult> {
  // Derive the binding's gateway_url BEFORE the activation request: when the
  // caller omits opts.gatewayUrl, fall back to the client's configured base
  // URL (Python parity — self.gateway_url). Failing early means an empty
  // result cannot leak an orphaned raw_key from a half-persisted activation.
  const gatewayUrl = (opts.gatewayUrl ?? client.baseUrl ?? '').trim()
  if (!gatewayUrl) {
    return {
      success: false,
      error: 'cannot derive gateway_url: opts.gatewayUrl missing and client has no ' +
        'baseUrl — refusing to persist a binding without it (contract: 7 fields)',
    }
  }
  const act = await activateAddressCode(client, code, emailAddress)
  if (!act.success || !act.raw_key || !act.system_id || !act.email_address) {
    return act
  }
  const sid = act.system_id
  const email = act.email_address
  const domain = email.includes('@') ? email.split('@').pop()! : ''
  const agentId = opts.agentId || email.split('@')[0] || 'agent'

  // 1. Address-keyed agentmail.json (register-isomorphic + expires_at).
  const cfg: AgentConfig = {
    email,
    gateway_url: gatewayUrl,
    domain,
    system_id: sid,
    api_key: act.raw_key,
    agent_id: agentId,
  }
  if (act.expires_at) cfg.expires_at = act.expires_at
  // Manager = what the gateway actually stored during activation (read back
  // in the activation transaction and returned in the response). Carried
  // into the binding so `manageContacts(add)` can send its approval request
  // without inferring the manager later; the gateway stays authoritative.
  if (act.manager_address) cfg.manager_address = act.manager_address
  const p = await saveAgentConfig(cfg, sid)
  // hot-add: the runtime that is already running must start polling this
  // mailbox now, not after the next host restart.
  await fireBindingCreated(cfg)

  // 2. Gateway connection file — create-if-absent only, **agent scope**.
  //    Audit D3: this used to write the agent's raw key into a field named
  //    `admin_key` — a semantic mismatch (it is not a management key) and a
  //    second copy of the key. It now holds connection facts + the scope
  //    marker only; the agent key lives in the binding alone
  //    (`aimail_base._load_gateway_config` accepts a scope=agent file, and
  //    the pull loop reads gateway_url from the binding).
  const gwPath = gatewayConfigPath(sid)
  try {
    await fs.access(gwPath)
  } catch {
    await fs.mkdir(path.dirname(gwPath), { recursive: true })
    await fs.writeFile(
      gwPath,
      JSON.stringify(
        {
          gateway_url: gatewayUrl,
          system_id: sid,
          domain,
          scope: 'agent',
        },
        null,
        2,
      ) + '\n',
      { mode: 0o600 },
    )
  }

  // 3. Discovery pointer (best-effort) — filename from the contract constant
  //    (never spelled out here: the literal ratchet gate), written 0600 via
  //    tmp+rename so a crash can never leave a partial pointer (parity with
  //    the Python side's atomic_write_private).
  let pointerWritten = false
  let pointerPath = ''
  if (opts.platformHome) {
    try {
      const ptrPath = path.join(opts.platformHome, POINTER_FILE)
      await fs.mkdir(path.dirname(ptrPath), { recursive: true, mode: 0o700 })
      const tmp = `${ptrPath}.tmp`
      await fs.writeFile(
        tmp,
        JSON.stringify({ system_id: sid, email }, null, 2) + '\n',
        { mode: 0o600 },
      )
      await fs.rename(tmp, ptrPath)
      pointerWritten = true
      pointerPath = ptrPath
    } catch {
      // best-effort: discovery degrades, the binding itself is intact
    }
  }

  return {
    success: true,
    raw_key: act.raw_key,
    system_id: sid,
    email_address: email,
    expires_at: act.expires_at,
    config_path: p,
    pointer_written: pointerWritten,
    pointer_path: pointerPath,
  }
}

export type { PullBatch, PullDelivery } from './pull-contract.js'

/**
 * Agent-scope pull over the shared `/api/v1/admin/pending` endpoint. With an
 * agent-scope key the gateway's interception layer serves ONLY this key's own
 * address — the `emails` field exists for bridge shape compatibility and is
 * ignored server-side (scope IS the range).
 */
export async function pullList(
  client: RequestClient,
  limit = 20,
): Promise<{ success: boolean; batches: PullBatch[]; error?: string }> {
  const res = await client.request('POST', '/api/v1/admin/pending', {
    limit: Math.max(1, Math.min(limit, 200)),
    // Long-pull hold: an empty queue is held server-side up to PULL_HOLD_MAX_MS
    // (old servers ignore the field ⇒ immediate answer).
    timeout_ms: PULL_HOLD_MAX_MS,
  })
  if (res.status !== 200) {
    return { success: false, batches: [], error: String(res.error ?? 'pull list failed') }
  }
  return { success: true, batches: (res.batches as PullBatch[]) ?? [] }
}

/** Ack pulled deliveries. Ownership is enforced server-side (cross-address ids affect 0 rows). */
export async function pullAck(
  client: RequestClient,
  ids: number[],
): Promise<{ success: boolean; acked?: number | undefined; error?: string | undefined }> {
  const res = await client.request('POST', '/api/v1/admin/pending/ack', { ids })
  if (res.status !== 200) {
    return { success: false, error: String(res.error ?? 'ack failed') }
  }
  return { success: true, acked: res.acked as number | undefined }
}

export interface PollStats {
  pulled: number
  acked: number
  errors: number
}

export interface StartPollingOptions {
  intervalMs?: number
  limit?: number
  /** Bounds the Poll rounds for tests (undefined = forever; probes/stream are not counted). */
  maxRounds?: number
  /**
   * Transport mode. Default: AIMAIL_PULL_MODE env or "auto".
   * "poll" = kill switch (no probing); "stream" = forced (never downgrades).
   */
  mode?: PullMode
  /** Per-read SSE timeout override in ms (default: min(30s ping, 90s read cap)). */
  streamPingTimeoutMs?: number
  /**
   * Cooperative shutdown. When aborted the loop returns at the next
   * check with the stats accumulated so far (in-flight round included);
   * a pending interval sleep is interrupted immediately. Hosts pass the
   * signal of their own lifecycle so no timer outlives the process.
   */
  signal?: AbortSignal
}

/** Abortable interval sleep. The timer is unref'd so a polling loop can
 *  never be the handle that keeps a host process alive ("stop on process exit"):
 *  the host's own listener/session remains the liveness owner. */
function pollSleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const t = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    // A host process must be free to exit; the loop is not a liveness anchor.
    ;(t as { unref?: () => void }).unref?.()
    function onAbort (): void {
      clearTimeout(t)
      resolve()
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/**
 * Shared forward+ack path used by BOTH the Stream and the Poll transports
 * (parity: py `_deliver_batches`). Dedup key = delivery id (stable and
 * unique). An `onEmail` failure — sync throw OR rejected promise — leaves
 * the id un-acked and removes it from the dedup set: the mail re-pulls next
 * round (nothing is lost, §9 failover).
 */
async function deliverBatches(
  client: RequestClient,
  batches: PullBatch[],
  onEmail: (mail: { id: number; email: string; headers: unknown; body: unknown }) => void | Promise<void>,
  seen: Set<number>,
  stats: PollStats,
): Promise<void> {
  const delivered: number[] = []
  for (const batch of batches) {
    let body: unknown = batch.body
    if (typeof body === 'string') {
      try {
        body = JSON.parse(body) as unknown
      } catch {
        // keep the raw string — onEmail sees the wire shape
      }
    }
    for (const d of batch.deliveries ?? []) {
      if (seen.has(d.id)) continue
      seen.add(d.id)
      try {
        // Awaited: an async delivery that rejects must count as a failure
        // (un-acked ⇒ re-pull). Fire-and-forget would ack mail the agent
        // never took.
        await onEmail({ id: d.id, email: d.email, headers: d.headers, body })
        delivered.push(d.id)
        stats.pulled++
      } catch {
        // un-acked on purpose: re-pulls next round
        seen.delete(d.id)
        stats.errors++
      }
    }
  }
  if (delivered.length) {
    const ack = await pullAck(client, delivered)
    if (ack.success) stats.acked += ack.acked ?? 0
  }
}

/**
 * Stream (SSE) primary / long-pull fallback — state machine (three-client
 * parity with the bridge `start_pull_loop` and py `start_polling`).
 *
 * Transport selection:
 * * mode "auto" (default, or absent AIMAIL_PULL_MODE): the first stream
 *   round doubles as the probe — available ⇒ Stream state; not available
 *   (404/405/old server/transport failure) ⇒ Poll state. While in Poll the
 *   stream is re-probed every PULL_REPROBE_INTERVAL_MS (server-upgrade
 *   recovery) — NOT on every round.
 * * mode "stream": always Stream; transport drops never downgrade
 *   (reconnect with exponential backoff).
 * * mode "poll": kill switch — long-pull only, no probing.
 * A client WITHOUT stream methods (test fakes) is poll-only: straight to
 * Poll, no probing.
 *
 * Stream state: a mail event reuses the exact batch delivery + ack path of
 * the Poll state (one shared deliverBatches). Server closed at its max life
 * ⇒ immediate reconnect (rotation — and it RESETS the drop streak, only a
 * healthy round breaks it); no data within the ping window (dead/buffered
 * link) ⇒ Poll; mid-stream transport drops ⇒ exponential backoff 1→30s
 * (the streak persists across reconnects), 3 consecutive ⇒ Poll; re-probe
 * recovery also resets the streak.
 *
 * Poll state: one long-pull round per iteration (pullList carries the
 * timeout_ms hold). An EMPTY round already waited server-side for the hold
 * ⇒ a plain interval sleep paces the next round; a NON-EMPTY round does NOT
 * sleep (continuous mail flow is not throttled by the poll interval); a
 * FAILED round backs off exponentially (interval*2^failures, cap 300s —
 * bridge parity).
 *
 * Delivery semantics (unchanged from the legacy loop, item-by-item): dedup
 * key = delivery id; an onEmail failure leaves the id un-acked; maxRounds
 * bounds Poll rounds only (probes are not counted); signal stops the loop at
 * the next check and interrupts any pending sleep.
 */
export async function startPolling(
  client: RequestClient,
  onEmail: (mail: { id: number; email: string; headers: unknown; body: unknown }) => void | Promise<void>,
  opts: StartPollingOptions = {},
): Promise<PollStats> {
  const intervalMs = opts.intervalMs ?? 30_000
  const limit = opts.limit ?? 20
  const mode: PullMode = opts.mode ?? parsePullMode(process.env[PULL_MODE_ENV])
  const stats: PollStats = { pulled: 0, acked: 0, errors: 0 }
  const seen = new Set<number>()
  const sleep = (ms: number) => pollSleep(ms, opts.signal)
  // Explicit transport state: true while in the long-poll fallback.
  // Auto starts optimistic (Stream); Poll is forced.
  let inPoll = mode === 'poll'
  let streamStreak = 0
  let pollFailures = 0
  // "Due immediately" so the first Poll round re-probes right away.
  let lastReprobe = Date.now() - PULL_REPROBE_INTERVAL_MS
  let rounds = 0
  for (;;) {
    if (opts.signal?.aborted) break
    // ── Poll state: due re-probe of the stream endpoint ────────────
    // Contract cadence (10 min), NOT every round — a streamless server
    // costs one 404 per reprobe, not per poll. A failed probe sleeps the
    // round out; a ready probe hands over to the stream branch below.
    if (inPoll && mode !== 'poll' && Date.now() - lastReprobe >= PULL_REPROBE_INTERVAL_MS) {
      lastReprobe = Date.now()
      if (client.streamProbe && (await client.streamProbe())) {
        streamStreak = 0 // recovery: a fresh stream run starts clean
        inPoll = false
      } else {
        await sleep(intervalMs)
        continue
      }
    }
    // ── Stream state (auto/stream modes, not currently in poll) ────
    if (!inPoll && client.streamRound) {
      const res = await client.streamRound(
        (batches) => deliverBatches(client, batches, onEmail, seen, stats),
        opts.signal,
        opts.streamPingTimeoutMs,
      )
      if (res === 'stop') break
      if (res === 'stay') {
        // Server max-life rotation: a HEALTHY round. Only a healthy
        // rotation breaks the transport-error streak (a fresh drop run
        // starts from 1) — bridge parity (plan 0.3 B).
        streamStreak = 0
        continue
      }
      if (res === 'error') {
        // Mid-stream transport drop: shared decision function (override
        // "stream" never downgrades; auto gives up after 3 consecutive).
        streamStreak++
        const [nextMode, backoff] = pullDecideNextMode(mode, null, streamStreak)
        if (nextMode === 'poll') {
          streamStreak = 0
          inPoll = true
          lastReprobe = Date.now()
        } else {
          if (backoff > 0) await sleep(backoff * 1000)
          continue
        }
      } else {
        // 'drop': connect failed or stalled — no alternative transport;
        // the Poll-state re-probe recovers later.
        inPoll = true
        lastReprobe = Date.now()
      }
    } else if (!inPoll) {
      // Poll-only client (no stream methods): straight to Poll.
      inPoll = true
      lastReprobe = Date.now()
    }
    // ── Poll state: one long-pull round ─────────────────────────────
    if (opts.maxRounds !== undefined && rounds >= opts.maxRounds) break
    if (opts.signal?.aborted) break
    rounds++
    try {
      const lst = await pullList(client, limit)
      if (!lst.success) {
        // Failed round: exponential backoff (bridge parity:
        // interval*2^failures, cap 300s) — NOT the plain interval.
        stats.errors++
        pollFailures++
        await sleep(pullPollBackoffSecs(Math.trunc(intervalMs / 1000), pollFailures) * 1000)
        continue
      }
      pollFailures = 0
      const batches = lst.batches
      if (!batches.length) {
        // Empty round: the server held us for timeout_ms already, so a
        // plain poll-interval sleep paces the next round.
        await sleep(intervalMs)
        continue
      }
      // Non-empty round: NO sleep — straight to the next round so a
      // continuous mail flow is not throttled by the poll interval.
      await deliverBatches(client, batches, onEmail, seen, stats)
    } catch {
      stats.errors++
      pollFailures++
      await sleep(pullPollBackoffSecs(Math.trunc(intervalMs / 1000), pollFailures) * 1000)
    }
  }
  return stats
}
