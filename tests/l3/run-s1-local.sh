#!/usr/bin/env bash
# run-s1-local.sh — 场景 1（单 Agent 专属地址）**上线后本地回归**。
#
# 为什么只在本地跑：aimail-advanced 闭源，不能进 GitHub Actions runner。
# 因此场景 1 的发布后回归不进 L3 云矩阵，只在本机（linux）上用两个典型宿主
# （hermes / dsh）跑真实用户链，每次从"获取激活码"开始。
#
# 与 runner 口径一致的部分：官方宿主安装形态、agent 自己的工具门、真实 apply 链。
# 本地化处理：宿主用一份**干净快照**（从本机已有官方安装拷贝，源目录只读），
#             不重复下载 3.6G 宿主树；网关是本地 advanced 真二进制（闭源）。
#
# 用法：
#   bash tests/l3/run-s1-local.sh --cli <已发布 CLI 资产路径> [--adv-bin <advanced>] \
#        [--hosts "hermes dsh"] [--evidence /tmp/s1-local]
# 退出码：0 全 PASS · 1 有 FAIL · 2 CANNOT JUDGE
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"

CLI="${AIMAIL_BIN:-}"
ADV_BIN="${ADV_BIN:-}"
HOSTS="hermes dsh"
EVIDENCE="${S1_EVIDENCE:-/tmp/s1-local}"

while [ $# -gt 0 ]; do
  case "$1" in
    --cli) CLI="${2:-}"; shift 2 ;;
    --adv-bin) ADV_BIN="${2:-}"; shift 2 ;;
    --hosts) HOSTS="${2:-}"; shift 2 ;;
    --evidence) EVIDENCE="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,18p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

if [ -z "$CLI" ] || [ ! -x "$CLI" ]; then
  echo "✗ 需要 --cli <已发布 CLI 资产路径>（上线后回归必须用发布资产，不用本机构建）" >&2
  exit 2
fi
if [ -z "$ADV_BIN" ]; then
  for c in "$REPO/../aimail-advanced/target/release/aimail-advanced" \
           "$REPO/../aimail-advanced/target/debug/aimail-advanced"; do
    [ -x "$c" ] && { ADV_BIN="$c"; break; }
  done
fi
if [ -z "$ADV_BIN" ] || [ ! -x "$ADV_BIN" ]; then
  echo "✗ 找不到 aimail-advanced 二进制（--adv-bin）；闭源件只能本地" >&2
  exit 2
fi

echo "════════════════════════════════════════════════════════════"
echo " 场景1 上线后回归（本地）"
echo "   CLI      : $CLI  ($( sha256sum "$CLI" | cut -c1-12 )…)"
echo "   advanced : $ADV_BIN  ($( sha256sum "$ADV_BIN" | cut -c1-12 )…)"
echo "   hosts    : $HOSTS"
echo "   evidence : $EVIDENCE"
echo "════════════════════════════════════════════════════════════"

overall=0
declare -a rows=()
for h in $HOSTS; do
  log="$EVIDENCE/$h/run.log"
  mkdir -p "$EVIDENCE/$h"
  python3 "$HERE/run-s1-local.py" --host "$h" --adv-bin "$ADV_BIN" --aimail-bin "$CLI" \
      --evidence "$EVIDENCE" > "$log" 2>&1
  rc=$?
  fails=$(python3 - "$EVIDENCE/$h/cases.json" <<'PY' 2>/dev/null || echo "?"
import json,sys
d=json.load(open(sys.argv[1]))
print(sum(1 for c in d["cases"] if not c["ok"]))
PY
)
  case "$rc" in
    0) verdict=PASS ;;
    2) verdict="CANNOT JUDGE" ;;
    *) verdict=FAIL ;;
  esac
  rows+=("$(printf '  %-8s %-14s fail=%s  %s' "$h" "$verdict" "${fails:-?}" "$log")")
  tail -3 "$log" | sed 's/^/    /'
  [ "$rc" -gt "$overall" ] && overall=$rc
done

echo
printf '%s\n' "${rows[@]}"
echo
if [ "$overall" -eq 0 ]; then
  echo "SCENARIO-1 LOCAL REGRESSION: PASS"
else
  echo "SCENARIO-1 LOCAL REGRESSION: FAIL/CANNOT-JUDGE (see $EVIDENCE)"
fi
exit "$overall"
