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
  "$bin" -c "$work/config.toml" start >"$work/gateway.log" 2>&1 &
  GW_PID=$!
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
  [ -n "${GW_PID:-}" ] && kill "$GW_PID" 2>/dev/null || true
  [ -n "${GW_SINK_PID:-}" ] && kill "$GW_SINK_PID" 2>/dev/null || true
  wait 2>/dev/null || true
}

gw_sql() { sqlite3 "${GW_WORK:-}/storage/aimail.db" "$1" 2>/dev/null; }

# rows still queued for an address (ack removes the row)
gw_pending_rows() { gw_sql "SELECT COUNT(*) FROM pending_deliveries WHERE email='$1';"; }
