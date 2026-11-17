#!/usr/bin/env bash
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
: "${GATEWAY_URL:?}"; : "${SYSTEM_CODE:?}"; : "${ADDRESS_CODE:?}"; : "${EMAIL:?}"; : "${WORKSPACE:?}"
: "${DOMAIN:=}"
ARGS=(--gateway "$GATEWAY_URL" --system-code "$SYSTEM_CODE" --address-code "$ADDRESS_CODE" --email "$EMAIL" --workspace "$WORKSPACE")
[ -n "$DOMAIN" ] && ARGS+=(--domain "$DOMAIN")
[ "${DELIVERY:-0}" = 1 ] && ARGS+=(--delivery)
exec node "$HERE/harness.mjs" pi "${ARGS[@]}"
