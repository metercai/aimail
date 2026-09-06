#!/usr/bin/env bash
# Release gate L0 — full test sweep before ANY publish (local + CI).
# Run from repo root (or anywhere; script locates the root itself).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "═══ [L0] python lint + unit tests ═══"
# Core runtime modules: strict (no unused/undefined). Deploy-time patch
# scripts (hermes/patch_* etc.) intentionally import `aimail` for
# side-effect/eval use — syntax-check only those.
python3 -m pyflakes pysdk/aimail_base.py pysdk/aimail_board.py pysdk/aimail_tools.py \
  pysdk/amail_mcp_server.py pysdk/hermes/aimail_hermes.py pysdk/deer-flow/*.py 2>/dev/null \
  || { echo "[L0] FAIL: pyflakes (core)"; exit 1; }
for f in $(find pysdk -name '*.py' -not -path '*__pycache__*'); do
  python3 -m py_compile "$f" || { echo "[L0] FAIL: py_compile $f"; exit 1; }
done
python3 -m pytest tests/ -q 2>&1 | tail -2

echo "═══ [L0] tssdk: tsc (5 packages) + vitest ═══"
cd tssdk
pnpm exec tsc -b packages/mail-core packages/mail packages/dsh-aimail
pnpm exec tsc -p packages/openclaw-aimail/tsconfig.json
pnpm exec tsc -p packages/pi-aimail/tsconfig.json
pnpm test 2>&1 | tail -2

echo "═══ [L0] PASS — all gates green ═══"
