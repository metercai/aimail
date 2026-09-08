#!/usr/bin/env bash
# run-host-regression-openclaw.sh — openclaw 官方镜像内插件回归(方案 A)
#
# 被测:openclaw 平台 + openclaw-aimail 插件(镜像内 node_modules 布局)。
# 与 dsh/pi/hermes 对齐的断言集(5 项):
#   1. openclaw CLI 就位 + 插件包就位
#   2. SKILL 资源随包
#   3. register 链(register-cli/autoBind × 真实网关;exit≠0 时按 restore 指引)
#   4. 绑定落盘(agentmail.json 完整性)
#   5. 幂等重跑(exists 语义)
# 注:openclaw register 走 tssdk openclaw-aimail 的 autoBind(mail-core),
#     与 dsh/pi 同链;gateway cliCommands 的 CLI 面在此以 node 直调注册脚本覆盖。
set -uo pipefail
SID="${TEST_SID:?TEST_SID required}"
GW="${TEST_GW:-http://host.docker.internal:34401}"
MGR="${TEST_MANAGER:-925457@qq.com}"
NM=/app/.openclaw/npm/projects/aimail-regression/node_modules
PASS=0; FAIL=0
ok(){ echo "  ✓ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ✗ $1"; FAIL=$((FAIL+1)); }

# 场景 1:CLI + 插件包
openclaw --version >/dev/null 2>&1 \
  && ok "openclaw CLI 就位($(openclaw --version 2>&1 | head -1))" \
  || bad "openclaw CLI 不可用"
ENTRY="$NM/openclaw-aimail/dist/register-cli.js"
[ -f "$ENTRY" ] || ENTRY="$NM/openclaw-aimail/dist/index.js"
[ -f "$ENTRY" ] && ok "插件包就位($ENTRY)" || { bad "插件包缺失"; exit 1; }

# 场景 2:SKILL 资源随包
[ -f "$NM/openclaw-aimail/resources/skills/SKILL.md" ] \
  && ok "SKILL 资源随包(openclaw-aimail/resources/skills)" \
  || bad "SKILL 资源缺失"

# 场景 3:注册链(autoBind × 真实网关;mail-core 修复版由 /opt/tssdk 覆盖)
cp /opt/tssdk/packages/mail-core/lib/*.js "$NM/@aimail/mail-core/lib/" 2>/dev/null
cp /opt/tssdk/packages/mail/lib/*.js "$NM/@aimail/mail/lib/" 2>/dev/null
EMAIL="agent$(( $(date +%s) % 100000 ))@sdk-e2e.local"  # 每容器唯一(exists 幂等在同容器场景 5 验证)
node -e "
  const { autoBind } = require('$NM/@aimail/mail-core/lib/auto-bind.js');
  autoBind({ systemId: process.env.TEST_SID, email: '$EMAIL',
             webhookUrl: 'http://127.0.0.1:18790/aimail/inbound' })
    .then(r => console.log(JSON.stringify({ ok: true, email: r.email, exists: !!r.exists })))
    .catch(e => console.log(JSON.stringify({ ok: false, error: String(e.message).slice(0, 160) })));
" > /tmp/reg.json 2>/tmp/reg.err
R=$(python3 -c "import json;d=json.load(open('/tmp/reg.json'));print(d.get('ok'), d.get('email',''), 'exists' if d.get('exists') else 'registered')" 2>/dev/null || echo parse-fail)
case "$R" in
  "True "*) ok "注册链通过(${R#True })" ;;
  *) bad "注册失败: ${R#False }" ;;
esac

# 场景 4:绑定落盘
AJ=$(ls /app/.aimail/systems/e2e-smoke/*${EMAIL%%@*}_sdk-e2e.local/agentmail.json 2>/dev/null | head -1)
if [ -n "$AJ" ] && python3 - "$AJ" <<'PY' 2>/dev/null; then
import json,sys
d=json.load(open(sys.argv[1]))
assert d.get("api_key") and d.get("webhook_url") and d.get("email")
PY
  ok "绑定落盘完整($AJ)"
else
  bad "绑定落盘缺失/不完整"
fi

# 场景 5:幂等重跑(exists 短路)
node -e "
  const { autoBind } = require('$NM/@aimail/mail-core/lib/auto-bind.js');
  autoBind({ systemId: process.env.TEST_SID, email: '$EMAIL',
             webhookUrl: 'http://127.0.0.1:18790/aimail/inbound' })
    .then(r => console.log(JSON.stringify({ ok: true, exists: !!r.exists })));
" > /tmp/reg2.json 2>/dev/null
grep -q '"exists":true' /tmp/reg2.json \
  && ok "重跑幂等(exists 短路)" \
  || bad "重跑异常($(head -c 80 /tmp/reg2.json))"

echo "══ 结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
