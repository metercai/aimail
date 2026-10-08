#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# run-l3.sh — L3 对接闭环回归(单 job, 由 l3-integration.yml 的 8 格矩阵驱动)。
#
# 回归内容**完全一致**, 不随 OS/agent 变(D3 裁决):
#   ① 装目标 agent 系统 + 配 LLM(deepseek-flash) + 验证 agent 自身可运行
#   ② 取已发布网关(基础版, GitHub Release 二进制)并起, 捕获 system/admin key
#   ③ 取已发布 CLI(bootstrap.sh 在线 = 用户真实入口)
#   ④ aimail install --home(admin-key 激活流, 系统级激活 + 平台适配)
#   ⑤ welcome 闭环: welcome → agent 回三标签 → manager(安全员)approve persona
#   ⑥ 查询邮件签名断言: SDK send_mail 一封查询 → 出站记录带 persona 前缀 + 签名
#
# 激活流: 只走 **admin-key**(基础版网关); 不测激活码(高级版)。
# 被测物: 全部来自发布渠道(GitHub Release / PyPI / npm), 绝不来自工作树。
#
# 环境(由 workflow 注入):
#   L3_PLATFORM  linux-amd64|linux-arm64|macos-arm64|windows-amd64
#   L3_AGENT     hermes | dsh
#   AIMAIL_REPO  aimail 仓 checkout
#   ADV_REPO     advanced 仓 checkout(llm-config.py / agent 助手)
#   DEEPSEEK_BASE_URL / DEEPSEEK_MODEL / DEEPSEEK_API_KEY
#
# 退出码: 0=绿(闭环完成) · 1=红(某阶段断言失败, 原因已打印) · 2=环境缺前置
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail

PLATFORM="${L3_PLATFORM:?L3_PLATFORM not set}"
AGENT="${L3_AGENT:?L3_AGENT not set}"
AIMAIL_REPO="${AIMAIL_REPO:?AIMAIL_REPO not set}"
ADV_REPO="${ADV_REPO:?ADV_REPO not set}"
LLM_BASE_URL="${DEEPSEEK_BASE_URL:-https://api.deepseek.com}"
LLM_MODEL="${DEEPSEEK_MODEL:-deepseek-flash}"
[ -n "${DEEPSEEK_API_KEY:-}" ] || { echo "✗ DEEPSEEK_API_KEY secret not set"; exit 2; }

WORK="$(mktemp -d /tmp/l3-XXXXXX)"
GW_HOME="$WORK/gw"
mkdir -p "$GW_HOME/data"
LOG="$WORK/l3.log"
mkdir -p "${TMPDIR:-/tmp}/l3-logs" 2>/dev/null || true
cp "$LOG" "${TMPDIR:-/tmp}/l3-logs/" 2>/dev/null || true

say() { printf '  %s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn(){ printf '  \033[33m⚠ %s\n' "$*"; }
die() { printf '\033[31m✗ %s\n' "$*" ; exit 1; }
gap() { printf '\033[35m⊘ ENV GAP: %s\n' "$*"; exit 2; }

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
    # hermes 是 python 宿主; 官方安装。CI 里用 pip 装官方包(见 advanced host 镜像口径)。
    command -v pip3 >/dev/null 2>&1 || pip3 --version >/dev/null 2>&1 \
      || gap "pip3 missing — cannot install hermes on $PLATFORM"
    pip3 install -q --upgrade hermes-agent || die "hermes-agent pip install failed"
    HERMES_BIN="$(command -v hermes || echo /root/.hermes/bin/hermes)"
    [ -x "$HERMES_BIN" ] || gap "hermes binary not found after install: $HERMES_BIN"
    HERMES_HOME="${HOME}/.hermes"
    AGENT_HOME="$HERMES_HOME"
    # 配 LLM(单源写入器, 与 journey J4b 同一份代码)
    python3 "$ADV_REPO/tests/cli/lib/llm-config.py" write \
      --platform hermes --home "$HERMES_HOME" \
      --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --api-key "$DEEPSEEK_API_KEY" \
      || die "hermes LLM config write failed"
    ok "hermes installed + LLM configured"
    ;;
  dsh)
    command -v npm >/dev/null 2>&1 || gap "npm missing — cannot install dsh on $PLATFORM"
    npm install -g @deepseek-ai/dsh --no-audit --no-fund || die "dsh npm install failed"
    DSH_HOME="${HOME}/.dsh"
    AGENT_HOME="$DSH_HOME"
    mkdir -p "$DSH_HOME/profiles/web"
    # 配 LLM(dsh writer)
    python3 "$ADV_REPO/tests/cli/lib/llm-config.py" write \
      --platform dsh --home "$DSH_HOME" \
      --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" --api-key "$DEEPSEEK_API_KEY" \
      || die "dsh LLM config write failed"
    ok "dsh installed + LLM configured"
    ;;
  *) die "unknown agent $AGENT" ;;
esac

# agent 自身可运行(D3 前置): 探活 LLM 端点可达 + agent 版本可打印
say "verifying agent runs + LLM endpoint reachable…"
python3 "$ADV_REPO/tests/cli/lib/llm-config.py" verify \
  --platform "$AGENT" --home "$AGENT_HOME" --base-url "$LLM_BASE_URL" --model "$LLM_MODEL" \
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

# ── ③ 取已发布 CLI(bootstrap 在线, 用户真实入口)────────────────
echo "── [3/6] published CLI via bootstrap.sh (online)"
bash "$AIMAIL_REPO/scripts/bootstrap.sh" || die "bootstrap.sh failed (published CLI unavailable)"
AIMAIL_BIN="$(command -v aimail || echo "$HOME/.aimail/bin/aimail")"
[ -x "$AIMAIL_BIN" ] || die "aimail binary not on PATH after bootstrap: $AIMAIL_BIN"
ok "published CLI: $( "$AIMAIL_BIN" --version 2>/dev/null | head -1 )"

# ── ④ aimail install(admin-key 激活流)──────────────────────────
echo "── [4/6] aimail install (admin-key activation)"
# -k 传 **system key**(agent 侧, setup.rs Path A: whoami 校验后写入 aimail_gateway.json)。
# --home = 目标 agent 系统根(平台适配入口: hermes webhook / dsh plugin 由 install 自动接线)。
"$AIMAIL_BIN" install \
  --home "$AGENT_HOME" \
  -k "$SYS_KEY" \
  -g "$GW_URL" \
  -m "manager@l3.local" \
  -n "l3-$PLATFORM-$AGENT" \
  || die "aimail install failed (admin-key activation)"
ok "install done (system activated + platform adapted)"

# ── ⑤ welcome 闭环(welcome → agent 回三标签 → manager approve)──
echo "── [5/6] welcome closed loop (manager approves identity card + signature)"
# aimail welcome 自带: admin key 调 /api/v1/system/welcome → 轮询 agent 回复(120s)
# → 解析 persona/signature/current_time → 以 **manager(安全员)身份** 发 approve persona
# → 网关 UPSERT 身份名片 + 邮件签名。
# 需 agent 的入站端点已起(install 已接线), 且 agent+LLM 能生成三标签回复。
export AGENT_HOME="$AGENT_HOME"
export AIMAIL_URL="$GW_URL"
export AIMAIL_MANAGER_ADDRESS="manager@l3.local"
"$AIMAIL_BIN" welcome --system-id "$SYSTEM_ID" --manager "manager@l3.local" 2>&1 | tee "$WORK/welcome.log"
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
exit 0
