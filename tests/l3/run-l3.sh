#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# run-l3.sh — L3 对接闭环回归(单 job, 由 l3-integration.yml 的 8 格矩阵驱动)。
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
#   AIMAIL_REPO  aimail 仓 checkout(llm-config.py 已自包含在 tests/l3/)
#   DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_API_KEY
#
# 流程契约(README Quick Start 场景 4, 原样复现, 严禁自行调整):
#   ③ env 四件套(AIMAIL_URL/ADMIN_KEY/DOMAIN/MANAGER_ADDRESS) + 在线自举 curl|bash
#   ④ aimail install --home <agent root>(裸命令) 或 dsh plugin --profile web add dsh-aimail
#   ⑤ 裸 aimail welcome(AGENT_HOME env → .agentmail 指针解析系统参数)
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

PLATFORM="${L3_PLATFORM:?L3_PLATFORM not set}"
AGENT="${L3_AGENT:?L3_AGENT not set}"
AIMAIL_REPO="${AIMAIL_REPO:?AIMAIL_REPO not set}"
LLMCFG="$AIMAIL_REPO/tests/l3/llm-config.py"
[ -f "$LLMCFG" ] || die "llm-config.py missing at $LLMCFG (self-contained in the aimail repo)"
LLM_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
LLM_MODEL="${DEEPSEEK_MODEL:-deepseek-flash}"
[ -n "${DEEPSEEK_API_KEY:-}" ] || { echo "✗ DEEPSEEK_API_KEY secret not set"; exit 2; }

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
    # 官方 install.sh(源码 clone 到 $HERMES_HOME/hermes-agent + venv)——**不是 pip**:
    # CLI 适配器的 health_checks 查源码文件 {home}/hermes-agent/gateway/platforms/webhook.py
    # (PREPROCESS_REGISTRY, install 打的 patch) 与 hermes_cli/profiles.py (AimailGateway 钩子),
    # pip wheel 布局没有这些文件 ⇒ install 必挂。无 TTY 时 install.sh 自动跳过 setup/gateway。
    HERMES_HOME="${HOME}/.hermes"
    AGENT_HOME="$HERMES_HOME"
    curl -fsSL --retry 2 --connect-timeout 20 --max-time 60 https://hermes-agent.nousresearch.com/install.sh \
      -o "$WORK/hermes-install.sh" || die "hermes install.sh download failed"
    HERMES_HOME="$HERMES_HOME" bash "$WORK/hermes-install.sh" </dev/null \
      || die "hermes official install failed (see $WORK)"
    # 无 profiles 脚手架: B4 实证(干净 HERMES_HOME 无 profiles/ + webhook.enabled=true
    # → hermes 默认 profile gateway 3s 起, 端口可达) —— hermes agent 不依赖 profiles/。
    # CLI detect all-of [hermes-agent, profiles] 是产品适配缺失(缺陷清单 C-3), 不绕。
    HERMES_BIN="$HERMES_HOME/hermes-agent/.hermes/bin/hermes"
    [ -x "$HERMES_BIN" ] || gap "hermes binary not found after official install: $HERMES_BIN"
    # LLM 配置在 install.sh **之后**(stage_config 会覆写 config.yaml; 写早了被冲掉)。
    # 双写(journey J4b 权威形态): --home 落 scratch(verify 读它, 不被宿主进程读到),
    # --extra-home 落真 $HERMES_HOME(宿主进程读它)。
    python3 "$LLMCFG" write \
      --platform hermes --home "$WORK/llm-scratch" --extra-home "$HERMES_HOME" \
      --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --api-key "$DEEPSEEK_API_KEY" \
      || die "hermes LLM config write failed"
    VERIFY_HOME="$WORK/llm-scratch"
    VERIFY_EXTRA=(--extra-home "$HERMES_HOME")
    ok "hermes installed (official, source+venv) + LLM configured"
    ;;
  dsh|dsh-aimail|dsh-plugin)
    command -v npm >/dev/null 2>&1 || gap "npm missing — cannot install dsh on $PLATFORM"
    npm install -g @deepseek-ai/dsh --no-audit --no-fund || die "dsh npm install failed"
    # pnpm 预热: dsh 插件管理器(dsh plugin add)转发 pnpm 装插件落 profile。
    # pnpm 属 dsh 自身基础环境, 必须预置(journey Dockerfile.dsh / r43 教训: 冷容器
    # 首调用联网拉 pnpm, 坏网时探针挂死)。CI runner 无 pnpm ⇒ ④ plugin add 必挂。
    command -v pnpm >/dev/null 2>&1 || npm install -g pnpm --no-audit --no-fund >/dev/null 2>&1
    command -v pnpm >/dev/null 2>&1 || die "pnpm unavailable — dsh plugin install needs pnpm on PATH"
    say "pnpm $(pnpm --version 2>/dev/null | head -1)"
    DSH_HOME="${HOME}/.dsh"
    AGENT_HOME="$DSH_HOME"
    # profile warmup(实测: dsh 首启 --profile 才创建 profiles/web/{cordis.yml,cordis.patch.yml}):
    # ① write_dsh 要求 profile 已存在(no dsh profile — cannot judge)
    # ② install 的 dsh 步 `dsh plugin --profile web add dsh-aimail` 要往 profile 里装插件。
    # 无 storages 脚手架: 官方 warmup 不建 storages(E1 实证), dsh 运行期 12s 自建(Q3 实证);
    # CLI detect all-of [profiles, storages] 是产品适配缺失(缺陷清单 C-3 同类), 不绕。
    DSH_HOME="$DSH_HOME" run_timeout 120 dsh --profile web --dump-config >/dev/null 2>&1 \
      || die "dsh profile warmup failed (profiles/web not created)"
    [ -f "$DSH_HOME/profiles/web/cordis.patch.yml" ] || die "dsh warmup did not create cordis.patch.yml"
    # LLM 配置: write_dsh 往 profile 的 cordis.patch.yml 追加 - id: llm-deepseek
    # (baseURL/model/apiKeyEnv: DEEPSEEK_API_KEY)——env 名在此, key 值在宿主进程 env(起宿主时 export)。
    python3 "$LLMCFG" write \
      --platform dsh --home "$DSH_HOME" \
      --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --api-key "$DEEPSEEK_API_KEY" \
      || die "dsh LLM config write failed"
    VERIFY_HOME="$DSH_HOME"
    VERIFY_EXTRA=()
    ok "dsh installed (npm) + profile warmed + LLM configured"
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
GW_URL_ASSET="$(curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 \
  "https://api.github.com/repos/metercai/aimail-gateway/releases?per_page=30" \
  | grep -o '"browser_download_url": *"[^"]*"' | sed 's/"browser_download_url": *"//; s/"$//' \
  | grep -m1 "aimail-gateway-$ASSET$EXT\$" )"
