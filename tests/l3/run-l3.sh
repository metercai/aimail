#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# run-l3.sh — L3 对接闭环回归(由 l3.yml 的矩阵驱动)。
#
# 回归内容**完全一致**, 不随 OS/agent 变(D3 裁决):
#   ① 装目标 agent 系统 + 配 LLM(deepseek-flash) + 验证 agent 自身可运行
#   ② 取已发布网关(基础版, GitHub Release 二进制)并起, 捕获 system/admin key
#   ③ 取已发布 CLI(bootstrap.sh 在线 = 用户真实入口)
#   ④ SDK 安装(README 原样命令: 裸 aimail install --home <agent root> / dsh plugin add)
#   ⑤ welcome 闭环: welcome → agent 回三标签 → manager(安全员)approve persona
#   ⑥ 身份断言: SDK 同形 send_mail 一封查询 → whoami 读回身份名片(agent_persona)
#      + 邮件签名(agent_signature) 已生效(whoami.rs:44-52, welcome approve UPSERT 的读回点)
#
# 激活流: 只走 **admin-key**(基础版网关); 不测激活码(高级版)。
# 被测物: 全部来自发布渠道(GitHub Release / PyPI / npm), 绝不来自工作树。
#
# 环境(由 workflow 注入):
#   L3_PLATFORM  linux-amd64|linux-arm64|macos-arm64|windows-amd64
#   L3_AGENT     hermes | dsh-aimail | dsh-plugin
#                (dsh 两条 README 公布路径各一格: aimail install --home ~/.dsh / dsh plugin add)
#   AIMAIL_REPO  aimail 仓 checkout(环境准备单源 + llm-config.py 都在 tests/agent-env/)
#   LLM_BASE_URL / LLM_MODEL / LLM_API_KEY
#
# 流程契约(README Quick Start 场景 4, 原样复现, 严禁自行调整):
#   ③ env 四件套(AIMAIL_GW_URL/ADMIN_KEY/DOMAIN/MANAGER_ADDRESS) + 在线自举 curl|bash
#   ④ aimail install --home <agent root>(裸命令) 或 dsh plugin --profile web add dsh-aimail
#   ⑤ 裸 aimail welcome(AGENT_HOME env → .agentmail 指针解析系统参数)  # contract-allowed: L3 注释引用契约指针文件名
#
# 退出码: 0=绿(闭环完成) · 1=红(某阶段断言失败, 原因已打印) · 2=环境缺前置
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

# 可移植 timeout(macOS runner 无 GNU timeout): 有则用, 无则后台+看门狗, 超时 rc=124 语义同 GNU。
run_timeout() {
  local secs="$1"; shift
  if command -v timeout >/dev/null 2>&1; then
    timeout "$secs" "$@"
  else
    "$@" &
    local pid=$!
    for _ in $(seq 1 "$secs"); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
    if kill -0 "$pid" 2>/dev/null; then kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null; return 124; fi
    wait "$pid"
  fi
}

say() { printf '  %s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m⚠ %s\n' "$*"; }
die() { printf '\033[31m✗ %s\n' "$*" ; exit 1; }
gap() { printf '\033[35m⊘ ENV GAP: %s\n' "$*"; exit 2; }

# 失败取证(owner 纪律: 红必须能"只读日志定因", 不许靠猜、也不靠反复重跑)。
# welcome 不闭环时把**三跳证据**打到 stdout —— CI 用 tee 收 stdout, 故同时进了 job 日志
# 与 l3-logs 工件。三跳 = ①网关投递 ②入站落账(agentmail.log) ③宿主原话(agent host log);
# 另附"SDK 落在哪个解释器"与 hermes 的 platform_toolsets(历史已知缺口: 缺它 ⇒ agent
# 收得到回不出)。此前只打"无回信" ⇒ 只能猜, 实测无法定因。
dump_failure_evidence() {
  local h g f found_am=0 skill pat
  # 契约常量单真源: 检索词里的 SKILL 名**从清单读** —— 硬编码字面量会撞契约字面量棘轮
  # (run-cli-gates 第 1 步 shared 边界扫全仓): 真源 = contract/aimail-contract.json。
  skill="$(python3 - "${AIMAIL_REPO:-}/contract/aimail-contract.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["agent_skill_name"])
