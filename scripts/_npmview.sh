#!/usr/bin/env bash
# _npmview.sh — lets gate scripts distinguish the two "npm view cannot get the version" cases (audit 2026-09-21):
#   · E404 / no match found  → the version is genuinely **not published** (normal state)
#   · other errors (ETIMEDOUT/EAI_AGAIN/ENOTFOUND/permission…) → **query failed** (hard failure, must be reported)
# The original `npm view … 2>/dev/null || true` collapsed both into an "empty string" ⇒ a network blip was treated
# as "not published" and the root cause was lost (the historical E415/empty-package incident is exactly this class).
#
# Usage: npm_version <pkg> [<ver>]
#   success (published) → prints the version on stdout, rc=0
#   not published       → empty stdout, rc=0
#   query failed        → empty stdout, rc=2 (the caller must exit 1 and print the reason)
npm_version() {
  local pkg="$1" ver="${2:-}" spec out
  if [ -n "$ver" ]; then spec="$pkg@$ver"; else spec="$pkg"; fi
  if out=$(npm view "$spec" version --prefer-online 2>&1); then
    # --prefer-online: bypass the npm local cache — an immediate post-publish recheck against the cache would
    # still report E404 for the just-published version (measured 2026-09-21 v0.1.12: pi-aimail@0.1.12 was published
    # yet unqueryable; a direct registry.npmjs.org hit confirmed dist-tags already updated) ⇒ a false-red gate.
    printf '%s' "$out"; return 0
  fi
  if printf '%s' "$out" | grep -qiE 'E404|no match found|not found|could not be found'; then
    return 0
  fi
  printf '%s\n' "npm query failed for $spec: $out" >&2
  return 2
}
