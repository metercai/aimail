// Host environment detection — multi-signal, lazy, never throws.
//
// Used for two purposes (NOT for choosing which entry runs — the host's own
// loader does that):
//   1. assertion: a host entry verifies "the host that loaded me is the host I
//      claim to be" (catches a package mis-installed under the wrong host).
//   2. diagnostics / direct import: `import 'agent-aimail'` then detectHost().

import { existsSync } from 'node:fs'
import { homedir } from 'node:os'
import { join } from 'node:path'
import { createRequire } from 'node:module'
import { HOSTS, HOST_KEYS, type HostSpec } from './registry.js'

const req = createRequire(import.meta.url)

/** Resolve a bare module spec against this runtime without importing it. */
function canResolve(spec: string): boolean {
  try {
    void req.resolve(spec)
    return true
  } catch {
    return false
  }
}

function hostPresent(spec: HostSpec): boolean {
  // Signal 1: the host SDK is resolvable (strongest).
  if (spec.sdkSpecs.some((s) => canResolve(s))) return true
  // Signal 2: host config dir exists under $HOME (weaker, host ran before).
  if (existsSync(join(homedir(), spec.configDir))) return true
  return false
}

/**
 * Detect the current host. Returns the host key, or null when no host signal
 * is present (e.g. running in a plain node process during tests / CLI).
 *
 * Order = HOST_KEYS order (openclaw, dsh, pi). If more than one host's SDK is
 * resolvable (rare, e.g. a meta-host bundling several), the first match wins —
 * the entry's activateHost() assertion would then reject a mismatch.
 */
export function detectHost(): string | null {
  for (const key of HOST_KEYS) {
    if (hostPresent(HOSTS[key])) return key
  }
  return null
}

/** All hosts whose signals are present (for diagnostics / tests). */
export function detectHostsPresent(): string[] {
  return HOST_KEYS.filter((k) => hostPresent(HOSTS[k]))
}
