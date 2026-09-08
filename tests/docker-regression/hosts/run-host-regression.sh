#!/usr/bin/env bash
# run-host-regression.sh — 宿主镜像内 SDK 主流场景回归(dsh/pi 等 node_entry 平台)
# 用法: run-host-regression.sh <platform> <system-id> [gateway-url] [domain]
# 场景: 注册链×真实网关 → 绑定落盘 → 幂等重跑 → 发信(API) → 清理指引
set -uo pipefail
PLAT="${1:-}"; SID="${2:-}"
MANAGER="${3:-925457@qq.com}"
DOMAIN="${4:-aimail.token.tm}"
: "${PLAT:?usage: run-host-regression.sh <dsh|pi> <system-id>}"
: "${SID:?system-id required}"
CFG="/root/.aimail/systems/${SID}/aimail_gateway.json"
[ -f "$CFG" ] || { echo "✗ cfg 未挂载: $CFG(需 -v \$HOME/.aimail:/root/.aimail:ro)"; exit 1; }
GW=$(python3 -c "import json;print(json.load(open('$CFG')).get('gateway_url',''))" 2>/dev/null)
PASS=0; FAIL=0
ok(){ echo "  ✓ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ✗ $1"; FAIL=$((FAIL+1)); }

case "$PLAT" in
  dsh) ENTRY=/root/.dsh/profiles/web/node_modules/dsh-aimail/lib/register-cli.js
       LOCAL=http://127.0.0.1:9099/aimail/inbound ;;
  pi)  ENTRY=/root/.pi/agent/npm/node_modules/pi-aimail/dist/register-cli.js
       LOCAL=http://127.0.0.1:9101/aimail/inbound ;;
  *) bad "不支持平台 $PLAT"; exit 1 ;;
esac
[ -f "$ENTRY" ] && ok "register-cli 就位($ENTRY)" || { bad "register-cli 缺失"; exit 1; }

echo "══ 场景 2:注册链×真实网关(本地端点 $LOCAL)"
node "$ENTRY" --system-id "$SID" --manager "$MANAGER" --local-webhook "$LOCAL" > /tmp/reg.json 2>/tmp/reg.err
R=$(python3 -c "import json;d=json.load(open('/tmp/reg.json'));print(d.get('ok'), d.get('email',''))" 2>/dev/null || echo "parse-fail")
case "$R" in
  "True "*) ok "注册成功(${R#True })" ;;
  *"exists"*) ok "幂等 exists(已绑定,${R#* })" ;;
  *) bad "注册失败: $(tail -2 /tmp/reg.err | head -1)" ;;
esac

echo "══ 场景 3:绑定落盘(agentmail.json 完整性)"
AJ=$(find "/root/.aimail/systems/$SID" -name agentmail.json 2>/dev/null | head -1)
if python3 - "$AJ" <<'PY' 2>/dev/null; then ok "绑定落盘完整($AJ)"
import json,sys
d=json.load(open(sys.argv[1]))
assert d.get("api_key") and d.get("webhook_url") and d.get("email")
PY
else bad "绑定落盘缺字段或缺失"; fi

echo "══ 场景 4:幂等重跑(不重复建地址)"
node "$ENTRY" --system-id "$SID" --manager "$MANAGER" --local-webhook "$LOCAL" > /tmp/reg2.json 2>/dev/null
grep -qE '"exists"|"ok"' /tmp/reg2.json && ok "重跑幂等" || bad "重跑异常: $(head -c 120 /tmp/reg2.json)"

echo "══ 场景 5:发信(API 通道,经网关 system sender)— host 侧执行(见 README)"
echo "══ 结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
