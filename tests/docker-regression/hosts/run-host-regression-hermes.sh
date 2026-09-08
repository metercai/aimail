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

# 场景 2:install(SDK 入口,注册表驱动)。
# 等待容器 s6 首启服务写完源码树(reconcile),避免并发 patch 竞态:
# 等 hermes 源码树稳定(容器 s6 首启服务会写 /opt/hermes;与 install 并发会竞态)
wait_stable() {
  local f="/opt/data/hermes-agent/hermes_cli/webhook.py" prev="" cur=""
  [ -f "$f" ] || { sleep 8; return; }
  prev=$(stat -c %Y "$f" 2>/dev/null || echo 0)
  for i in $(seq 1 20); do
    sleep 2
    cur=$(stat -c %Y "$f" 2>/dev/null || echo 0)
    [ "$cur" = "$prev" ] && return
    prev=$cur
  done
}
wait_stable
mkdir -p "$HERMES_HOME"
if PYTHONPATH="/opt/aimail-src/pysdk${PYTHONPATH:+:$PYTHONPATH}" \
     python3 -c "import sys; sys.path.insert(0, '/opt/aimail-src/pysdk'); from install import main; sys.exit(main(['install', '--type', 'hermes', '--home', '$HERMES_HOME', '--system-id', '$SID', '--manager', '$MGR']))" \
     > /tmp/install.log 2>&1; then
  ok "install 完成(skills 释放+注册链)"
else
  INST_RC=$?
  if [ "$INST_RC" -gt 128 ]; then
    bad "install 信号退出($INST_RC)"
  else
    sleep 5
    if PYTHONPATH="/opt/aimail-src/pysdk${PYTHONPATH:+:$PYTHONPATH}" \
         python3 -c "import sys; sys.path.insert(0, '/opt/aimail-src/pysdk'); from install import main; sys.exit(main(['install', '--type', 'hermes', '--home', '$HERMES_HOME', '--system-id', '$SID', '--manager', '$MGR']))" \
         > /tmp/install.log 2>&1; then
      ok "install 完成(重试后)"
    else
      bad "install 失败(重试后仍非零)"
      echo "── install.log 全文:"
      cat /tmp/install.log
    fi
  fi
fi

# 场景 3:SKILL 释放产物(hermes skills 目录)+ 绑定落盘(cfg 存在时)
SKILL_OK=0
for d in "$HERMES_HOME/profiles"/*/skills/agentmail "$HERMES_HOME/skills/agentmail"; do
  [ -f "$d/SKILL.md" ] && SKILL_OK=1 && break
done
[ "$SKILL_OK" = "1" ] && ok "SKILL 释放在位(profiles/*/skills/agentmail)" \
  || bad "SKILL 释放缺失"
AJ=$(find "$HERMES_HOME" -name agentmail.json 2>/dev/null | head -1)
if [ -z "$AJ" ] && grep -q "no_config" /tmp/install.log 2>/dev/null; then
  echo "  - 绑定落盘跳过(no_config:容器无系统 cfg,注册凭证由 S1 覆盖)"
elif [ -n "$AJ" ] && python3 - "$AJ" <<'PY' 2>/dev/null; then
import json,sys
d=json.load(open(sys.argv[1]))
assert d.get("api_key") and d.get("webhook_url") and d.get("email")
PY
  ok "绑定落盘完整($AJ)"
else
  bad "绑定落盘缺失/不完整"
fi

# 场景 4:幂等重跑
if PYTHONPATH="/opt/aimail-src/pysdk${PYTHONPATH:+:$PYTHONPATH}" \
     python3 -c "import sys; sys.path.insert(0, '/opt/aimail-src/pysdk'); from install import main; sys.exit(main(['install', '--type', 'hermes', '--home', '$HERMES_HOME', '--system-id', '$SID', '--manager', '$MGR']))" \
     > /tmp/install2.log 2>&1; then
  N1=$(find "$HERMES_HOME" -name agentmail.json 2>/dev/null | wc -l)
  ok "install 重跑成功($N1 个地址,幂等)"
else
  bad "install 重跑失败"
fi

echo "══ 结果: PASS=$PASS FAIL=$FAIL"
[ "$FAIL" -eq 0 ]
