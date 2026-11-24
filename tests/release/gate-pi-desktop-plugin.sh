#!/usr/bin/env bash
# L2 pre-release gate for the PI-Desktop plugin (aimail.desktop).
#
# The .piplug artifact is what a user installs, so the gate judges the ARTIFACT, not the source:
#   build → smoke (real bundle, ack rules) → pack (store-only zip) → re-read the archive
#
# Every step fails closed. The smoke file has two scenarios and the SECOND one is the data-safety
# one: a refused delivery must stay pending (no ack). If that scenario is missing or silent, this
# gate fails — a gate that cannot see the refusal case is not a gate.
#
# Usage: bash tests/release/gate-pi-desktop-plugin.sh [--skip-install]
set -uo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
TSSDK="$REPO/tssdk"
PLUGIN="$TSSDK/packages/aimail-desktop-plugin"
OUT="${PIPLUG_OUT:-$REPO/dist-piplug}"
SKIP_INSTALL=0
[ "${1:-}" = "--skip-install" ] && SKIP_INSTALL=1

step() { printf '\n── %s\n' "$1"; }
ok()   { printf '  ✓ %s\n' "$1"; }
bad()  { printf '  ✗ %s\n' "$1"; FAILED=$((FAILED+1)); }
FAILED=0

step "1/6 install (workspace link for the new plugin package)"
if [ "$SKIP_INSTALL" = "0" ]; then
  # A failed `--offline` attempt followed by a normal install left a half-written node_modules on CI
  # and produced type errors that do not exist in a clean environment (same node/pnpm/lockfile in a
  # container: clean). Install ONCE, from the lockfile, and judge strictly on its exit code.
  (
    cd "$TSSDK" || exit 1
    if [ -f pnpm-lock.yaml ]; then pnpm install --frozen-lockfile; else pnpm install; fi
  ) >/tmp/gate-piplug-install.log 2>&1
  if [ $? -eq 0 ]; then ok "pnpm install --frozen-lockfile"; else bad "pnpm install failed (see /tmp/gate-piplug-install.log)"; fi
else
  ok "install skipped on request"
fi

step "2/6 type check (agent-aimail build + plugin noEmit)"
# Two traps paid for in CI:
#   1. `npx tsc` can resolve a DIFFERENT TypeScript than the workspace's (this repo's lockfile carries
#      both 6.0.3 and 7.0.2). The batch type-checks under 7.0.2 and fails under 6.0.3 with TS18046 —
#      so the gate must use the workspace compiler, and say which version it used.
#   2. `tsc -b` without --force is a no-op when a tsbuildinfo exists: my first local run was "clean"
#      only because the build info was stale. The gate always rebuilds.
TSC_BIN="$TSSDK/node_modules/typescript/bin/tsc"
if [ ! -f "$TSC_BIN" ]; then
  bad "workspace TypeScript missing at $TSC_BIN (install step did not run?)"
else
  TSVER="$(node "$TSC_BIN" --version 2>/dev/null | tail -1)"
  # the package's own build is `node scripts/gen-tool-spec.mjs && tsc -b`; running bare tsc would
  # judge a file set the product never builds
  ( cd "$TSSDK/packages/agent-aimail" && node scripts/gen-tool-spec.mjs >/tmp/gate-piplug-gen.log 2>&1 ) \
    || bad "codegen failed (see /tmp/gate-piplug-gen.log)"
  ( cd "$TSSDK" && node "$TSC_BIN" -b packages/agent-aimail --force >/tmp/gate-piplug-tsc.log 2>&1 )
  if [ $? -eq 0 ]; then ok "tsc -b packages/agent-aimail --force clean ($TSVER)"; else bad "tsc -b failed ($TSVER):"; tail -6 /tmp/gate-piplug-tsc.log; fi
  ( cd "$PLUGIN" && node "$TSC_BIN" -p tsconfig.json --noEmit >/tmp/gate-piplug-tsc2.log 2>&1 )
  if [ $? -eq 0 ]; then ok "plugin tsc --noEmit clean ($TSVER)"; else bad "plugin tsc failed ($TSVER):"; tail -6 /tmp/gate-piplug-tsc2.log; fi
fi

step "3/6 unit tests for this surface (desktop/* + injected fetch)"
( cd "$TSSDK" && node "$TSSDK/node_modules/vitest/vitest.mjs" run packages/agent-aimail/test/desktop \
    packages/agent-aimail/test/core/gateway-injected-fetch.test.ts >/tmp/gate-piplug-vitest.log 2>&1 )
if [ $? -eq 0 ]; then
  ok "$(grep -aE 'Tests +[0-9]+ passed' /tmp/gate-piplug-vitest.log | tail -1 | sed 's/^ *//')"
else
  bad "surface tests failed:"; grep -aE "✗|failed" /tmp/gate-piplug-vitest.log | tail -5
fi

step "4/6 build the bundle"
( cd "$PLUGIN" && node scripts/build.mjs >/tmp/gate-piplug-build.log 2>&1 )
if [ $? -eq 0 ]; then ok "$(tail -1 /tmp/gate-piplug-build.log)"; else bad "build failed:"; tail -5 /tmp/gate-piplug-build.log; fi

step "5/6 smoke: real bundle, both scenarios (incl. refused delivery must NOT ack)"
( cd "$PLUGIN" && node scripts/smoke.mjs >/tmp/gate-piplug-smoke.log 2>&1 )
SMOKE_RC=$?
if [ $SMOKE_RC -eq 0 ] \
   && grep -q "pulled, imported one session" /tmp/gate-piplug-smoke.log \
   && grep -qi "refused delivery stays pending" /tmp/gate-piplug-smoke.log; then
  ok "smoke ok — delivery+ack and refusal+no-ack both observed"
else
  bad "smoke missing an expected scenario (rc=$SMOKE_RC):"
  tail -6 /tmp/gate-piplug-smoke.log
fi

step "6/6 pack .piplug (store-only) and re-read the archive"
rm -rf "$OUT"
python3 "$REPO/tests/release/pack-piplug.py" --dist "$PLUGIN/dist" --out "$OUT" \
  --plug-package "$PLUGIN/package.json" >/tmp/gate-piplug-pack.log 2>&1
if [ $? -eq 0 ]; then
  ok "$(head -1 /tmp/gate-piplug-pack.log | sed 's/^PACK OK //')"
  sed -n '2,3p' /tmp/gate-piplug-pack.log | sed 's/^/    /'
else
  bad "pack failed:"; cat /tmp/gate-piplug-pack.log
fi

echo
if [ "$FAILED" -eq 0 ]; then
  echo "GATE PASS — pi-desktop plugin artifact ready in $OUT"
  ls -l "$OUT" 2>/dev/null | sed 's/^/  /'
  exit 0
else
  echo "GATE FAIL — $FAILED step(s) failed"
  exit 1
fi
