// Package resources root — single source of truth for where this package's
// staged resources/ (board role prompts + agentmail skill) live.
//
// core/ sits one directory below the package root in BOTH the source tree
// (src/core/) and the build output (dist/core/), so the package root is a
// fixed two levels up from this file. Adapters resolve their board/skill roots
// from here instead of deriving them from their own file location (which
// broke when the 5 packages merged into one: host entries moved one level
// deeper, dist/{host}/, while resources/ stayed at the package root).
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

export function packageResourcesRoot(): string {
  return resolve(dirname(fileURLToPath(import.meta.url)), '..', '..', 'resources')
}
