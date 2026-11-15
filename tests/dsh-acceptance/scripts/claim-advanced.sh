#!/usr/bin/env bash
# Claim an advanced shared-domain address code. Advanced emails the code and
# does NOT return it in the response, so the code is read from Mailpit.
#
# usage: claim-advanced.sh --gateway URL --mailpit URL --email E --name N \
#        --agentname A --shared-domain shared.test
set -euo pipefail
GATEWAY=""; MAILPIT=""; EMAIL=""; NAME="User"; AGENT=""; SHARED="shared.test"
while [[ $# -gt 0 ]]; do case "$1" in
  --gateway) GATEWAY="$2"; shift 2;;
  --mailpit) MAILPIT="$2"; shift 2;;
  --email) EMAIL="$2"; shift 2;;
  --name) NAME="$2"; shift 2;;
  --agentname) AGENT="$2"; shift 2;;
  --shared-domain) SHARED="$2"; shift 2;;
  *) echo "unknown $1" >&2; exit 2;;
esac; done
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_READER="$HERE/../harness/mailpit_code.py"

curl -fsS -X POST "$GATEWAY/api/v1/public/email-verify/send" \
  -H 'content-type: application/json' --data "{\"email\":\"$EMAIL\"}" >/dev/null
VCODE=$(python3 "$CODE_READER" --url "$MAILPIT" --to "$EMAIL" --kind verify --timeout 60)
START_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
curl -fsS -X POST "$GATEWAY/api/v1/apply-address" \
  -H 'content-type: application/json' \
  --data "{\"applicant_name\":\"$NAME\",\"applicant_email\":\"$EMAIL\",\"email_verify_code\":\"$VCODE\",\"agentname\":\"$AGENT\",\"shared_domain\":\"$SHARED\"}" >/dev/null
END_MS=$(python3 -c 'import time; print(int(time.time()*1000))')
echo "apply_ms=$((END_MS-START_MS))" >&2
# The API must return quickly after enqueue; the code mail is async. Give the
# scheduler/Mailpit enough time in constrained QA networks.
python3 "$CODE_READER" --url "$MAILPIT" --to "$EMAIL" --kind activation --contains "$AGENT@$SHARED" --timeout 240
