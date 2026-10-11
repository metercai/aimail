#!/usr/bin/env bash
# stage-resources.sh — stage every npm adapter package's resources/ from the canonical
# single source (this repo's resources/).
#
# Why: each npm adapter package ships its own copy (a tarball can only carry files under the
# package directory) and stages it in its OWN prepack through the shared entry
# tssdk/packages/mail-core/scripts/stage-resources.mjs. Harness runs need those copies
# present before the install chain / S9 probes read them — they are gitignored, so a clean
# checkout has none. There is no repo-level materialize script any more.
#
# Usage: stage-resources.sh [aimail-repo]     (default: this script's repo root)
set -euo pipefail
SELF_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="${1:-${AIMAIL_REPO:-$SELF_REPO}}"
STAGER="$REPO/tssdk/packages/mail-core/scripts/stage-resources.mjs"
[ -f "$STAGER" ] || { echo "✗ stage-resources: 找不到包内共同暂存入口 $STAGER" >&2; exit 2; }
[ -d "$REPO/resources" ] || { echo "✗ stage-resources: 真源缺失 $REPO/resources" >&2; exit 2; }
command -v node >/dev/null 2>&1 || { echo "✗ stage-resources: 需要 node" >&2; exit 2; }

for p in dsh-aimail openclaw-aimail pi-aimail; do
  node "$STAGER" --dest "$REPO/tssdk/packages/$p/resources" >/dev/null \
    || { echo "✗ stage-resources: $p 暂存失败" >&2; exit 1; }
done
echo "staged: dsh-aimail openclaw-aimail pi-aimail ← $REPO/resources"
