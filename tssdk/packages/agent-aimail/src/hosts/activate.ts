// Host activation assertion — the "identify first, then branch" contract (#5).
//
// Each per-host entry (dist/{host}/index.js) is loaded BY that host's own
// loader. Once it runs, the host SDK is present by construction. activateHost()
// verifies the detected environment matches the entry's declared host, and logs
// a warning (never throws — the host owns its lifecycle) on mismatch, so a
// package mis-installed under the wrong host fails loudly instead of
// half-activating.
//
// Verification is only meaningful when the environment is UNAMBIGUOUS —
// exactly one host's signals are present. In a dev/workspace checkout all three
// host SDKs are installed as devDeps, and in a bare test process none are: in
// both cases detection can't distinguish, so the assertion reports
// `verified: false` and stays consistent (no false mismatch). In production a
// host installs only its own peer (all peers are optional, npm won't pull the
// others), so exactly one host is present and the assertion is verified.

import { HOSTS } from './registry.js'
import { detectHostsPresent } from './detect.js'

export interface ActivationResult {
  /** the host this entry declares itself for. */
  declared: string
  /** the host the environment detected, or null when not verifiable. */
  detected: string | null
  /** true only when exactly one host is present (detection is unambiguous). */
  verified: boolean
  /** true when not verifiable, or declared matches detected. */
  consistent: boolean
  /** all hosts whose signals are present (diagnostics). */
  present: string[]
}

function logLine(msg: string): void {
  try {
    console.error(`[agent-aimail] ${msg}`)
  } catch {
    /* console unavailable — never break host startup over logging */
  }
}

/**
 * Assert the current environment matches `declaredHost`.
 *
 * - exactly one host present and it === declared → verified, consistent, silent.
 * - exactly one host present and it !== declared → verified, INCONSISTENT, warn.
 * - 0 or >1 hosts present → not verifiable (dev/test/meta-host), consistent,
 *   silent.
 *
 * Returns the full result so entries/tests can assert on it.
 */
export function activateHost(declaredHost: string): ActivationResult {
  const present = detectHostsPresent()
  const verified = present.length === 1
  const detected = verified ? present[0] : null
  const consistent = !verified || detected === declaredHost

  if (!HOSTS[declaredHost]) {
    logLine(`unknown declared host '${declaredHost}' — add it to src/hosts/registry.ts`)
  } else if (!consistent) {
    logLine(
      `host mismatch: entry declares '${declaredHost}' but environment looks like '${detected}'. ` +
      `The package may be installed under the wrong host — continuing, but check the install.`,
    )
  }
  return { declared: declaredHost, detected, verified, consistent, present }
}