[ -n "$GW_URL_ASSET" ] || gap "no aimail-gateway-$ASSET release asset — run the gateway release workflow first"
say "fetching gateway: $GW_URL_ASSET"
curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 "$GW_URL_ASSET" -o "$GW_BIN" \
  || die "gateway download failed"
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
export AIMAIL_URL="$GW_URL"
export AIMAIL_ADMIN_KEY="$SYS_KEY"
export AIMAIL_DOMAIN="l3.local"
export AIMAIL_MANAGER_ADDRESS="manager@l3.local"
curl -fsSL "https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh" | bash \
  || die "online bootstrap failed (published CLI unavailable)"
AIMAIL_BIN="$(command -v aimail || echo "$HOME/.aimail/bin/aimail")"
[ -x "$AIMAIL_BIN" ] || die "aimail binary not on PATH after bootstrap: $AIMAIL_BIN"
ok "published CLI: $( "$AIMAIL_BIN" version 2>/dev/null | head -1 )"

# ── ④ aimail install / dsh plugin add(README 公布的两种入口)──────
# README: "install either through the aimail command line or through the Agent's
# plugin — pick one of the two"。两种路径都是对外公布的 ⇒ 都测(矩阵 agent 维
# 扩展: hermes / dsh-aimail / dsh-plugin)。命令形态 = README 原样(裸命令,
# env 已由 ③ 落盘 ~/.aimail/.env + 本 shell export)。
echo "── [4/6] SDK install (README command shape)"
case "$AGENT" in
  dsh-plugin)
    # README 96 行: dsh plugin --profile web add dsh-aimail (Agent 插件入口)
    DSH_HOME="$DSH_HOME" dsh plugin --profile web add dsh-aimail \
      || die "dsh plugin add dsh-aimail failed (README plugin path)"
    ;;
  dsh-aimail|hermes)
    # README 90 行: aimail install --home <agent root> (CLI 入口; 裸命令, 无 -k/-g/-m/-n)
    "$AIMAIL_BIN" install --home "$AGENT_HOME" \
      || die "aimail install --home failed (README CLI path)"
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
    HERMES_PORT="$(python3 - "$HERMES_HOME/config.yaml" <<'PY'
