#!/usr/bin/env bash
# Shared boundary gate (a set **shared** by CLI and SDK): the main criterion is the "boundary/contract" itself, and both sides must satisfy it.
# 2026-10-04 owner adjudication: CLI and SDK gates are separated, each in its place; these four **boundary** checkers are not SDK exclusive,
# listed here separately, called by tests/cli/run-cli-gates.sh and tests/sdk/l0-gate-tests.sh respectively.
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 2
fail=0

echo "[shared] contract single-source: consts == manifest; frontmatter 5/5; literal ratchet"
python3 tests/contract/check-contract-single-source.py || fail=1

echo "[shared] zero-bridge: zero bridge symbols on SDK/reference side"
python3 tests/contract/check-zero-bridge.py . || fail=1

echo "[shared] file-ownership: CLI ↛ bound files / SDK ↛ system env (baseline empty)"
python3 tests/contract/check-file-ownership.py . || fail=1

echo "[shared] docs↔impl: symbols defined; en/zh heading structure equal; contract values == manifest"
python3 tests/contract/check-docs-consistency.py || fail=1

if [ "$fail" = 0 ]; then echo "[shared] PASS"; else echo "[shared] FAIL"; fi
exit "$fail"
