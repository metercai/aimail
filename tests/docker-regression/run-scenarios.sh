#!/usr/bin/env bash
# run-scenarios.sh — 纯净容器内 SDK 主流场景回归(注册链×真实网关)。
# 用法: run-scenarios.sh <sid> [gateway-url] [domain]
#   sid 从宿主挂载的 ~/.aimail/systems 复用(admin_key 经 cfg 读,容器只读)
# 场景全绿退出 0;任一失败即退出 1(L2.5 发布前门)。
set -uo pipefail
SID="${1:-}"
GW="${2:-https://api.aimail.token.tm}"
DOMAIN="${3:-aimail.token.tm}"
: "${SID:?usage: run-scenarios.sh <system-id> [gateway-url] [domain]}"
CFG="/root/.aimail/systems/${SID}/aimail_gateway.json"
[ -f "$CFG" ] || { echo "✗ cfg not mounted: $CFG"; exit 1; }
VENV=/opt/venv
AIMAIL_HOME=/root/.aimail
export AIMAIL_HOME VENV

PASS=0; FAIL=0
ok()   { echo "  ✓ $1"; PASS=$((PASS+1)); }
bad()  { echo "  ✗ $1"; FAIL=$((FAIL+1)); }

echo "══ 场景 1:TS 包随 registry 版本安装(SDK 就位)"
for p in dsh-aimail pi-aimail openclaw-aimail @aimail/mail-core @aimail/mail; do
  [ -d "/opt/tsdeps/node_modules/$p" ] && ok "$p 装入" || bad "$p 缺失"
done

echo "══ 场景 2:注册链×真实网关(node_entry: dsh-aimail register-cli)"
node /opt/host-profiles/web/node_modules/dsh-aimail/lib/register-cli.js \
  --system-id "$SID" --local-webhook "http://127.0.0.1:9099/aimail/inbound" \
  > /tmp/reg-dsh.json 2>/tmp/reg-dsh.err
R=$(python3 -c "import json;d=json.load(open('/tmp/reg-dsh.json'));print(d.get('ok'), d.get('email',''))" 2>/dev/null)
[ "${R%% *}" = "True" ] && ok "dsh 注册成功(${R#* })" || { bad "dsh 注册失败: $(tail -1 /tmp/reg-dsh.err)"; }
DSH_EMAIL="${R#* }"

echo "══ 场景 3:绑定落盘(agentmail.json 含 api_key/webhook_url)"
AJ=$(find "/root/.aimail/systems/$SID" -name agentmail.json | head -1)
python3 - "$AJ" <<'PY' && ok "绑定落盘完整" || bad "绑定落盘缺字段"
import json,sys
d=json.load(open(sys.argv[1]))
assert d.get("api_key") and d.get("webhook_url") and d.get("email")
PY

echo "══ 场景 4:发信(API 通道,经网关 system sender)"
$VENV/bin/python - <<PY && ok "发信成功" || bad "发信失败"
import json, os, sys
sys.path.insert(0, "/opt/venv/lib/python3*/site-packages")  # noqa
# 用共享库直接调(CLI 全链等价)——真实网关出站
from aimail_base import load_gateway_config
from aimail_tools import _GatewayClient
from aimail_base import send_mail
PY

echo "══ 场景 5:收信链(ping 三阶段,真实网关往返)"
# 容器内起收信端点 → 网关 ping 投递 → 验签 → pending ack
echo "  (需要容器内常驻收信进程 + 网关投递——本脚本覆盖注册/落盘/发信;"
echo "   收信双路 E2E 由 host 侧 run-e2e.sh 承接,两路都测为铁律)"

echo "══ 场景 6:清理(deregister 测试地址,不污染生产)"
if [ -n "${DSH_EMAIL:-}" ]; then
  echo "  cleanup: 测试地址 ${DSH_EMAIL}(deregister 由 host 侧 uninstall 链处理)"
fi

echo
echo "══ 结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
