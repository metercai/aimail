/**
 * pull-contract.ts — the pull-transport contract (Phase 2, three-client
 * parity). Single source for the long-pull / SSE-stream numbers and the
 * pure state-machine decision functions.
 *
 * Parity: py `aimail_base` (PULL_* constants + parse_pull_mode +
 * pull_*_backoff_secs + pull_decide_next_mode) and bridge `pull.rs`
 * (PULL_* consts + decide_next + *_backoff_secs). The py↔ts cross-language
 * test (pull-stream-state-machine.test.ts) re-runs the py functions via
 * stdin and diffs field-for-field.
 *
 * Kept in its own module (not poll-entry.ts) because BOTH address-code.ts
 * (startPolling) and poll-entry.ts (startAgentPullEntry) consume it —
 * poll-entry already imports address-code, so a reverse import would cycle.
 */

/** `GET` path of the SSE endpoint (also the signature path component). */
export const PULL_STREAM_PATH = '/api/v1/admin/pending/stream'

/** Long-pull server-side hold (ms): an empty queue is held up to this long. */
export const PULL_HOLD_MAX_MS = 50_000

/**
 * Client HTTP timeout (ms) for the long-pull round: hold + 15s of slack so
 * a healthy hold can never trip the client timer (65s > 50s hold).
 */
export const PULL_HTTP_TIMEOUT_MS = PULL_HOLD_MAX_MS + 15_000

/**
 * Per-read bound on the SSE socket (ms) — the OUTER cap. The effective
 * per-read timeout is min(ping timeout, this): it catches a healthy stream
 * whose frames are pathologically large, exactly as the bridge
 * (chunk_timeout = ping_timeout.min(90s)).
 */
export const PULL_STREAM_READ_TIMEOUT_MS = 90_000

/** No frame (including the 15s heartbeat) within this window ⇒ the link is dead or buffered. */
export const PULL_STREAM_PING_TIMEOUT_MS = 30_000

/** While in the Poll fallback, re-probe the stream endpoint every this long (ms). */
export const PULL_REPROBE_INTERVAL_MS = 600_000

/** Kill switch / transport mode environment variable. */
export const PULL_MODE_ENV = 'AIMAIL_PULL_MODE'

/** Transport mode: auto (probe once, stream primary) / stream (forced) / poll (kill switch). */
export type PullMode = 'auto' | 'stream' | 'poll'

/**
 * Result of one SSE round (parity: py `_stream_round` return values).
 * 'stop' = abort signal; 'stay' = server closed at max life (reconnect now);
 * 'occupied' = 409, another client holds this key's single-slot mailbox
 * (caller stays in the current mode and backoffs against the SAME endpoint);
 * 'drop' = connect failed / stalled (caller drops straight to Poll);
 * 'error' = mid-stream transport error (caller applies the decision fn).
 */
export type StreamRoundResult = 'stop' | 'stay' | 'occupied' | 'drop' | 'error'

/** One delivery (envelope metadata): no mail body. */
export interface PullDelivery {
  id: number
  email: string
  headers: unknown
}

/** One batch in a pull round: one payload + its delivery list. */
export interface PullBatch {
  body: unknown
  deliveries: PullDelivery[]
}

/**
 * `AIMAIL_PULL_MODE` ⇒ "auto" (default) / "stream" / "poll".
 * Unknown values degrade to "auto" (never guess a forced mode).
 * Parity: py parse_pull_mode.
 */
export function parsePullMode(raw: string | undefined): PullMode {
  if (raw === undefined) return 'auto'
  const v = raw.trim().toLowerCase()
  if (v === 'stream' || v === 'poll') return v
  return 'auto'
}

/** Stream reconnect backoff (s): 1,2,4,8,16,30. Parity: py/bridge. */
export function pullStreamBackoffSecs(streak: number): number {
  if (streak <= 0) return 0
  return Math.min(1 << Math.min(streak - 1, 5), 30)
}

/**
 * Poll failure backoff (s): interval_s * 2^failures, cap 300.
 * Exact mirror of bridge `poll_backoff_secs` (no lower bound: interval 0 ⇒ backoff 0).
 */
export function pullPollBackoffSecs(intervalS: number, failures: number): number {
  return Math.min(
    Math.max(0, Math.trunc(intervalS)) * (1 << Math.min(Math.max(0, Math.trunc(failures)), 6)),
    300,
  )
}

/**
 * Pure state-machine decision (three-client parity).
 *
 * @param overrideMode resolved transport mode (auto/stream/poll)
 * @param probe        probe outcome: null (none — use the streak) | 'ready' | 'unavailable'
 * @param streamFailStreak consecutive stream transport failures
 * @returns [nextMode, delaySecs]
 *
 *   - override "poll"         ⇒ poll immediately (kill switch)
 *   - probe 'ready'           ⇒ stream now
 *   - probe 'unavailable'     ⇒ poll (re-probe on the schedule)
 *   - probe null + "stream"   ⇒ stay stream with backoff (never downgrades)
 *   - probe null + streak >= 3 ⇒ downgrade to poll
 *   - probe null              ⇒ retry stream with backoff
 *
 * Parity: py pull_decide_next_mode / bridge decide_next.
 */
export function pullDecideNextMode(
  overrideMode: PullMode,
  probe: 'ready' | 'unavailable' | null,
  streamFailStreak: number,
): [PullMode, number] {
  if (overrideMode === 'poll') return ['poll', 0]
  if (probe === 'ready') return ['stream', 0]
  if (probe === 'unavailable') return ['poll', 0]
  if (overrideMode === 'stream') return ['stream', pullStreamBackoffSecs(streamFailStreak)]
  if (streamFailStreak >= 3) return ['poll', 0]
  return ['stream', pullStreamBackoffSecs(streamFailStreak)]
}