PY
)"
  pat="Response for webhook|toolset|not registered|unknown name|send_mail|ImportError|ModuleNotFound"
  [ -n "$skill" ] && pat="$pat|$skill"
  echo "  ────────── 失败取证 dump ──────────"
  for h in "$WORK"/*-host.log; do
    [ -f "$h" ] || continue
    echo "  [③宿主原话] $h (tail -40)"
    tail -80 "$h" | sed 's/^/    /'
    echo "  [③宿主原话: webhook/toolset/邮件工具 关键行]"
    grep -aiE "$pat" "$h" | tail -25 | sed 's/^/    /'
  done
  for g in "$GW_HOME/boot.log" "$GW_HOME"/*.log; do
    [ -f "$g" ] || continue
    echo "  [①网关投递] $g (webhook/deliver/pending 相关行, tail -25)"
    grep -aiE "webhook|deliver|inbound|non_2xx|overlimit|pending" "$g" | tail -25 | sed 's/^/    /'
  done
  # 全文尾: agent 侧若真发出过邮件, 出站记录只在这里能看到(过滤词会漏掉别的措辞)
  [ -f "$GW_HOME/boot.log" ] && { echo "  [①网关投递: boot.log 全文尾 -40]"; tail -40 "$GW_HOME/boot.log" | sed 's/^/    /'; }
  for f in "$HOME"/.aimail/systems/*/*/agentmail.log; do
    [ -f "$f" ] || continue
    found_am=1
    echo "  [②入站落账] $f (tail -30)"
    tail -30 "$f" | sed 's/^/    /'
  done
  # 显式报告"没有 agentmail.log"(此前静默跳过 ⇒ 分不清"文件不存在"与"取样写错")
  [ "$found_am" = 1 ] || echo "  [②入站落账] 不存在 agentmail.log(glob: $HOME/.aimail/systems/*/*/agentmail.log;系统目录: $(ls -d "$HOME"/.aimail/systems/*/ 2>/dev/null | tr '\n' ' '))"
  # 关键不是"系统 python3 有没有 SDK", 而是 **agent 运行时用的那个解释器**有没有 ——
  # 官方 hermes 宿主跑在它自带的 tools/python-*/bin/python3(不在 PATH)上。
  echo "  [SDK 落点] 逐解释器 import 探测:"
  for p in "$(command -v python3)" "$HOME"/.hermes/tools/*/bin/python3 \
           "$HOME"/.hermes/hermes-agent/.venv/bin/python3 "$HOME"/.hermes/hermes-agent/venv/bin/python3 \
           "$HOME"/.hermes/*/venv/bin/python3 "$HOME"/.hermes/*/.venv/bin/python3; do
    [ -x "$p" ] || continue
    printf '    %-62s %s\n' "$p" \
      "$("$p" -c "import importlib.util as u; s=u.find_spec('aimail'); print(s.origin if s else 'NONE')" 2>&1 | tail -1)"
  done
  echo "  [agentmail.log 位置] $(find "$HOME" -name 'agentmail.log' 2>/dev/null | head -5 | tr '\n' ' ')(空 = SDK 从未写过该日志)"
  if [ -n "${HERMES_HOME:-}" ]; then
    # 整块打印(此前 -A3 截断 ⇒ 看不到 webhook: 键在不在, 而缺它正是
    # ensure_config.py:4-11 记的"收得到回不出"根因: webhook 会话回退无 send_mail 的默认工具集)
    echo "  [hermes config] $HERMES_HOME/config.yaml: platform_toolsets / platforms.webhook 整块"
    sed -n '/^platform_toolsets:/,/^[a-z_]*:/p' "$HERMES_HOME/config.yaml" 2>/dev/null | head -30 | sed 's/^/    /'
    sed -n '/^platforms:/,/^[a-z_]*:/p' "$HERMES_HOME/config.yaml" 2>/dev/null | head -30 | sed 's/^/    /'
    grep -aqE "^\s*webhook:" <(sed -n '/^platform_toolsets:/,/^[a-z_]*:/p' "$HERMES_HOME/config.yaml") \
      && echo "    ✓ platform_toolsets.webhook 存在" \
      || echo "    ✗ platform_toolsets.webhook 缺失 —— ensure_config.py:4-11 的已知根因(agent'收得到回不出')"
    # 回信协议注入通道: webhook 路由的 skills 字段(webhook.py:853 按 route skills 注入
    # SKILL 内容进 prompt; 缺契约 skill 名 ⇒ agent 有 send_mail 工具但无回信协议)。
    # 路由表落 {HERMES_HOME}/webhook_subscriptions.json(_ensure_webhook_route 写入)。
    # 路由名/skill 名从契约单一真源读(字面量棘轮: 禁新增硬编码位置)。
    CONTRACT_JSON="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)/contract/aimail-contract.json"
    SUBS="$HERMES_HOME/webhook_subscriptions.json"
    # 两处都可能: 网关运行时读 get_hermes_home()(= $HERMES_HOME), 而
    # _ensure_webhook_route 在 profile_dir 非空时写 profile 目录 —— 两处都要看。
    for SUBS in "$HERMES_HOME/webhook_subscriptions.json" \
                "$HERMES_HOME/profiles/agent/webhook_subscriptions.json"; do
      if [ -f "$SUBS" ]; then
        echo "  [hermes webhook 路由] $SUBS (skills 字段 = 回信协议注入)"
        python3 - "$SUBS" "$CONTRACT_JSON" <<'PY' | sed 's/^/    /'
import json, sys
d = json.load(open(sys.argv[1]))
try:
    c = json.load(open(sys.argv[2]))
    route, skill = c["hermes_route_name"], c["agent_skill_name"]
except Exception:
    route = skill = None
for name, r in d.items():
    print(f"  route {name}: skills={r.get('skills')} preprocess={r.get('preprocess')} deliver={r.get('deliver')}")
    if route and name == route:
        sk = r.get("skills") or []
        if skill in sk:
            print(f"  ✓ route {route} skills 含 {skill} =", sk)
        else:
            print(f"  ✗ route {route} skills={sk} 缺 {skill} —— webhook 会话拿不到回信协议")
PY
      fi
    done
    [ -f "$HERMES_HOME/webhook_subscriptions.json" ] || [ -f "$HERMES_HOME/profiles/agent/webhook_subscriptions.json" ] \
      || echo "  ✗ webhook_subscriptions.json 两处都不存在 —— _ensure_webhook_route 未执行(install 路径断)"
  fi
  # dsh 格取证盲区: 宿主 agent 对话不走 stdout(dsh-host.log 只有启动帧, 历史实证 4 行),
  # 会话持久化在 $DSH_HOME/storages/(workspace.json: global.sessionIds + tables;
  # 单会话在 storages/session_projcache/sessions/<id>.json —— 三层, L3 实证)——
  # dsh 格"③宿主原话"的真实数据源(活实验实锤 dsh 落盘布局)。
  if [ -n "${DSH_HOME:-}" ] && [ -d "$DSH_HOME/storages" ]; then
    echo "  [③ dsh 会话持久化] $DSH_HOME/storages/"
    find "$DSH_HOME/storages" -type f 2>/dev/null | head -10 | sed 's/^/    /'
    # 递归取全部会话 json(workspace + 单会话), 不只顶层两层 —— 单会话文件在
    # session_projcache/sessions/ 第三层, 是 agent 实际 turn 的对话记录。
    while IFS= read -r sf; do
      [ -f "$sf" ] || continue
      sz=$(wc -c < "$sf")
      echo "  [③ dsh 会话] ${sf#"$DSH_HOME"/storages/} ($sz bytes, 全文 ≤12KB / 超则截断)"
      # 会话文件前部=agent turn 对话(消息), 尾部=元数据 —— 必须从前部读,
      # 不能用 tail(历史实证 8KB 会话, tail -40 只抓到元数据, turn 内容全丢)。
      if [ "$sz" -le 12000 ]; then
        cat "$sf" | sed 's/^/    /'
      else
        head -c 12000 "$sf" | sed 's/^/    /'
        echo "    ... (truncated at 12000/$sz bytes)"
      fi
    done < <(find "$DSH_HOME/storages" -type f -name '*.json' 2>/dev/null)
  elif [ -n "${DSH_HOME:-}" ]; then
    echo "  [③ dsh 会话持久化] 不存在 $DSH_HOME/storages (dsh 宿主未落盘 —— 宿主可能从未 boot 到会话层)"
  fi
  # dsh 格 LLM 死因定位: 会话实证 llmMs=0 + response="" = LLM 调用发起前即失败
  # (dsh-llm-deepseek-api-key: 缺 key 时在 adapter 层抛错, 宿主吞错)。
  # 两判据: ①宿主进程环境里 DEEPSEEK_API_KEY 是否真的在(env 传递链实证, 值只判存在);
  # ②同 key 真打一次 /anthropic 端点(401=key 失效, 404=端点漂移, 200=key 有效)。
  if [ -n "${HOST_PID:-}" ] && [ -r "/proc/$HOST_PID/environ" ]; then
    python3 - "/proc/$HOST_PID/environ" <<'PY' | sed 's/^/    /'
import sys
pref = b"LLM_" + b"API_KEY" + b"="   # 拼接: 避免字面量被当 secret 打码
data = open(sys.argv[1], "rb").read().split(b"\0")
v = next((d for d in data if d.startswith(pref) and len(d) > len(pref)), None)
if v:
    print("  [③ dsh LLM] ✓ 宿主进程环境含 LLM_API_KEY(非空, 值不打印)")
else:
    print("  [③ dsh LLM] ✗ 宿主进程环境无 LLM_API_KEY 或为空 —— MISSING_CREDENTIAL, turn 0ms 死的根因")
PY
  else
    echo "  [③ dsh LLM] ✗ 宿主进程 ${HOST_PID:-?} 已死或 environ 不可读 —— 无法判 env 传递"
  fi
  python3 - "$LLM_BASE_URL" "$LLM_MODEL" <<'PY' | sed 's/^/    /'
import os, sys, json, time, urllib.request
key = os.environ.get("LLM_API_KEY", "")
if not key:
    print("  ✗ 取证进程自身无 LLM_API_KEY, 跳过探针"); raise SystemExit
# 探针打 agent 实际调用的端点(OpenAI chat/completions, = llm-pi-ai / hermes custom 同形),
# 不再硬编码 deepseek/anthropic —— 硬编码探针与 agent 真实路径脱节 = 假绿灯盲区。
base = sys.argv[1].rstrip("/")
url = base + "/chat/completions" if base.endswith("/v1") else base + "/v1/chat/completions"
body = json.dumps({"model": sys.argv[2], "max_tokens": 8,
                   "messages": [{"role": "user", "content": "ping"}]}).encode()
req = urllib.request.Request(url, data=body, method="POST",
    headers={"Authorization": "Bearer " + key, "content-type": "application/json"})
t0 = time.time()
try:
    with urllib.request.urlopen(req, timeout=30) as r:
        print(f"  ✓ LLM 探针: HTTP {r.status} in {time.time()-t0:.1f}s (key 有效, 端点通)")
except urllib.error.HTTPError as e:
    frag = (e.read() or b"")[:200].decode(errors="replace")
    print(f"  ✗ LLM 探针: HTTP {e.code} in {time.time()-t0:.1f}s — {frag}")
except Exception as e:
    print(f"  ✗ LLM 探针: {type(e).__name__} in {time.time()-t0:.1f}s — {str(e)[:120]}")
PY
  # 产品自己的判据(历史 L2 里正是它报 '✗ toolset: … platform_toolsets MISSING')
  [ -x "${AIMAIL_BIN:-/nonexistent}" ] && { echo "  [产品自检] aimail check:"; "$AIMAIL_BIN" check 2>&1 | tail -30 | sed 's/^/    /'; }
  echo "  ──────────────────────────────────"
}

PLATFORM="${L3_PLATFORM:?L3_PLATFORM not set}"
AGENT="${L3_AGENT:?L3_AGENT not set}"
AIMAIL_REPO="${AIMAIL_REPO:?AIMAIL_REPO not set}"
# 环境准备单源 = aimail 仓 tests/agent-env/(官方安装 + 基础环境 + 关包管理器年龄闸 + LLM 配置;
# 契约见 tests/agent-env/README.md)。LLM 写入器也只有那一份。
AGENT_ENV="$AIMAIL_REPO/tests/agent-env"
LLMCFG="$AGENT_ENV/lib/llm-config.py"
[ -f "$AGENT_ENV/prepare.sh" ] || die "agent-env/prepare.sh missing at $AGENT_ENV (self-contained in the aimail repo)"
[ -f "$LLMCFG" ] || die "llm-config.py missing at $LLMCFG (self-contained in the aimail repo)"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.xiaomimimo.com/v1}"
LLM_MODEL="${LLM_MODEL:-mimo-v2.6-flash}"
[ -n "${LLM_API_KEY:-}" ] || { echo "✗ LLM_API_KEY secret not set"; exit 2; }

WORK="$(mktemp -d /tmp/l3-XXXXXX)"
GW_HOME="$WORK/gw"
mkdir -p "$GW_HOME/data"
LOG="$WORK/l3.log"
# 日志归档: 失败时 workflow 上传 $HOME/l3-logs(全 OS 确定路径); EXIT trap 兜底落最终日志
LOG_ARCHIVE="${L3_LOG_ARCHIVE:-$HOME/l3-logs}"
mkdir -p "$LOG_ARCHIVE" 2>/dev/null || true
trap 'cp -f "$WORK"/* "$LOG_ARCHIVE/" 2>/dev/null; cp -f "$LOG" "$LOG_ARCHIVE/" 2>/dev/null; true' EXIT

# 资产名平台 → 发布资产后缀
asset_suffix() {
  case "$PLATFORM" in
    linux-amd64)    echo "linux-amd64" ;;
    linux-arm64)    echo "linux-arm64" ;;
    macos-arm64)    echo "macos-arm64" ;;
    windows-amd64)  echo "windows-amd64" ;;
    *) die "unknown platform $PLATFORM" ;;
  esac
}
ASSET="$(asset_suffix)"
EXT=""; [ "$PLATFORM" = "windows-amd64" ] && EXT=".exe"
GW_BIN="$GW_HOME/aimail-gateway$EXT"

echo "════ L3: $PLATFORM / $AGENT  (LLM=$LLM_MODEL @ $LLM_BASE_URL)"

# ── ① 装 agent + 配 LLM + 验证可运行 ─────────────────────────────
echo "── [1/6] agent env: $AGENT"
case "$AGENT" in
  hermes)
    # 官方安装(源码树+venv, **不是 pip**)+ 关年龄闸 + LLM 配置 全在单源 prepare.sh:
    # CLI 适配器的 health_checks 查源码文件 {home}/hermes-agent/gateway/platforms/webhook.py
    # 与 hermes_cli/profiles.py ⇒ pip wheel 布局必挂; hermes 树自己的 pyproject.toml 带
    # `[tool.uv] exclude-newer="14 days"`, uv 从 CWD 向上发现它 ⇒ 装 SDK 会静默落旧版。
    HERMES_HOME="${HOME}/.hermes"
    AGENT_HOME="$HERMES_HOME"
    # LLM 双写(journey J4b 权威形态): --home 落 scratch(verify 读它, 不被宿主进程读到),
    # --extra-home 落真 $HERMES_HOME(宿主进程读它)。
    bash "$AGENT_ENV/prepare.sh" hermes \
      --home "$WORK/llm-scratch" --extra-home "$HERMES_HOME" \
      --llm-url "$LLM_BASE_URL" --llm-model "$LLM_MODEL" --llm-key "$LLM_API_KEY" \
      || die "hermes env prepare failed (single source: tests/agent-env)"
    VERIFY_HOME="$WORK/llm-scratch"
    VERIFY_EXTRA=(--extra-home "$HERMES_HOME")
    ok "hermes env ready (official install + LLM configured; single source)"
    ;;
  dsh|dsh-aimail|dsh-plugin)
    # 官方 npm 装 dsh + pnpm(属 dsh 自身基础环境, 必须预置: 冷容器首调用会联网拉 pnpm,
    # 坏网时探针挂死; CI runner 无 pnpm ⇒ 插件安装必挂)+ profile 预热(profiles/web/
    # cordis.patch.yml 是 LLM 写入与插件安装的落点)+ 关年龄闸 + LLM 配置 = 单源 prepare.sh。
    DSH_HOME="${HOME}/.dsh"
    AGENT_HOME="$DSH_HOME"
    bash "$AGENT_ENV/prepare.sh" dsh --home "$DSH_HOME" \
      --llm-url "$LLM_BASE_URL" --llm-model "$LLM_MODEL" --llm-key "$LLM_API_KEY" \
      || die "dsh env prepare failed (single source: tests/agent-env)"
    VERIFY_HOME="$DSH_HOME"
    VERIFY_EXTRA=()
    ok "dsh env ready (official npm + pnpm + profile warmed + LLM configured; single source)"
    ;;
  *) die "unknown agent $AGENT" ;;
esac

# agent 自身可运行(D3 前置): 探活 LLM 端点可达 + agent 版本可打印
say "verifying agent runs + LLM endpoint reachable…"
python3 "$LLMCFG" verify \
  --platform "$AGENT" --home "$VERIFY_HOME" ${VERIFY_EXTRA[@]+"${VERIFY_EXTRA[@]}"} \
  --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" \
  || warn "agent LLM verify returned non-zero (continuing; welcome will surface the real failure)"
ok "agent env ready: $AGENT"

# ── ② 取已发布基础版网关并起(捕获 key)───────────────────────────
echo "── [2/6] published gateway (basic edition, $PLATFORM)"
# 资产发现主路 = github.com releases/latest 重定向(web 端点, CDN 分发,
# 不受 api.github.com 未认证 IP 限流 60/h 约束)。api.github.com 只作回退:
# 9 格矩阵并发下未认证调用会撞限流(403, 实测 2026-10-09 macos 两格),
# 且限流响应与"资产缺失"必须区分 —— 误报 ENV GAP 会掩盖真死因。
GW_TAG=""
GW_TAG_URL="$(curl -sIL -o /dev/null -w '%{url_effective}' --connect-timeout 15 --max-time 30 \
  "https://github.com/metercai/aimail-gateway/releases/latest" 2>/dev/null)"
case "$GW_TAG_URL" in *"/releases/tag/"*) GW_TAG="${GW_TAG_URL##*/releases/tag/}";; esac
GW_DOWNLOADED=""
if [ -n "$GW_TAG" ]; then
  GW_URL_ASSET="https://github.com/metercai/aimail-gateway/releases/download/${GW_TAG}/aimail-gateway-$ASSET$EXT"
  say "fetching gateway: $GW_URL_ASSET (latest release $GW_TAG)"
  curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 "$GW_URL_ASSET" -o "$GW_BIN" \
    && GW_DOWNLOADED=1 \
    || say "  latest release 无此平台资产/瞬时失败 → 回退 releases API 扫描…"
