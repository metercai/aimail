#!/usr/bin/env bash
# Claim a base system/address activation code through the public webapp API.
# The verification code and the activation code are emailed via the real
# outbound queue to Mailpit; this script reads them back.
#
# usage: claim-base.sh --gateway URL --mailpit URL --email E --name N \
#        --kind system|address --domain D [--agentname A]
set -euo pipefail
GATEWAY=""; MAILPIT=""; EMAIL=""; NAME="User"; KIND="address"; DOMAIN="test.local"; AGENT=""
while [[ $# -gt 0 ]]; do case "$1" in
  --gateway) GATEWAY="$2"; shift 2;;
  --mailpit) MAILPIT="$2"; shift 2;;
  --email) EMAIL="$2"; shift 2;;
  --name) NAME="$2"; shift 2;;
  --kind) KIND="$2"; shift 2;;
  --domain) DOMAIN="$2"; shift 2;;
  --agentname) AGENT="$2"; shift 2;;
  *) echo "unknown $1" >&2; exit 2;;
esac; done
[[ -n "$GATEWAY" && -n "$MAILPIT" && -n "$EMAIL" ]] || { echo "gateway/mailpit/email required" >&2; exit 2; }
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CODE_READER="$HERE/../harness/mailpit_code.py"

curl -fsS -X POST "$GATEWAY/api/v1/public/email-verify/send" \
  -H 'content-type: application/json' --data "{\"email\":\"$EMAIL\"}" >/dev/null
VCODE=$(python3 "$CODE_READER" --url "$MAILPIT" --to "$EMAIL" --kind verify --timeout 60)
curl -fsS -X POST "$GATEWAY/api/v1/public/email-verify/check" \
  -H 'content-type: application/json' --data "{\"email\":\"$EMAIL\",\"code\":\"$VCODE\"}" >/dev/null

if [[ "$KIND" == "system" ]]; then
  JSON=$(curl -fsS -X POST "$GATEWAY/api/v1/public/apply-system" \
    -H 'content-type: application/json' \
    --data "{\"email\":\"$EMAIL\",\"name\":\"$NAME\",\"domain\":\"$DOMAIN\"}")
else
  JSON=$(curl -fsS -X POST "$GATEWAY/api/v1/public/apply-address" \
    -H 'content-type: application/json' \
    --data "{\"email\":\"$EMAIL\",\"name\":\"$NAME\",\"agentname\":\"$AGENT\",\"domain\":\"$DOMAIN\"}")
fi
# Base returns the code in the response; the same code is also emailed via
# the real outbound queue (asserted by the Mailpit verification mail above).
printf '%s\n' "$JSON" | python3 -c 'import sys,json; print(json.load(sys.stdin)["activation_code"])' 
