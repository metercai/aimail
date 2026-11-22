#!/usr/bin/env bash
# Pi host adapter (containerised, pinned image; L2 = dev build pre-installed).
set -uo pipefail
HOST_NAME=pi
PI_IMAGE="${PI_IMAGE:-aimail-host-pi:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

host_s1_activate() {
  local out="$1" addr="$2" code="$3"
  mkdir -p "$out"
  docker run --rm --network host \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL:-http://127.0.0.1:18080}" \
    -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$PI_IMAGE" -lc '
      set -u
      export HOME=/root; mkdir -p /root/ws
      python3 - <<PY
import json,os,pathlib
p=pathlib.Path("/root/.pi/agent/models.json"); d=json.loads(p.read_text())
for prov in (d.get("providers") or {}).values(): prov["apiKey"]=os.environ["LLM_API_KEY"]
p.write_text(json.dumps(d,indent=2))
PY
      mkdir -p /opt/sdkx && tar -xzf /opt/sdk/*.tgz -C /opt/sdkx
      pi install /opt/sdkx/package >/tmp/plugin.log 2>&1 || { echo "pi install failed"; tail -20 /tmp/plugin.log; exit 3; }
      cd /root/ws
      timeout 300 pi -p "Activate and bind my AIMail mailbox address $AH with activation code $AC at gateway $GWURL using aimail_address." >/tmp/act.log 2>&1 || true
      B=$(ls /root/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)
      echo "binding=${B:-none}"
      [ -n "$B" ] && { stat -c %a "$B" > /out/mode; install -m 644 "$B" /out/binding.json; }
      tail -n 300 /tmp/act.log > /out/host.log 2>/dev/null || true
      [ -n "$B" ]
    ' >"$out/rc" 2>&1
  echo $? >"$out/code"
  return 0
}

host_s2_arming() {
  local out="$1"
  docker run --rm --network host -e "LLM_API_KEY=${LLM_API_KEY:?}" \
    -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$PI_IMAGE" -lc '
      set -u
      mkdir -p /opt/sdkx && tar -xzf /opt/sdk/*.tgz -C /opt/sdkx
      pi install /opt/sdkx/package >/dev/null 2>&1 || true
      cd /root/ws && timeout 120 pi -p "reply ok" >/tmp/s2.log 2>&1 || true
      tail -n 200 /tmp/s2.log >> /out/host.log 2>/dev/null || true
    ' >/dev/null 2>&1 || true
  return 0
}

host_stop() { :; }
