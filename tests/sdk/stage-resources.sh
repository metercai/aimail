#!/usr/bin/env bash
# stage-resources.sh — stage the agent-aimail package's resources/ from the canonical
# single source (this repo's resources/).
#
# Why: an npm tarball can only carry files under the package directory, so the package
# ships its own copy staged in its OWN prepack through the shared entry
# tssdk/packages/agent-aimail/scripts/stage-resources.mjs. Harness runs need that copy
# present before the install chain / S9 probes read it — it is gitignored, so a clean
# checkout has none. There is no repo-level materialize script any more.
# 2026-10-22 single package merge: staging object converged from old 5-package list to unique package agent-aimail (old package directories deleted).
#
# Usage: stage-resources.sh [aimail-repo]     (default: this script's repo root)
set -euo pipefail
SELF_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="${1:-${AIMAIL_REPO:-$SELF_REPO}}"
STAGER="$REPO/tssdk/packages/agent-aimail/scripts/stage-resources.mjs"
[ -f "$STAGER" ] || { echo "✗ stage-resources: cannot find in-package common staging entry $STAGER" >&2; exit 2; }
[ -d "$REPO/resources" ] || { echo "✗ stage-resources: source of truth missing $REPO/resources" >&2; exit 2; }
command -v node >/dev/null 2>&1 || { echo "✗ stage-resources: node required" >&2; exit 2; }

node "$STAGER" --dest "$REPO/tssdk/packages/agent-aimail/resources" >/dev/null \
  || { echo "✗ stage-resources: agent-aimail staging failed" >&2; exit 1; }
echo "staged: agent-aimail ← $REPO/resources"
