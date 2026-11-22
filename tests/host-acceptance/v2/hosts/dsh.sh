#!/usr/bin/env bash
# DSH host adapter (containerised, pinned image; L2 = dev build pre-installed).
#
# Contract: implements the same 3 entry points as every host adapter, so case
# scripts stay host-agnostic:
#   host_s1_activate <outdir> <addr> <code>   -> writes $outdir/{binding.json,host.log,rc}
#   host_s2_arming   <outdir>                 -> restarts the host, greps the arming log
#   host_stop                                  -> no-op for the one-shot container model
set -uo pipefail

HOST_NAME=dsh
DSH_IMAGE="${DSH_IMAGE:-aimail-host-dsh:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

_dsh_prompt() {
  printf 'Activate and bind my AIMail mailbox address %s with activation code %s at gateway %s using aimail_address.' "$1" "$2" "${GW_URL:-http://127.0.0.1:18080}"
}

# one container: install dev build, boot web host, drive the in-host bind, restart, arm
host_s1_activate() {
  local out="$1" addr="$2" code="$3"
  mkdir -p "$out"
  local prompt; prompt="$(_dsh_prompt "$addr" "$code")"
  docker run --rm --network host \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "APROMPT=$prompt" \
    -v "$V2_ROOT/lib:/opt/v2lib:ro" -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$DSH_IMAGE" -lc '
      set -u
      export HOME=/root
      mkdir -p /opt/sdkx && tar -xzf /opt/sdk/*.tgz -C /opt/sdkx
      dsh plugin --profile web add /opt/sdkx/package >/tmp/plugin.log 2>&1 || { echo "plugin add failed"; cat /tmp/plugin.log; exit 3; }
      setsid nohup dsh --profile web >/tmp/web.log 2>&1 &
      URL=""
      for i in $(seq 1 120); do
        URL=$(grep -aoE "http://127\.0\.0\.1:[0-9]+/\?token=[A-Za-z0-9_-]+" /tmp/web.log | head -1)
        [ -n "$URL" ] && break; sleep 1
      done
      [ -n "$URL" ] || { echo "no web url"; tail -20 /tmp/web.log; exit 4; }
      python3 /opt/v2lib/dsh_activate.py "$URL" /root/ws "$APROMPT" >/tmp/act.log 2>&1
      for i in $(seq 1 60); do ls /root/.aimail/systems/*/*/agentmail.json >/dev/null 2>&1 && break; sleep 2; done
      B=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)
      echo "binding=${B:-none}"
      [ -n "$B" ] && { stat -c %a "$B" > /out/mode; install -m 644 "$B" /out/binding.json; }
      tail -n 400 /tmp/web.log > /out/host.log 2>/dev/null || true
      [ -n "$B" ] || tail -n 80 /tmp/act.log > /out/act.log 2>/dev/null || true
      # S2: restart so the binding arms the pull
      pkill -f "dsh --profile web" 2>/dev/null; sleep 2
      setsid nohup dsh --profile web >>/tmp/web2.log 2>&1 &
      sleep 25
      tail -n 400 /tmp/web2.log >> /out/host.log 2>/dev/null || true
      [ -n "$B" ]
    ' >"$out/rc" 2>&1
  echo $? >"$out/code"
  return 0
}

host_s2_arming() { :; }   # restart happens inside host_s1_activate
host_stop() { :; }
