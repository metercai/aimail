#!/usr/bin/env bash
# Published-gateway lifecycle for the L2/L3 harness.
#
# Both layers test the SDK against a PUBLISHED gateway binary (owner rule 5):
# download the release asset pinned in tests/agent-env/pins.env, verify sha256,
# run it in a throwaway workdir.
#
# provides: gw_up <workdir> [http_port] [smtp_port]   -> exports GW_URL GW_ADMIN GW_SID GW_WORK
#           gw_down
#           gw_pending_rows <email>   (sqlite: pending|acked rows still queued)
#           gw_sql <query>
set -uo pipefail

V2_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIMAIL_ROOT="$(cd "$V2_LIB/../../../.." && pwd)"
PINS="$AIMAIL_ROOT/tests/agent-env/pins.env"

gw_platform() {
  local os arch
  os="$(uname -s)"; arch="$(uname -m)"
  case "$os/$arch" in
    Linux/x86_64) echo linux-amd64 ;;
    Linux/aarch64|Linux/arm64) echo linux-arm64 ;;
    Darwin/arm64) echo macos-arm64 ;;
    MINGW*|MSYS*|CYGWIN*) echo windows-amd64 ;;
    *) echo unsupported ;;
  esac
}

_gw_pin() { grep -E "^$1=" "$PINS" | head -1 | cut -d= -f2-; }

