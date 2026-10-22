#!/usr/bin/env bash
# stage-resources.sh — stage the agent-aimail package's resources/ from the canonical
# single source (this repo's resources/).
#
# Why: an npm tarball can only carry files under the package directory, so the package
# ships its own copy staged in its OWN prepack through the shared entry
# tssdk/packages/agent-aimail/scripts/stage-resources.mjs. Harness runs need that copy
# present before the install chain / S9 probes read it — it is gitignored, so a clean
# checkout has none. There is no repo-level materialize script any more.
# 2026-10-22 单包合并: 暂存对象从旧 5 包清单收敛为唯一包 agent-aimail(旧包目录已删)。
#
# Usage: stage-resources.sh [aimail-repo]     (default: this script's repo root)
set -euo pipefail
SELF_REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REPO="${1:-${AIMAIL_REPO:-$SELF_REPO}}"
STAGER="$REPO/tssdk/packages/agent-aimail/scripts/stage-resources.mjs"
[ -f "$STAGER" ] || { echo "✗ stage-resources: 找不到包内共同暂存入口 $STAGER" >&2; exit 2; }
[ -d "$REPO/resources" ] || { echo "✗ stage-resources: 真源缺失 $REPO/resources" >&2; exit 2; }
command -v node >/dev/null 2>&1 || { echo "✗ stage-resources: 需要 node" >&2; exit 2; }

node "$STAGER" --dest "$REPO/tssdk/packages/agent-aimail/resources" >/dev/null \
  || { echo "✗ stage-resources: agent-aimail 暂存失败" >&2; exit 1; }
echo "staged: agent-aimail ← $REPO/resources"
