#!/usr/bin/env bash
# S1 — activation artefacts (the journey already ran; this case only asserts).
set -uo pipefail
CASE="S1 activation"
out="$1"

if [ ! -f "$out/binding.json" ]; then
  red "no binding written (see $out/rc)"
  return 0
fi
pass "binding written"

python3 - "$out/binding.json" <<'PY' >"$out/binding.assert" 2>&1
import json, sys
d = json.load(open(sys.argv[1]))
sid = str(d.get("system_id", ""))
checks = {
    "system_id_is_gateway": sid.startswith("system-") or sid.startswith("system_"),
    "agent_system_id": (str(d.get("agent_system_id", "")).startswith("asid_")
                        or not d.get("agent_system_id")),   # dsh derives it at runtime
    "workspace_path": bool(d.get("workspace_path") or d.get("workspace_id")),
    "no_webhook": not d.get("webhook_url"),
    "has_key": bool(d.get("api_key")),
}
for k, v in checks.items():
    print(("OK " if v else "FAIL ") + k)
PY
while read -r st name; do
  [ "$st" = "OK" ] && pass "$name" || red "$name"
done <"$out/binding.assert"

mode="$(cat "$out/mode" 2>/dev/null || echo '?')"
[ "$mode" = "600" ] && pass "binding mode 0600 (in-host)" || red "binding mode $mode (in-host)"

rows="$(cat "$out/mail_rows" 2>/dev/null || echo '')"
if [ -n "$rows" ]; then
  [ "$rows" -ge 1 ] && pass "composed profile carries tool-mail (rows=$rows)" \
    || red "no tool-mail row in composed profile (rows=$rows) — the plugin did not load"
fi