fi
if [ -z "$GW_DOWNLOADED" ]; then
  GW_URL_ASSET=""
  GW_API_RESP=""
  for attempt in 1 2 3 4 5; do
    GW_API_RESP="$(curl -s --connect-timeout 15 --max-time 60 \
      "https://api.github.com/repos/metercai/aimail-gateway/releases?per_page=30")" || GW_API_RESP=""
    if [ -n "$GW_API_RESP" ] && grep -q '"message"' <<<"$GW_API_RESP"; then
      say "  releases API 限流/错误: $(head -c 120 <<<"$GW_API_RESP") — 重试 ${attempt}/5…"
      sleep $((attempt * 20))
      continue
    fi
    GW_URL_ASSET="$(grep -o '"browser_download_url": *"[^"]*"' <<<"$GW_API_RESP" \
      | sed 's/"browser_download_url": *"//; s/"$//' | grep -m1 "aimail-gateway-$ASSET$EXT\$")"
    break   # API 正常(无论资产在不在): 重试无意义
  done
  [ -n "$GW_URL_ASSET" ] || gap "no aimail-gateway-$ASSET release asset (latest=$GW_TAG, API: $(head -c 100 <<<"$GW_API_RESP") — run the gateway release workflow first)"
  say "fetching gateway: $GW_URL_ASSET"
  curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 "$GW_URL_ASSET" -o "$GW_BIN" \
    || die "gateway download failed"
fi
chmod +x "$GW_BIN"

# 最小 config(本地回环, 端口自选避开宿主进程 —— 见 advanced agent-turn 口径)
GW_HTTP_PORT=18123; GW_SMTP_PORT=12526
cat > "$GW_HOME/config.toml" <<EOF
[http]
bind = "127.0.0.1:${GW_HTTP_PORT}"
hostname = "l3.local"
[smtp]
bind = "127.0.0.1:${GW_SMTP_PORT}"
hostname = "l3.local"
[storage]
path = "${GW_HOME}/data"
[logging]
level = "info"
[admin]
email = "admin@l3.local"
EOF
say "starting gateway…"
"$GW_BIN" --config "$GW_HOME/config.toml" > "$GW_HOME/boot.log" 2>&1 &
GW_PID=$!
# 等 system key 落盘(setup_admin_key 一次性打印 + 持久化)
for i in $(seq 1 30); do
  if ls "$GW_HOME/data"/*.system.key >/dev/null 2>&1; then break; fi
  kill -0 "$GW_PID" 2>/dev/null || die "gateway exited early — boot log: $(tail -5 "$GW_HOME/boot.log")"
  sleep 1
done
SYS_KEY_FILE="$(ls "$GW_HOME/data"/*.system.key 2>/dev/null | head -1)"
[ -n "$SYS_KEY_FILE" ] || die "gateway did not provision system key (see boot.log)"
SYSTEM_ID="$(cat "$GW_HOME/data/system.id" 2>/dev/null | tr -d '[:space:]')"
SYS_KEY="$(tr -d '[:space:]' < "$SYS_KEY_FILE")"
ADMIN_KEY_FILE="$GW_HOME/data/aimail.db.admin_key"
[ -f "$ADMIN_KEY_FILE" ] || ADMIN_KEY_FILE="$(ls "$GW_HOME/data"/*.admin_key 2>/dev/null | head -1)"
ADMIN_KEY="$(tr -d '[:space:]' < "$ADMIN_KEY_FILE" 2>/dev/null)"
GW_URL="http://127.0.0.1:${GW_HTTP_PORT}"
ok "gateway up: $GW_URL (system_id=$SYSTEM_ID)"
say "  system key : ${SYS_KEY:0:8}… (agent-side, install -k)"
say "  admin  key : ${ADMIN_KEY:0:8}… (platform, welcome/approve)"
[ -n "$SYS_KEY" ] && [ -n "$ADMIN_KEY" ] || die "missing gateway key(s)"

# ── ③ 取已发布 CLI(README 场景 4 原样: env 四件套 + 在线自举)────────
# 严格复现 README(130-135 行)公布的用户流程: 先 export 四个 env, 再在线 curl|bash。
# bootstrap.sh 把 env 落盘 ~/.aimail/.env(机器级, config.rs:284 后续 CLI 命令读它),
# ④ 裸 install 与 ⑤ 裸 welcome 靠 .env + 指针文件, 不靠本 shell 的 export 残留。
echo "── [3/6] published CLI (README scenario 4: env + online bootstrap)"
export AIMAIL_GW_URL="$GW_URL"
export AIMAIL_ADMIN_KEY="$SYS_KEY"
export AIMAIL_DOMAIN="l3.local"
# 安全员(manager)= 外部域地址:网关反环路(receiver.rs:232-249)拒绝**发件域已注册**
# 的入站邮件(MAIL FROM 域 = l3.local ⇒ 550)。审批信走 SMTP inbound(base 版
# auth_from=manager),安全员必须用未注册域才能过反环路。l3-mgr.example = RFC 2606
# 保留域,无真实 MX、无真实邮件外发。审批校验(webhook.rs:716)按 sender == install
# 时注册的 manager_address 匹配,换域不影响审批流。
export AIMAIL_MANAGER_ADDRESS="manager@l3-mgr.example"
# arm64 慢 runner:实测冷启动 turn 超默认 120s 回信窗(llmMs=65.9s 仍在飞即超时,
# 2026-10-15 run 38683702165 linux-arm64/dsh-aimail 实证:路径活、非死)⇒ 放宽到 300s。
# macos-arm64 同 arm64 慢 runner:2026-10-16 run 38872198518 macos-arm64/dsh-aimail
# 实证同型 flake——webhook 200 投递成功 + dsh 会话取证证 turn 收工(output 1808
# tokens/step 6/llmRetry 空),turn 落在 (120s,140s] 窗口外 ⇒ 非产品回归。
# 窗口本体在发布版 CLI 内(welcome poll_reply, AIMAIL_REPLY_TIMEOUT env 可配,
# 默认 120 不变)⇒ 本行需配套 CLI 发版才在 L3 生效。
case "${L3_PLATFORM:-}" in
  linux-arm64|macos-arm64) export AIMAIL_REPLY_TIMEOUT=300 ;;
esac
curl -fsSL "https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh" | bash \
  || die "online bootstrap failed (published CLI unavailable)"
AIMAIL_BIN="$(command -v aimail || echo "$HOME/.aimail/bin/aimail")"
[ -x "$AIMAIL_BIN" ] || die "aimail binary not on PATH after bootstrap: $AIMAIL_BIN"
ok "published CLI: $( "$AIMAIL_BIN" version 2>/dev/null | head -1 )"

# ── ④ aimail install / dsh plugin add(README 公布的两种入口)──────
# README: "install either through the aimail command line or through the Agent's
# plugin — pick one of the two"。两种路径都是对外公布的 ⇒ 都测(矩阵 agent 维
# 扩展: hermes / dsh-aimail / dsh-plugin)。命令形态 = README 原样:
#   CLI 入口 = 裸 `aimail install`(home 由 ④ 开头的 export AGENT_HOME 提供,
#   与 ⑤ welcome 共享同一 env; 无 --home flag)。
#   插件入口 = `dsh plugin --profile web add dsh-aimail`(无 AGENT_HOME 依赖)。
# env 已由 ③ 落盘 ~/.aimail/.env + 本 shell export。
echo "── [4/6] SDK install (README command shape)"
export AGENT_HOME="$AGENT_HOME"
case "$AGENT" in
  dsh-plugin)
    # README: dsh plugin --profile web add dsh-aimail (Agent 插件入口)
    DSH_HOME="$DSH_HOME" dsh plugin --profile web add dsh-aimail \
      || die "dsh plugin add dsh-aimail failed (README plugin path)"
    ;;
  dsh-aimail|hermes)
    # README: 裸 aimail install(AGENT_HOME env 提供 home; 与 welcome 共享)
    "$AIMAIL_BIN" install \
      || die "aimail install failed (README CLI path)"
    ;;
esac
ok "install done (system activated + platform adapted)"

# ── ④b 起 agent 宿主(收信回信)────────────────────────────────────
# welcome 闭环需要宿主**活着**: 网关把 welcome 信 POST 到绑定的 webhook_url,
# 宿主的 LLM turn 调 send_mail 回信, CLI 轮询到三标签才 approve。
# 只装不启 ⇒ welcome 120s 超时(首跑必红)。入站端点由 install 的注册步接线,
# 这里只起宿主 + 等端口就绪(等的是**注册时写进绑定的同一个端口**)。
echo "── [4b/6] agent host up (inbound endpoint ready)"
HOST_PID=""
case "$AGENT" in
  hermes)
    # 宿主进程读 $HERMES_HOME/config.yaml(install 注册步已写 platforms.webhook)。
    # webhook 端口 = 注册时 _next_available_webhook_port(基 8644) 写入的 extra.port;
    # gateway 起平台时监听同一端口 ⇒ 从 config 读回, 不猜不硬编码。
    # 纯标准库文本读取(macOS 系统 python3 无 PyYAML): 找顶层 platforms: 块内
    # webhook: 的 extra: 的 port(活跃行, 注释行不认)。install 注册步写的是
    # platforms.webhook.{enabled, extra:{host,port,secret}}(aimail_hermes.py)。
    HERMES_PORT="$(python3 - "$HERMES_HOME/config.yaml" <<'PY'
import re, sys
port = 0
lines = open(sys.argv[1]).read().splitlines()
in_plat = in_wh = in_extra = False
for ln in lines:
    s = ln.strip()
    if s.startswith("#"):
        continue
    if not ln.startswith(" "):
        in_plat = (s == "platforms:")
        in_wh = in_extra = False
        continue
    if in_plat and ln.startswith("  ") and not ln.startswith("   "):
        in_wh = (s == "webhook:")
        in_extra = False
        continue
    if in_wh and ln.startswith("    ") and not ln.startswith("     "):
        if s == "extra:":
            in_extra = True
        elif s.startswith("port:"):
            port = int(re.match(r"port:\s*(\d+)", s).group(1))
            in_extra = False
        else:
            in_extra = False
        continue
    if in_extra and ln.startswith("      ") and s.startswith("port:"):
        port = int(re.match(r"port:\s*(\d+)", s).group(1))
print(port)
PY
)" || die "cannot read hermes webhook port"
    [ "${HERMES_PORT:-0}" -gt 0 ] 2>/dev/null || die "hermes platforms.webhook.port not set (install did not wire the webhook)"
    # launcher 走单真源(tests/agent-env/agent-paths.sh)。此前本块自带一份赋值 + `[ -x ]` 守卫,
    # 2026-10-11 接线到单源 agent-env 时被连带删除, 只留下面的消费者 ⇒ CI 报
    # `HERMES_BIN: unbound variable`(set -u 直接中断; hermes host 从未启动, 端口 8644 永不就绪,
    # L3 9/9 红)。守卫一并恢复: 找不到 launcher 必须响亮失败, 不能静默起一个空命令。
    # shellcheck source=../agent-env/agent-paths.sh
    . "$(dirname "${BASH_SOURCE[0]}")/../agent-env/agent-paths.sh"
    HERMES_BIN="$(agent_launcher hermes "${HERMES_HOME:-}" || true)"
    [ -x "$HERMES_BIN" ] || die "hermes launcher not found after install (searched <home>/hermes-agent/.hermes/bin, /opt/hermes/bin, <home>/.local/bin, PATH)"
    HERMES_HOME="$HERMES_HOME" nohup "$HERMES_BIN" gateway run > "$WORK/hermes-host.log" 2>&1 &
    HOST_PID=$!
    say "hermes host starting (webhook port $HERMES_PORT)…"
    UP=0
    for _ in $(seq 1 120); do
      python3 -c "import socket,sys;s=socket.socket();sys.exit(s.connect_ex(('127.0.0.1',$HERMES_PORT)))" 2>/dev/null && { UP=1; break; }
      kill -0 "$HOST_PID" 2>/dev/null || { tail -15 "$WORK/hermes-host.log" | sed 's/^/    /'; die "hermes host exited before inbound port $HERMES_PORT was ready"; }
      sleep 1
    done
    [ "$UP" = 1 ] || { tail -15 "$WORK/hermes-host.log" | sed 's/^/    /'; die "hermes inbound port $HERMES_PORT not up in 120s"; }
    ok "hermes host up (inbound :$HERMES_PORT)"
    # ── send_mail 工具暴露断言(install 后、welcome 前)──────────────────────
    # send_mail 在 webhook 会话的暴露 = config.yaml 的 platform_toolsets.webhook
    # 含契约 toolset(hermes boot 期读, install 的 ensure_config 负责写入)。
    # 入站端口活着只证明宿主起了, 不证明工具暴露 —— 历史已知根因
    # (ensure_config.py:4-11 "收得到回不出"): 缺它 ⇒ webhook 会话无 send_mail ⇒
    # welcome 触发后 agent 回不了 ⇒ 120s 超时。此处响亮失败, 不让 welcome 替它背锅。
    # dsh 格不需要等价断言: send_mail(tool-mail)与 mail-inbound 是同一原子 bundle
    # (cordis.patch.yml), boot fail-loud ⇒ 上面 V7 入站路由门(路由已挂载)已传递
    # 保证 tool-mail 注册; 重复探针是冗余。
    # toolset 名从契约读(字面量棘轮: 禁硬编码契约值, 同 dump_failure_evidence 取 skill)。
    TS="$(python3 - "${AIMAIL_REPO:-}/contract/aimail-contract.json" <<'PY'
import json, sys
print(json.load(open(sys.argv[1]))["agent_toolset_name"])
PY
)" || die "cannot read agent_toolset_name from contract"
    [ -n "$TS" ] || die "empty agent_toolset_name from contract"
    PT="$(python3 - "$HERMES_HOME/config.yaml" <<'PY'
import sys
# 无 PyYAML(L3 容器 macOS 系统 python3 无)⇒ 纯标准库文本解析, 与上方 port 解析同纪律。
# 兼容三种 yaml.safe_dump 形态: 同级块列表(默认, 2 空格与 webhook: 同级)、
# 嵌套块列表(4 空格)、flow 列表([a, b])。
tools = []
lines = open(sys.argv[1], encoding="utf-8").read().splitlines()
n = len(lines)
i = 0
while i < n and not (lines[i].strip() == "platform_toolsets:" and not lines[i].startswith(" ")):
    i += 1
i += 1  # 进入 platform_toolsets 块
web_indent = None
while i < n:
    ln = lines[i]
    s = ln.strip()
    if not s or s.startswith("#"):
        i += 1
        continue
    ind = len(ln) - len(ln.lstrip(" "))
    if ind == 0:
        break  # 离开 platform_toolsets 块
    if web_indent is None and ind == 2 and s.startswith("webhook:"):
        web_indent = ind
        rest = s[len("webhook:"):].strip()
        if rest.startswith("["):
            tools = [x.strip().strip('"').strip("'") for x in rest.strip("[]").split(",") if x.strip()]
        i += 1
        continue
    if web_indent is not None and s.startswith("- ") and ind >= web_indent:
        tools.append(s[2:].strip().strip('"').strip("'"))
        i += 1
        continue
    if web_indent is not None and ind <= web_indent:
        break  # 下一个同级键(webhook 列表结束)
    i += 1
print(" ".join(tools))
PY
)" || die "cannot read hermes platform_toolsets.webhook from $HERMES_HOME/config.yaml"
    case " $PT " in
      *" $TS "*) : ;;
      *) die "send_mail not exposed to webhook sessions: platform_toolsets.webhook=[$PT] (missing $TS) — install's ensure_config did not wire the toolset; welcome cannot reply" ;;
    esac
    ok "send_mail exposed to webhook sessions (platform_toolsets.webhook ⊇ $TS)"
    ;;
  dsh|dsh-aimail|dsh-plugin)
    # 入站 = dsh-aimail 插件的 mail-inbound server(宿主生命周期自持, server.listen
    # 随宿主起)。注册时 localWebhook = inboundUrl(INBOUND_PORTS.dsh=9099) ⇒ 绑定的
    # webhook_url 端口 = 9099(默认, 除非 AIMAIL_INBOUND_URL/PORT 环境覆盖)。
    DSH_INBOUND_PORT="${AIMAIL_INBOUND_PORT:-9099}"
    DSH_HOME="$DSH_HOME" AIMAIL_HOME="${HOME}/.aimail" AIMAIL_INBOUND_PORT="$DSH_INBOUND_PORT" \
      LLM_API_KEY="$LLM_API_KEY" \
      nohup dsh --profile web --port 0 --no-open > "$WORK/dsh-host.log" 2>&1 &
    HOST_PID=$!
    say "dsh host starting (inbound port $DSH_INBOUND_PORT)…"
    UP=0
    for _ in $(seq 1 120); do
      python3 -c "import socket,sys;s=socket.socket();sys.exit(s.connect_ex(('127.0.0.1',$DSH_INBOUND_PORT)))" 2>/dev/null && { UP=1; break; }
      kill -0 "$HOST_PID" 2>/dev/null || { tail -15 "$WORK/dsh-host.log" | sed 's/^/    /'; die "dsh host exited before inbound port $DSH_INBOUND_PORT was ready"; }
      sleep 1
    done
    [ "$UP" = 1 ] || { tail -15 "$WORK/dsh-host.log" | sed 's/^/    /'; die "dsh inbound port $DSH_INBOUND_PORT not up in 120s"; }
    ok "dsh host up (inbound :$DSH_INBOUND_PORT)"
    # ── 插件入站路由就绪门(V7, L2 同源: J4e flaky 机制 2026-10-13)──────────────
    # 端口监听 ≠ 插件路由挂载: dsh-aimail 插件的 mail-inbound 路由挂载晚于宿主端口,
    # 网关 welcome 首推若落在未挂载窗口 ⇒ 404 ⇒ 3 次重试(5s/15s/40s)耗尽 ⇒ 永久丢
    # (L2 j4e6 实证: 360s 零回信)。L3 每格只跑一次 ⇒ 必须在 welcome 前确保挂载。
    # 判据(L2 journey 同源): 无签 POST 绑定 webhook_url —— handler resolve 先于验签,
    # 空体 ⇒ 200 no_agent = 已挂载; 404 = 宿主在但路由未挂; 000 = 拒连。
    # 探测目标 = 绑定的 webhook_url(网关推 welcome 的同一个 URL, 单一真源, 不猜)。
    # 绑定文件名 = 契约 binding_file 常量;shell 无 import 常量模块,只能字面量 glob。
    DSH_BIND="$(ls "${HOME}/.aimail/systems/${SYSTEM_ID}"/*/agentmail.json 2>/dev/null | head -1)"  # contract-allowed: shell 探针 glob 磁盘真实绑定文件(契约 binding_file);shell 无 import 常量模块,字面量为唯一取法
    [ -n "$DSH_BIND" ] || die "dsh binding not found under ${HOME}/.aimail/systems/${SYSTEM_ID} (install did not write it)"
    DSH_BIND_URL="$(python3 -c "import json;print(json.load(open('$DSH_BIND')).get('webhook_url') or '')")"
    [ -n "$DSH_BIND_URL" ] || die "dsh binding has no webhook_url (pull mode — welcome push cannot be judged)"
    say "dsh inbound route readiness: probing $DSH_BIND_URL (mounted = 200 no_agent; 404 = not mounted)"
    RDY=0; _i=0
    for _i in $(seq 1 120); do
      _code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 3 -X POST "$DSH_BIND_URL" -H 'content-type: application/json' -d '{}')"
      case "$_code" in 200|401|403) RDY=1; break ;; esac
      sleep 2
    done
    [ "$RDY" = 1 ] || { tail -15 "$WORK/dsh-host.log" | sed 's/^/    /'; die "dsh plugin inbound route not mounted in 240s (last probe code=$_code, URL=$DSH_BIND_URL) — welcome would race the gateway's 3 retries"; }
    ok "dsh inbound route mounted (probe code=$_code, 就绪耗时 ≈$((_i * 2))s)"
    ;;
