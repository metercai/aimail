/**
 * listen.ts — conflict-tolerant inbound listener bind (owner ruling 2026-10-25).
 *
 * Self-bind hosts (dsh / pi) all default to the SAME common port (the contract
 * `inbound_port`), so two hosts on one machine WILL collide. The bind result is
 * the source of truth: try the preferred port, and on EADDRINUSE walk up
 * (port+1 …) until one binds — the OS bind is an atomic arbiter, so concurrent
 * starters never double-bind (one wins, the other re-selects). The CONFIRMED
 * URL is only known once a bind has actually succeeded, so the caller computes
 * it from the returned actual port — never pre-computed from the preferred
 * port (a collision would silently report a dead endpoint).
 *
 * Contract:
 *   - preferred < 1 or >= 65536 → throw (a caller defect, not a runtime state).
 *   - at most `maxAttempts` tries (default 100); exhausting them rejects with
 *     an Error — the caller MUST surface this as a loud failure and must NOT
 *     report the inbound endpoint as live (a route pinned to an unbound port
 *     is a silent black hole).
 *   - non-EADDRINUSE errors propagate untouched (they are not port conflicts).
 */
import type { Server } from 'node:http'

export interface ListenWithRetryOptions {
  /** Max consecutive port probes (default 100). */
  maxAttempts?: number
}

/**
 * Listen `server` on `preferred`, re-selecting the next port on EADDRINUSE.
 * Resolves with the ACTUAL port the OS assigned (the source of truth); rejects
 * when no port was free within `maxAttempts` or the preferred port is invalid.
 * The caller keeps full ownership of `server` (close on dispose).
 */
export async function listenWithRetry(
  server: Server,
  host: string,
  preferred: number,
  opts: ListenWithRetryOptions = {},
): Promise<number> {
  if (!Number.isInteger(preferred) || preferred < 1 || preferred >= 65536) {
    throw new Error(`listenWithRetry: invalid preferred port ${preferred}`)
  }
  const max = Math.max(1, opts.maxAttempts ?? 100)
  for (let i = 0; i < max; i++) {
    const port = preferred + i
    try {
      // Named probe handler, removed by REFERENCE on success so a permanent
      // error handler the caller registered on the server survives (a
      // removeAllListeners would wipe it).
      await new Promise<void>((resolve, reject) => {
        const onErr = (e: Error) => reject(e)
        server.once('error', onErr)
        server.listen(port, host, () => {
          server.removeListener('error', onErr)
          resolve()
        })
      })
      return port
    } catch (e) {
      // EADDRINUSE ⇒ this port is taken; probe the next one (the OS bind is
      // the atomic arbiter — concurrent starters can never double-bind).
      // Any other error is not a port conflict: abort loudly.
      if ((e as NodeJS.ErrnoException).code !== 'EADDRINUSE') throw e
    }
  }
  throw new Error(
    `listenWithRetry: no free port in [${preferred}..${preferred + max - 1}] ` +
      `(host ${host}) — EADDRINUSE on every probe, inbound NOT live`,
  )
}