import sys, yaml
c = yaml.safe_load(open(sys.argv[1])) or {}
wh = c.get("platforms", {}).get("webhook", {})
p = wh.get("extra", {}).get("port") or wh.get("port")
print(int(p) if p else 0)
PY
)" || die "cannot read hermes webhook port"
    [ "${HERMES_PORT:-0}" -gt 0 ] 2>/dev/null || die "hermes platforms.webhook.port not set (install did not wire the webhook)"
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
    ;;
  dsh|dsh-aimail|dsh-plugin)
    # 入站 = dsh-aimail 插件的 mail-inbound server(宿主生命周期自持, server.listen
    # 随宿主起)。注册时 localWebhook = inboundUrl(INBOUND_PORTS.dsh=9099) ⇒ 绑定的
    # webhook_url 端口 = 9099(默认, 除非 AIMAIL_INBOUND_URL/PORT 环境覆盖)。
    DSH_INBOUND_PORT="${AIMAIL_INBOUND_PORT:-9099}"
    DSH_HOME="$DSH_HOME" AIMAIL_HOME="${HOME}/.aimail" AIMAIL_INBOUND_PORT="$DSH_INBOUND_PORT" \
      DEEPSEEK_API_KEY="$DEEPSEEK_API_KEY" \
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
    ;;
esac

# ── ⑤ welcome 闭环(welcome → agent 回三标签 → manager approve)──
echo "── [5/6] welcome closed loop (manager approves identity card + signature)"
# README 104 行原样: 裸 `aimail welcome`。上下文解析链(welcome.rs:25-91):
#   sid:      AGENT_HOME env → {AGENT_HOME}/.agentmail 指针(install/插件注册时写)
#             → 自动判定; 用户不知道 system_id, 不传 --system-id。
#   manager:  -m flag → AIMAIL_MANAGER_ADDRESS env(③ 已 export + bootstrap 落盘 .env)
#             → config.manager_address。
#   admin_key: systems/<sid>/aimail_gateway.json(install 写入, welcome.rs:102)。
# 需 agent 的入站端点已起(install 已接线), 且 agent+LLM 能生成三标签回复。
export AGENT_HOME="$AGENT_HOME"
"$AIMAIL_BIN" welcome 2>&1 | tee "$WORK/welcome.log"
WELC_RC=${PIPESTATUS[0]}
[ "$WELC_RC" -eq 0 ] || die "welcome closed loop failed (rc=$WELC_RC) — $(tail -12 "$WORK/welcome.log")"
ok "welcome loop complete (identity card + signature approved)"

# ── ⑥ 查询邮件签名断言 ─────────────────────────────────────────
echo "── [6/6] query-mail signature assertion"
# 用 agent 绑定里的地址级 key 发一封**查询邮件**(POST /api/v1/send, SDK send_mail 同形,
# v1 签名协议), 然后 whoami 读回网关侧已生效的身份名片(agent_persona)+ 邮件签名
# (agent_signature) —— welcome approve 后 UPSERT 到 domain_addr_meta 的权威断言点
# (whoami.rs:44-52; CLI check/l1 同源)。
PYTHONPATH="$AIMAIL_REPO" python3 - "$GW_URL" "$SYSTEM_ID" <<'PYEOF' || die "signature assertion failed"
import sys, json, os, time, urllib.request, urllib.error
import hashlib, hmac

gw_url, system_id = sys.argv[1], sys.argv[2]

# ── 1) 定位 agent 绑定文件(install 落的单一真源, check/l0 同源) ──
# 布局: ~/.aimail/systems/<sid>/*/agentmail.json —— 字段 email/api_key 必填
# (BINDING_REQUIRED, cli/src/core/checks/l0.rs:36)。
home = os.path.expanduser("~/.aimail")
binding = None
base = os.path.join(home, "systems", system_id)
for entry in sorted(os.listdir(base)) if os.path.isdir(base) else []:
    aj = os.path.join(base, entry, "agentmail.json")
    if os.path.isfile(aj):
        binding = json.load(open(aj, encoding="utf-8"))
        break
if not binding:
    print(f"  ✗ no agentmail.json under {base} (install did not adapt the platform)"); sys.exit(1)
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
                  {"to": "manager@l3.local", "subject": f"query {marker}",
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
