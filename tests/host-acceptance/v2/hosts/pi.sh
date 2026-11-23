#!/usr/bin/env bash
# Pi host adapter — single-address tssdk host (L2 = dev build pre-installed).
#
# Pi drives one prompt per process; the binding directory is the shared state, so a
# multi-turn journey is a sequence of `pi -p` runs over the same HOME.
# S3 (live delivery) needs a long-lived pi process and is NOT wired here: it reports
# GAP with that reason instead of pretending to be green.
set -uo pipefail
HOST_NAME=pi
PI_IMAGE="${PI_IMAGE:-aimail-host-pi:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_pi_run() {  # _pi_run <out> <prompt>
  local out="$1" prompt="$2"
  local hdir="$out/home${PI_HOME_SUFFIX:+_$PI_HOME_SUFFIX}"
  mkdir -p "$hdir"
  # persistent HOME: pi keeps the binding under /root/.aimail — a fresh container
  # per turn would lose it, so the journey shares one state directory.
  docker run --rm --network host -v "$hdir:/root" \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "PROMPT=$prompt" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$PI_IMAGE" -lc '
      set -u
      export HOME=/root
      python3 - <<PY
import json, os, pathlib
p = pathlib.Path("/root/.pi/agent/models.json")
p.parent.mkdir(parents=True, exist_ok=True)
try:
    d = json.loads(p.read_text())
except Exception:
    d = {"providers": {}}
provs = d.setdefault("providers", {})
if not provs:
    provs["custom-api-xiaomimimo-com"] = {
        "baseUrl": "https://api.xiaomimimo.com/v1",
        "api": "openai-completions",
        "models": [{"id": "mimo-v2.6-flash"}],
    }
for prov in provs.values():
    prov["apiKey"] = os.environ["LLM_API_KEY"]
d.setdefault("defaultModel", "custom-api-xiaomimimo-com/mimo-v2.6-flash")
p.write_text(json.dumps(d, indent=2))
PY
      TGZ="$(ls /opt/sdk/*.tgz | head -1)"
      mkdir -p /tmp/sdk && tar -xzf "$TGZ" -C /tmp/sdk
      pi install /tmp/sdk/package >/tmp/install.log 2>&1 || true
      mkdir -p /root/ws && cd /root/ws
      timeout 420 pi -p "$PROMPT" >/tmp/pi.log 2>&1 || true
      python3 /opt/v2lib/dsh_state.py /root/.aimail count > /out/bindings_seen 2>/dev/null || echo 0 > /out/bindings_seen
      B=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)
      if [ -n "$B" ]; then install -m 644 "$B" /out/binding.json; stat -c %a "$B" > /out/mode; fi
      python3 - <<PY >/out/paused_seen 2>/dev/null || echo None >/out/paused_seen
import glob, json
p = sorted(glob.glob("/root/.aimail/systems/*/*/agentmail.json"))
print(json.load(open(p[0])).get("paused") if p else "no-binding")
PY
      tail -n 200 /tmp/pi.log > /out/last.log 2>/dev/null || true
    ' >"$out/run.log" 2>&1
}

host_journey() {
  local out="$1" addr="$2" code="$3" token="$4"
  mkdir -p "$out"
  _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"$code\",\"email\":\"$addr\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result."
  local maxb; maxb="$(cat "$out/bindings_seen" 2>/dev/null || echo 0)"
  local paused="False"

  if [ -n "${S4_ADDR:-}" ] && [ -n "${S4_CODE:-}" ]; then
    _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"${S4_CODE}\",\"email\":\"${S4_ADDR}\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result."
    local b2; b2="$(cat "$out/bindings_seen" 2>/dev/null || echo 0)"
    [ "${b2:-0}" -gt "${maxb:-0}" ] && maxb="$b2"
  fi

  if [ "${LIFECYCLE:-0}" = "1" ]; then
    _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"pause\",\"email\":\"$addr\"}. Do not ask questions; just call it and report the result."
    paused="$(cat "$out/paused_seen" 2>/dev/null || echo False)"
    _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"unbind\",\"email\":\"$addr\"}. Do not ask questions; just call it and report the result."
  fi

  local final; final="$(cat "$out/bindings_seen" 2>/dev/null || echo "$maxb")"
  printf 'max_bindings=%s paused_seen=%s final_bindings=%s turns=na\n' "$maxb" "$paused" "$final" > "$out/journey_state"
  [ -f "$out/last.log" ] && cp "$out/last.log" "$out/host.log" 2>/dev/null || true
  # binding artefact is captured inside _pi_run (it owns the persistent HOME)
  return 0
}

host_s1_activate() { host_journey "$@"; }
host_s3_delivery() { :; }
host_stop() { :; }

host_negative() {
  local out="$1" addr="$2"
  PI_HOME_SUFFIX=neg _pi_run "$out" "Call the aimail_address tool now with exactly this JSON: {\"action\":\"bind\",\"code\":\"addr-0000-deadbeef\",\"email\":\"$addr\",\"gateway_url\":\"${GW_URL}\"}. Do not ask questions; just call it and report the result."
  cp "$out/bindings_seen" "$out/bindings_count" 2>/dev/null || echo 0 > "$out/bindings_count"
  cp "$out/last.log" "$out/act.log" 2>/dev/null || true
  return 0
}
