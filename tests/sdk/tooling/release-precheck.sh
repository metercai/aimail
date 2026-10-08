#!/usr/bin/env bash
# 发布前校验（owner 2026-10-07 · R4）：核心包(mail-core|mail)任一有内容变动 ⇒ 五个 tssdk 包**必须都已升版**
# 用法: release-precheck.sh   → 通过 rc=0；违反 rc=1（逐包列出"未升版"的包）
set -u
cd "$(dirname "$0")/../../.." || exit 1
ver() { python3 -c "import json,sys;print(json.load(open('tssdk/packages/'+sys.argv[1]+'/package.json'))['version'])" "$1"; }
prev() { t=$(git tag -l "ts-$1-v*" | sort -V | tail -1); [ -n "$t" ] && echo "${t##*v}" || echo ""; }
core_changed=0; bad=""
for p in mail-core mail dsh-aimail openclaw-aimail pi-aimail; do
  t=$(git tag -l "ts-$p-v*" | sort -V | tail -1)
  if [ -z "$t" ] || ! git diff --quiet "$t" -- "tssdk/packages/$p"; then
    ch=1; else ch=0; fi
  v=$(ver "$p"); pv=$(prev "$p")
  [ "$ch" = 1 ] && case "$p" in mail-core|mail) core_changed=1;; esac
  if [ "$ch" = 1 ] && [ -n "$pv" ] && [ "$v" = "$pv" ]; then bad="$bad $p(有改动但未升版: $v)"; fi
  echo "[pre] $p changed=$ch ver=$v prev=${pv:-none}"
done
if [ "$core_changed" = 1 ]; then
  for p in mail dsh-aimail openclaw-aimail pi-aimail; do
    v=$(ver "$p"); pv=$(prev "$p")
    [ -n "$pv" ] && [ "$v" = "$pv" ] && bad="$bad $p(核心包变动但未升版: $v)"
  done
fi
[ -z "$bad" ] && { echo "[pre] PASS"; exit 0; }
echo "[pre] FAIL: 违反 R4 ⇒$bad"; exit 1
