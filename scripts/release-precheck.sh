#!/usr/bin/env bash
# Pre-release check (owner 2026-10-07 · R4): a content change in agent-aimail (single package) ⇒ the version must already be bumped
# Usage: release-precheck.sh   → pass rc=0; violation rc=1 (lists the not-bumped package(s))
set -u
cd "$(dirname "$0")/.." || exit 1
ver() { python3 -c "import json,sys;print(json.load(open('tssdk/packages/'+sys.argv[1]+'/package.json'))['version'])" "$1"; }
prev() { t=$(git tag -l "ts-$1-v*" | sort -V | tail -1); [ -n "$t" ] && echo "${t##*v}" || echo ""; }
bad=""
for p in agent-aimail; do
  t=$(git tag -l "ts-$p-v*" | sort -V | tail -1)
  if [ -z "$t" ] || ! git diff --quiet "$t" -- "tssdk/packages/$p"; then
    ch=1; else ch=0; fi
  v=$(ver "$p"); pv=$(prev "$p")
  if [ "$ch" = 1 ] && [ -n "$pv" ] && [ "$v" = "$pv" ]; then bad="$bad $p(changed but not bumped: $v)"; fi
  echo "[pre] $p changed=$ch ver=$v prev=${pv:-none}"
done
# 2026-10-22 single-package merge: the original "core package (mail-core|mail) change ⇒ the other four bump together" section retired (a single package has one version; the main-loop ch check already covers it).
[ -z "$bad" ] && { echo "[pre] PASS"; exit 0; }
echo "[pre] FAIL: R4 violated ⇒$bad"; exit 1