gw_fetch() {
  local plat bin ver sha cache
  plat="$(gw_platform)"
  [ "$plat" != unsupported ] || { echo "GAP: unsupported platform $(uname -s)/$(uname -m)"; return 2; }
  ver="$(_gw_pin GATEWAY_VERSION)"; sha="$(_gw_pin "GATEWAY_SHA256_${plat//-/_}")"
  [ -n "$ver" ] && [ -n "$sha" ] || { echo "GAP: pins.env missing GATEWAY_VERSION/SHA256 for $plat"; return 2; }
  cache="${AIMAIL_GW_CACHE:-$HOME/.cache/aimail-gateway}/$ver"
  mkdir -p "$cache"
  bin="$cache/aimail-gateway-$plat"
  [ "$plat" = windows-amd64 ] && bin="$bin.exe"
  if [ ! -x "$bin" ]; then
    local url="https://github.com/metercai/aimail-gateway/releases/download/${ver}/aimail-gateway-${plat}"
    [ "$plat" = windows-amd64 ] && url="$url.exe"
    curl -fsSL --retry 3 -o "$bin" "$url" || { echo "GAP: download failed $url"; return 2; }
    chmod +x "$bin" 2>/dev/null || true
  fi
  local got; got="$(sha256sum "$bin" | cut -d' ' -f1)"
  [ "$got" = "$sha" ] || { echo "RED: gateway sha256 mismatch ($got != $sha) for $ver/$plat"; return 1; }
  echo "$bin"
}

gw_up() {
  local work="${1:?workdir}" http="${2:-18080}" smtp="${3:-12525}"
  # Fail closed on an occupied port: a stale gateway/sink from a previous run keeps
  # answering /health while the new process dies, so the rest of the fixture then reads
  # DB files from the NEW workdir that the OLD process never wrote
  # ("cat: .../storage/aimail.db.admin_key: No such file or directory"). Observed
  # 2026-11-23 after a background run whose teardown did not fire.
  local p
  for p in "$http" "$smtp" "$((smtp + 1))"; do
    if python3 -c 'import socket,sys; s=socket.socket(); s.settimeout(1); sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1])))==0 else 1)' "$p"; then
      echo "GAP: port $p already in use — a stale gateway/sink is holding it; free it or set GW_HTTP_PORT/GW_SMTP_PORT"; return 2
    fi
  done
  local bin; bin="$(gw_fetch)" || return $?
  mkdir -p "$work/storage" "$work/workspace" "$work/home"
  cat >"$work/config.toml" <<TOML
[http]
bind = "127.0.0.1:$http"
hostname = "test.local"

[smtp]
bind = "127.0.0.1:$((smtp + 1))"
hostname = "test.local"

[storage]
path = "$work/storage"

[relay]
smtp_server = "127.0.0.1:$smtp"

[admin]
email = "admin@test.local"
manager_default = "admin@test.local"

[webhook]
allow_private_targets = true

[retry]
poll_interval_secs = 1
initial_backoff_secs = 1
TOML
  python3 "$AIMAIL_ROOT/tests/host-acceptance/fixture/smtp_sink.py" --port "$smtp" --spool "$work/mail.spool" >"$work/smtp.log" 2>&1 &
  GW_SINK_PID=$!
  local pidf="$work/gateway.pid"
  "$bin" -c "$work/config.toml" --pid-file "$pidf" start >"$work/gateway.log" 2>&1 &
  GW_PID=$!
  # This workdir's own leftover instance (crash without teardown) still holds OUR pid file
  # while its port may already be free, so the port probe above cannot see it. Clear that
  # one and retry. Other runs hold their OWN pid files now, so they are never touched.
  sleep 3
  if grep -q "Server is already running" "$work/gateway.log" 2>/dev/null; then
    _stale="$(sed -nE 's/.*PID: ([0-9]+).*/\1/p' "$work/gateway.log" | head -1)"
    if [ -n "${_stale:-}" ]; then
      echo "  stale gateway PID $_stale holds THIS workdir - terminating it and retrying once"
      kill "$_stale" 2>/dev/null || true
      sleep 2
      "$bin" -c "$work/config.toml" --pid-file "$pidf" start >"$work/gateway.log" 2>&1 &
      GW_PID=$!
    fi
  fi
  local i
  for i in $(seq 1 120); do
    curl -fsS "http://127.0.0.1:$http/health" >/dev/null 2>&1 && break
    sleep 0.5
  done
  curl -fsS "http://127.0.0.1:$http/health" >/dev/null || { echo "GAP: gateway did not come up (see $work/gateway.log)"; return 2; }
  GW_URL="http://127.0.0.1:$http"
  GW_ADMIN="$(cat "$work/storage/aimail.db.admin_key")"
  GW_SID="$(cat "$work/storage/system.id")"
  GW_WORK="$work"; GW_SMTP="$smtp"
  export GW_URL GW_ADMIN GW_SID GW_WORK GW_SMTP
  # offer the test domain through the public admin surface used by the fixture
  python3 "$V2_LIB/gw_admin.py" --gateway "$GW_URL" --key "$GW_ADMIN" --identity "$GW_SID" \
    --system "$GW_SID" --domain test.local >/dev/null 2>&1 || true
  return 0
}

gw_down() {
  # Kill both the children we started and anything else still holding our fixture ports: a
  # leftover instance keeps the binary's own single-instance lock ("Server is already
  # running (PID ...)") and can answer a later probe itself (a stale sink returning 250
  # made openclaw read emails=0 on 2026-11-23). Best-effort, never fatal.
  # Stop through OUR pid file when it exists (the binary's own graceful path), then fall
  # back to the PID/port sweeps below for the crash case.
  if [ -n "${GW_WORK:-}" ] && [ -f "$GW_WORK/gateway.pid" ]; then
    kill "$(cat "$GW_WORK/gateway.pid" 2>/dev/null)" 2>/dev/null || true
  fi
  [ -n "${GW_PID:-}" ] && kill "$GW_PID" 2>/dev/null || true
  [ -n "${GW_SINK_PID:-}" ] && kill "$GW_SINK_PID" 2>/dev/null || true
  sleep 1
  pkill -f "aimail-gateway-linux-amd64 -c ${GW_WORK:-/nonexistent}" 2>/dev/null || true
  pkill -f "smtp_sink.py --port ${GW_SMTP:-0} " 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    pgrep -f "aimail-gateway-linux-amd64 -c ${GW_WORK:-/nonexistent}" >/dev/null 2>&1 || break
    sleep 1
  done
  wait 2>/dev/null || true
}

gw_sql() { sqlite3 "${GW_WORK:-}/storage/aimail.db" "$1" 2>/dev/null; }

# rows still queued for an address (ack removes the row)
gw_pending_rows() { gw_sql "SELECT COUNT(*) FROM pending_deliveries WHERE email='$1';"; }
