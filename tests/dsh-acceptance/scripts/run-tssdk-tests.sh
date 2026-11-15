#!/usr/bin/env bash
# tssdk + compatibility + static contracts (host Node/pnpm).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"

echo "== tssdk install/build"
cd "$ROOT/tssdk"
pnpm install --frozen-lockfile
pnpm build

echo "== dsh + openclaw + pi tests"
./node_modules/.bin/vitest run \
  packages/agent-aimail/test/dsh \
  packages/agent-aimail/test/openclaw \
  packages/agent-aimail/test/pi

echo "== static contracts"
cd "$ROOT"
python3 tests/contract/check-file-ownership.py
python3 tests/contract/check-zero-bridge.py .
python3 tests/contract/check-contract-single-source.py

echo "== tssdk suite PASS"