esac

# ── ⑤ welcome 闭环(welcome → agent 回三标签 → manager approve)──
echo "── [5/6] welcome closed loop (manager approves identity card + signature)"
# README 原样: 裸 `aimail welcome`。上下文解析链(welcome.rs:25-91):
#   sid:      AGENT_HOME env(④ 已 export, install 与 welcome 共享)
#             → {AGENT_HOME}/.agentmail 指针(install/插件注册时写)→ 结束;  # contract-allowed: L3 注释引用契约指针文件名
#             用户不知道 system_id, 不传 --system-id。
#   manager:  -m flag → AIMAIL_MANAGER_ADDRESS env(③ 已 export + bootstrap 落盘 .env)
#             → config.manager_address。
#   admin_key: systems/<sid>/aimail_gateway.json(install 写入, welcome.rs:102)。
# 需 agent 的入站端点已起(install 已接线), 且 agent+LLM 能生成三标签回复。
"$AIMAIL_BIN" welcome 2>&1 | tee "$WORK/welcome.log"
WELC_RC=${PIPESTATUS[0]}
[ "$WELC_RC" -eq 0 ] || { dump_failure_evidence; die "welcome closed loop failed (rc=$WELC_RC) — $(tail -12 "$WORK/welcome.log")"; }
ok "welcome loop complete (identity card + signature approved)"

