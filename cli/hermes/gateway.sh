#!/usr/bin/env bash
# hermes_gateway.sh — Ensure Hermes gateway is running with webhook support
# Used by integrate.sh Step 6, and can be run standalone for maintenance.
# Starts gateways for all profiles (root + named) using `hermes -p <name>`.
# Relies on: platforms.webhook already configured in config.yaml

# ── 1. Determine webhook port from root config ──────────────────
WH_PORT=$(python3 -c "
import yaml, os
p = os.path.expanduser('~/.hermes/config.yaml')
try:
    cfg = yaml.safe_load(open(p)) or {}
    print(cfg.get('platforms', {}).get('webhook', {}).get('extra', {}).get('port', 8644))
except: print(8644)
" 2>/dev/null || echo 8644)

_CURL="curl -sf --connect-timeout 2 --max-time 3"

# ── 2. Kill any existing gateway processes ─────────────────────
# Ensures patched webhook.py is loaded on restart。
# 模式必须匹配实际命令行 "venv/bin/python -m hermes_cli.main --profile X
# gateway run"(旧模式含 accept-hooks 永不匹配 → 旧进程永不被杀)。
# [.] 括号技巧避免 pkill 自匹配调用方命令行。
pkill -f "hermes_cli[.]main.*gateway run" 2>/dev/null || true
sleep 2

# ── 3. Start root profile gateway ──────────────────────────────
echo "  Starting Hermes gateway for default profile..."
nohup env HERMES_PROFILE=default hermes gateway run --accept-hooks \
    < /dev/null \
    > "$HOME/.hermes/gateway.log" 2>&1 &
GATEWAY_PID=$!
disown "$GATEWAY_PID" 2>/dev/null || true

ROOT_OK=false
for _ in 1 2 3 4 5 6 7 8; do
    sleep 1
    if $_CURL "http://127.0.0.1:${WH_PORT}/health" >/dev/null 2>&1; then
        ROOT_OK=true
        echo "  ✓ Default gateway started (PID $GATEWAY_PID, port $WH_PORT)"
        break
    fi
done

# ── 4. Start named profile gateways ────────────────────────────
PROFILES_DIR="$HOME/.hermes/profiles"
if [ -d "$PROFILES_DIR" ]; then
    for profile_dir in "$PROFILES_DIR"/*/; do
        [ -d "$profile_dir" ] || continue
        profile_name=$(basename "$profile_dir")

        # Skip profiles without amail identity (pointer file)
        [ -f "$profile_dir/.agentmail" ] || continue

        # Read profile's webhook port from its config.yaml
        PROF_PORT=$(python3 -c "
import yaml
p = '$profile_dir/config.yaml'
try:
    cfg = yaml.safe_load(open(p)) or {}
    print(cfg.get('platforms', {}).get('webhook', {}).get('extra', {}).get('port', 8644))
except: print(8644)
" 2>/dev/null || echo 8644)

        echo "  Starting Hermes gateway for '$profile_name' profile..."
        PROF_LOG="$profile_dir/gateway.log"
        nohup env HERMES_PROFILE="$profile_name" hermes gateway run --accept-hooks --replace \
            < /dev/null \
            > "$PROF_LOG" 2>&1 &
        PROF_PID=$!
        disown "$PROF_PID" 2>/dev/null || true

        PROF_OK=false
        for _ in 1 2 3 4 5 6 7 8; do
            sleep 1
            if $_CURL "http://127.0.0.1:${PROF_PORT}/health" >/dev/null 2>&1; then
                PROF_OK=true
                echo "  ✓ '$profile_name' gateway started (PID $PROF_PID, port $PROF_PORT)"
                break
            fi
        done
        if [ "$PROF_OK" != true ]; then
            echo "  ⚠ '$profile_name' gateway may not be reachable — check $PROF_LOG"
        fi
    done
fi

# ── 5. Sync bridge routes (if bridge is deployed) ──────────────
python3 << 'PYEOF' 2>/dev/null || true
import json, os, urllib.request
from pathlib import Path

# Prefer centralized gateway config via SYSTEM_ID env var
sid = os.environ.get("SYSTEM_ID", "")
if sid:
    gw_cfg_path = os.path.join(os.path.expanduser("~/.agentmail/systems"), sid, "agentmail_gateway.json")
else:
    gw_cfg_path = os.path.join(os.path.expanduser("~/.hermes"), "agentmail_gateway.json")
if not os.path.exists(gw_cfg_path):
    exit(0)

with open(gw_cfg_path) as f:
    gw_cfg = json.load(f)

# bridge admin 端口(bridge_admin_port,默认 38081)。2026-08-18 修订:
# webhook_host 三态(pull=空)不影响路由注册——pull 模式 bridge 拉取后
# 同样按路由表转发,路由表必须有条目。bridge 存在性由 POST 结果判定。
admin_port = int(gw_cfg.get("bridge_admin_port", 38081))
bridge_base = f"http://127.0.0.1:{admin_port}"

# 接收端点一律从 agentmail.json 的 webhook_url 解析(唯一信任源,全 URL
# 含路径;旧 _wh_port 字段已停写,不再 fallback)
sys_dir = os.path.join(os.path.expanduser("~/.agentmail/systems"), sid)
profiles = {}
if sid and os.path.isdir(sys_dir):
    for name in sorted(os.listdir(sys_dir)):
        aj = os.path.join(sys_dir, name, "agentmail.json")
        if not os.path.isfile(aj):
            continue
        with open(aj) as f:
            pf = json.load(f)
        email = pf.get("email", "")
        wu = pf.get("webhook_url", "")
        if email and wu:
            profiles[email] = wu

if not profiles:
    exit(0)

registered = 0
errors = 0
for email, target in profiles.items():
    # port=80 占位(host 为全 URL 时被 bridge 忽略;0 会被参数校验拒绝)
    data = json.dumps({"email": email, "host": target, "port": 80}).encode()
    req = urllib.request.Request(
        f"{bridge_base}/api/v1/routes",
        data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            registered += 1
    except Exception as e:
        errors += 1
        if errors == 1:
            print(f"  ⚠ Bridge route sync failed for {email}: {e}")

if registered > 0:
    print(f"  ✓ Bridge routes synced: {registered} email(s) → {bridge_base}")
PYEOF
