#!/usr/bin/env bash
# Offline self-test of the s6 judgement: a GOOD evidence file must pass, a BAD one (new session on a
# same-thread reply) must red. This keeps the case honest without paying for a 10-minute journey.
set -uo pipefail
cd /home/ubuntu/aimail/tests/host-acceptance/v2 || exit 1
T=$(mktemp -d)

good='{
  "acked": [true, true, true],
  "root1": "s6-a@example.com", "root2": "s6-a@example.com", "root3": "s6-b@example.com",
  "session1": "agent:main:aimail:dm:aimail:376d41b55841",
  "session2": "agent:main:aimail:dm:aimail:376d41b55841",
  "session3": "agent:main:aimail:dm:aimail:449a808598a7",
  "new_on_reply": 0, "new_on_newthread": 1,
  "sessions1": 4, "sessions2": 4, "sessions3": 5,
  "workspace": "/opt/l2home"
}'
bad=$(printf '%s' "$good" | python3 -c '
import json,sys
d=json.load(sys.stdin)
d["session2"]="agent:main:aimail:dm:aimail:deadbeef"
d["new_on_reply"]=1; d["sessions2"]=5
print(json.dumps(d))')

run() { CASE=S6 bash -c 'set -uo pipefail; . lib/assert.sh >/dev/null 2>&1; . cases/s6_threading.sh "'"$1"'"' 2>&1; }

echo "=== GOOD evidence (expect 0 RED) ==="
echo "$good" > "$T/s6.json"; out_good=$(run "$T"); printf '%s\n' "$out_good" | sed 's/^/  /'
echo "=== BAD evidence (expect >=1 RED, no PASS on the reply) ==="
echo "$bad" > "$T/s6.json"; out_bad=$(run "$T"); printf '%s\n' "$out_bad" | sed 's/^/  /'

gr=$(printf '%s' "$out_good" | grep -c '^RED' || true)
br=$(printf '%s' "$out_bad" | grep -c '^RED' || true)
echo
if [ "${gr:-0}" -eq 0 ] && [ "${br:-0}" -ge 1 ]; then echo "S6 CASE SELF-TEST: PASS (good=$gr red, bad=$br red)"; else echo "S6 CASE SELF-TEST: FAIL (good=$gr red, bad=$br red)"; fi
rm -rf "$T"