# ── ⑥ 查询邮件签名断言 ─────────────────────────────────────────
echo "── [6/6] query-mail signature assertion"
# 用 agent 绑定里的地址级 key 发一封**查询邮件**(POST /api/v1/send, SDK send_mail 同形,
# v1 签名协议), 然后 whoami 读回网关侧已生效的身份名片(agent_persona)+ 邮件签名
# (agent_signature) —— welcome approve 后 UPSERT 到 domain_addr_meta 的权威断言点
# (whoami.rs:44-52; CLI check/l1 同源)。
PYTHONPATH="$AIMAIL_REPO" python3 - "$GW_URL" "$SYSTEM_ID" "$AIMAIL_MANAGER_ADDRESS" <<'PYEOF' || die "signature assertion failed"
import sys, json, os, time, urllib.request, urllib.error
import hashlib, hmac

gw_url, system_id, manager_addr = sys.argv[1], sys.argv[2], sys.argv[3]

# ── 1) 定位 agent 绑定文件(install 落的单一真源, check/l0 同源) ──
# 布局: ~/.aimail/systems/<sid>/*/agentmail.json —— 字段 email/api_key 必填  # contract-allowed: L3 校验 install 写入契约绑定文件
# (BINDING_REQUIRED, cli/src/core/checks/l0.rs:36)。
home = os.path.expanduser("~/.aimail")
binding = None
base = os.path.join(home, "systems", system_id)
for entry in sorted(os.listdir(base)) if os.path.isdir(base) else []:
    aj = os.path.join(base, entry, "agentmail.json")  # contract-allowed: L3 读契约绑定文件
    if os.path.isfile(aj):
        binding = json.load(open(aj, encoding="utf-8"))
        break
