#!/usr/bin/env bash
# OpenClaw host adapter (containerised, pinned image; L2 = dev build pre-installed).
set -uo pipefail
HOST_NAME=openclaw
OC_IMAGE="${OC_IMAGE:-aimail-host-openclaw:latest}"
V2_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

host_s1_activate() {
  local out="$1" addr="$2" code="$3"
  mkdir -p "$out"
  docker run --rm --network host \
    -e "LLM_API_KEY=${LLM_API_KEY:?}" -e "AH=$addr" -e "AC=$code" -e "GWURL=${GW_URL:-http://127.0.0.1:18080}" \
    -v "${SDK_TARBALL_DIR:?}:/opt/sdk:ro" -v "$out:/out" \
    --entrypoint bash "$OC_IMAGE" -lc '
      set -u
      cd /app
      python3 - <<PY
import json,os,pathlib
p=pathlib.Path("/app/.openclaw/openclaw.json"); c=json.loads(p.read_text())
for prov in ((c.get("models") or {}).get("providers") or {}).values():
    if prov.get("apiKey") is not None: prov["apiKey"]=os.environ["LLM_API_KEY"]
pl=c.setdefault("plugins",{}); pl["enabled"]=True; pl["allow"]=["agent-aimail"]
pl.setdefault("entries",{}).setdefault("agent-aimail",{})["enabled"]=True
c["tools"]={"allow":["send_mail","manage_contacts","contact_profile","set_contact_profile","email_summary","set_email_summary","search_mail","board_status","board_task_list","board_task_show","board_heartbeat","board_members","board_roles","role_calibrator","aimail_address"]}
g=c.setdefault("gateway",{}); g.update({"mode":"local","port":39200,"auth":{"token":"l2-oc"}})
c["hooks"]={"enabled":True,"token":"l2-hook","allowRequestSessionKey":True,"allowedSessionKeyPrefixes":["hook:","agent:main:aimail:"]}
p.write_text(json.dumps(c,indent=2))
PY
      openclaw plugins install /opt/sdk/*.tgz --force --accept-capabilities >/tmp/plugin.log 2>&1 || { echo "install failed"; tail -20 /tmp/plugin.log; exit 3; }
      openclaw plugins enable agent-aimail --accept-capabilities >>/tmp/plugin.log 2>&1 || true
      pkill -f "openclaw gateway" 2>/dev/null; sleep 1
      setsid nohup openclaw gateway >/tmp/gw.log 2>&1 &
      for i in $(seq 1 90); do grep -aq "http server listening" /tmp/gw.log && break; sleep 1; done
      curl -s -m 240 -X POST http://127.0.0.1:39200/hooks/agent -H "content-type: application/json" \
        -H "authorization: Bearer l2-hook" \
        -d "{\"message\":\"Activate and bind my AIMail mailbox address $AH with activation code $AC at gateway $GWURL using aimail_address.\"}" >/tmp/act.json 2>&1 || true
      for i in $(seq 1 80); do ls /app/.aimail/systems/*/*/agentmail.json >/dev/null 2>&1 && break; sleep 3; done
      B=$(ls /app/.aimail/systems/*/*/agentmail.json 2>/dev/null | head -1)
      echo "binding=${B:-none}"
      [ -n "$B" ] && cp "$B" /out/binding.json
      # S2: restart so the binding arms the pull
      pkill -f "openclaw gateway" 2>/dev/null; sleep 3
      setsid nohup openclaw gateway >>/tmp/gw.log 2>&1 &
      sleep 30
      tail -n 500 /tmp/gw.log > /out/host.log 2>/dev/null || true
      [ -n "$B" ]
    ' >"$out/rc" 2>&1
  echo $? >"$out/code"
  return 0
}

host_s2_arming() { :; }   # restart happens inside host_s1_activate
host_stop() { :; }
