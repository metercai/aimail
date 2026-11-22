#!/usr/bin/env bash
# S1 — activation: public /apply code -> in-host aimail_address(bind) -> binding artefact.
# host-agnostic: talks only to the adapter.
set -uo pipefail
CASE="S1 activation"
out="$1"; addr="$2"; code="$3"

host_s1_activate "$out" "$addr" "$code"
rc="$(cat "$out/code" 2>/dev/null || echo 9)"

if [ ! -f "$out/binding.json" ]; then
  red "no binding written (adapter rc=$rc) — see $out/rc"
  return 0
fi
pass "binding written"

python3 - "$out/binding.json" <<'PY' >"$out/binding.assert" 2>&1
import json, sys
d = json.load(open(sys.argv[1]))
checks = {
    "system_id_is_gateway": str(d.get("system_id", "")).startswith("system_") or str(d.get("system_id", "")).startswith("system-"),
    "agent_system_id": str(d.get("agent_system_id", "")).startswith("asid_"),
    "workspace_path": bool(d.get("workspace_path")),
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
