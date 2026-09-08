#!/usr/bin/env bash
# run-host-regression-hermes.sh — hermes 官方镜像内 SDK 回归(方案 A)
#
# 被测:hermes 平台 + pysdk 安装/注册链。
# 前置(宿主 docker run 注入):
#   -v <aimail repo>:/opt/aimail-src:ro        仓库(pysdk/cli)
#   -v ~/.aimail:/root/.aimail:ro              系统配置(admin_key 复用路径)
#   TEST_SID / TEST_GW / TEST_MANAGER 环境变量
#
# 场景(与 dsh/pi 同基准,4 断言):
#   1. hermes CLI 可用(版本探测)
#   2. pysdk install(hermes home=/opt/data → profiles/*/skills 释放 + 注册链)
#   3. 绑定落盘(agentmail.json 完整性)
#   4. 幂等重跑
set -uo pipefail
PLAT="hermes"
SID="${TEST_SID:?TEST_SID required}"
GW="${TEST_GW:-https://aimail.token.tm}"
MGR="${TEST_MANAGER:-925457@qq.com}"
HERMES_HOME="${HERMES_HOME:-/opt/data}"
PASS=0; FAIL=0
ok(){ echo "  ✓ $1"; PASS=$((PASS+1)); }
bad(){ echo "  ✗ $1"; FAIL=$((FAIL+1)); }

# 场景 1:hermes CLI
if /opt/hermes/bin/hermes --version >/dev/null 2>&1; then
  ok "hermes CLI 就位($(/opt/hermes/bin/hermes --version 2>&1 | head -1))"
else
  bad "hermes CLI 不可用"; exit 1
fi

# SDK import 环境(仓库注入优先)
export PYTHONPATH="/opt/aimail-src/pysdk:/opt/aimail-src/pysdk/hermes${PYTHONPATH:+:$PYTHONPATH}"
python3 -c "import aimail_base" 2>/dev/null \
  && ok "pysdk import 就绪(PYTHONPATH 注入)" \
  || { bad "pysdk import 失败"; exit 1; }

# 场景 2:install(SDK 入口,注册表驱动)
mkdir -p "$HERMES_HOME"
if python3 -m aimail.install install --type hermes --home "$HERMES_HOME" \
     --system-id "$SID" --manager "$MGR" > /tmp/install.log 2>&1; then
  ok "install 完成(skills 释放+注册链)"
  grep -c "✓" /tmp/install.log | xargs -I{} echo "    (install ✓ 项: {})"
else
  bad "install 失败: $(tail -2 /tmp/install.log | head -1)"
fi

# 场景 3:绑定落盘
AJ=$(find "$HERMES_HOME" -name agentmail.json 2>/dev/null | head -1)
if [ -n "$AJ" ] && python3 - "$AJ" <<'PY' 2>/dev/null; then
import json,sys
d=json.load(open(sys.argv[1]))
assert d.get("api_key") and d.get("webhook_url") and d.get("email")
PY
  ok "绑定落盘完整($AJ)"
else
  bad "绑定落盘缺失/不完整"
fi

# 场景 4:幂等重跑
if python3 -m aimail.install install --type hermes --home "$HERMES_HOME" \
     --system-id "$SID" --manager "$MGR" > /tmp/install2.log 2>&1; then
  N1=$(find "$HERMES_HOME" -name agentmail.json 2>/dev/null | wc -l)
  ok "install 重跑成功($N1 个地址,幂等)"
else
  bad "install 重跑失败"
fi

echo "══ 结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