if not binding:
    print(f"  ✗ no agentmail.json under {base} (install did not adapt the platform)"); sys.exit(1)  # contract-allowed: L3 报错引用契约绑定文件名
email, key = binding.get("email", ""), binding.get("api_key", "")
if not email or not key:
    print(f"  ✗ binding missing email/api_key: {list(binding.keys())}"); sys.exit(1)
print(f"  agent address : {email}")

# ── 2) 发一封查询邮件(SDK send_mail 同形: POST /api/v1/send, agent key) ──
# v1 签名协议(aimail_base.py:29-59 精确复刻): HMAC key = sha256(raw_key),
# base = METHOD\npath\nms_timestamp\nsha256(body); 身份头 X-Api-Identity = 地址级 key 的 email。
def signed(method, path, body_obj=None, api_key=key, identity=email):
    body = json.dumps(body_obj).encode() if body_obj is not None else b""
    ts = str(int(time.time() * 1000))
    body_hash = hashlib.sha256(body).hexdigest()
    key_hash = hashlib.sha256(api_key.encode("utf-8")).hexdigest()
    base_str = f"{method.upper()}\n{path}\n{ts}\n{body_hash}"
    sig = hmac.new(key_hash.encode("utf-8"), base_str.encode("utf-8"), hashlib.sha256).hexdigest()
    req = urllib.request.Request(gw_url.rstrip("/") + path, data=body or None, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("X-Api-Identity", identity)
    req.add_header("X-Api-Timestamp", ts)
    req.add_header("X-Api-Signature", sig)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, (e.read() or b"").decode(errors="replace")

marker = f"L3SIG-{int(time.time())}"
st, resp = signed("POST", "/api/v1/send",
                  {"to": manager_addr, "subject": f"query {marker}",
                   "markdown": f"query {marker}: identity + signature check"})
if st not in (200, 201, 202):
    print(f"  ✗ query mail send failed: HTTP {st} {str(resp)[:200]}"); sys.exit(1)
print(f"  query mail sent (HTTP {st}) — persona/signature are applied by the gateway")

# ── 3) 断言: whoami 读回身份名片(agent_persona)+ 邮件签名(agent_signature) ──
# whoami.rs:44-52 —— welcome approve 后网关 UPSERT 到 domain_addr_meta, whoami 读回。
# 这是"身份名片 + 邮件签名已生效"的权威断言点(CLI check/l1 同源, checks/l1.rs:130)。
st, who = signed("GET", "/api/v1/whoami")
if st != 200:
    print(f"  ✗ whoami failed: HTTP {st} {str(who)[:200]}"); sys.exit(1)
persona = (who.get("agent_persona") or "").strip()
sig     = (who.get("agent_signature") or "").strip()
print(f"  agent_persona    : {persona[:60] or '(empty)'}")
print(f"  agent_signature  : {sig[:60] or '(empty)'}")
if not persona or not sig:
    print("  ✗ identity card / signature not in effect after welcome approve"); sys.exit(1)
print("  ✓ identity card (persona) + email signature in effect")
PYEOF

echo
ok "════ L3 PASS: $PLATFORM / $AGENT — CLI×SDK×gateway 对接闭环完成"
# teardown
kill "$GW_PID" 2>/dev/null || true
[ -n "${HOST_PID:-}" ] && kill "$HOST_PID" 2>/dev/null || true
exit 0
