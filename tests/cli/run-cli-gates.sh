#!/usr/bin/env bash
# ▓ CLI domain gate (owner 2026-10-04 ruling: CLI and SDK gates are split, each in its own place)
#
# This script only contains checks where **the main subject is CLI**:
#   1) Shared boundary block (contract single source of truth ratchet / zero-bridge / file ownership / docs<->impl) -- shared by both sides of the boundary, hence invoked as a shared component
#   2) rust battery: fmt --check · clippy -D warnings · test --all-targets (the CLI binary itself)
[ -n "${L2_JOURNEY:-}" ] && export CLI_JOURNEY=1
#   3) CLI host L1/L2 gate: aimail-advanced/tests/cli/ (black-box: rust binary is the test subject)
#
# Platform literal criteria are not in this script: on the Rust side, they are covered by host invariant unit tests in cli/src/hosts/
# + adapters unit tests guard (run with step 2 cargo test), 2026-10-08 remove dead grep for retired Python files.
#
# Not in this script (belongs to SDK domain, see tests/sdk/l0-gate-tests.sh):
#   SDK resources (each package prepacks from staging) · pysdk pyflakes/py_compile · product surface pytest (tests/sdk/) · tssdk build/vitest.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO" || exit 2
fail=0

echo "═══ [CLI] 1) Shared boundary block ═══"
bash tests/shared/shared-boundary-checks.sh || fail=1

echo
echo "═══ [CLI] 2) rust battery (order cannot be swapped: cli tests compare against built artifacts) ═══"
if [ -d cli ]; then
  # All process environment write points hold testutil::ENV_LOCK for serialization (P8 layer 2: product code set_var is zeroed +
  # Added process-level lock to 11 env write cases on the test side) ⇒ parallel safe, removed --test-threads=1 serial masking.
  (cd cli && cargo fmt --all --check \
        && cargo clippy --offline --all-targets -- -D warnings \
        && cargo build --offline \
        && cargo test --offline --all-targets) || fail=1
else
  echo "[CLI] SKIP: no cli/ directory"
fi

echo
echo "═══ [CLI] 2b) parity cases requiring installed components (L2: via python gate calling installed agent-aimail) ═══"
if [ -d cli ]; then
  # Belongs to L2 (component level): dev gate (ci.yml) does not install SDK, so such cases in cli/tests are disabled by feature.
  # Precondition = in-domain interpreter can import agent-aimail (probe_in_domain chain head AIMAIL_PYTHON);
  # Loudly SKIP if missing —— a missing environment is not a product red, same as step 3 for missing advanced.
  _sdk_py=""
  # Same class of criteria (owner 2026-10-11): a same-named directory under CWD is treated as a namespace package ⇒ must require a "real package"
  # (cd / to remove CWD influence + find_spec has origin; namespace package origin=None). Only fix errors, do not loosen.
  for c in "${AIMAIL_PYTHON:-}" python3; do
    [ -n "$c" ] || continue
    if command -v "$c" >/dev/null 2>&1 && ( cd / && "$c" -c "import importlib.util,sys; s=importlib.util.find_spec('aimail'); sys.exit(0 if (s and s.origin) else 1)" ) >/dev/null 2>&1; then _sdk_py="$c"; break; fi
  done
  if [ -z "$_sdk_py" ]; then
    echo "[CLI] SKIP(env): No interpreter available to import agent-aimail -- parity case not run"
  else
    echo "[CLI] interpreter = $_sdk_py (agent-aimail importable)"
    (cd cli && cargo test --offline --features sdk-parity --test repair_backfill_parity) || fail=1
  fi
else
  echo "[CLI] SKIP: no cli/ directory"
fi

echo
echo "═══ [CLI] 2c) registry shape tests (pytest, data source = spec --json black-box) ═══"
# P2c S3b: data source for platform registry shape assertions = `aimail platform spec --json` of the built binary
# (cli/platforms.json is retired). Precondition = step 2 has cargo build (target/debug/aimail in place);
# Depends on pytest + PyYAML (CI pins versions in ci.yml cli-gates job; local missing pytest ⇒ loud failure).
if [ -d cli ] && [ -d tests/cli ] && ls tests/cli/*.py >/dev/null 2>&1; then
  python3 -m pytest tests/cli/ -q || fail=1
fi

echo
echo "═══ [CLI] 3) CLI host L1/L2 gate (black-box, test subject = rust binary) ═══"
if [ -x "$HOME/aimail-advanced/tests/cli/run-cli-gate.sh" ]; then
  (cd "$HOME/aimail-advanced" && bash tests/cli/run-cli-gate.sh) || fail=1
else
  echo "[CLI] SKIP: aimail-advanced/tests/cli/run-cli-gate.sh not found"
fi

echo
if [ "$fail" = 0 ]; then echo "═══ [CLI] PASS — all CLI gates green ═══"; else echo "═══ [CLI] FAIL ═══"; fi
exit "$fail"
