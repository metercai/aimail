#!/usr/bin/env bash
# run-dsh-l2-sse.sh — NEW DSH L2 profile (SSE contract), no CLI test phases.
#
# Legacy L2 remains at aimail-advanced/tests/SDK/docker-regression/hosts/
# run-host-regression.sh: it tests CLI install/register and push/webhook.
#
# This profile drives the real tssdk DSH adapter through its Agent-facing
# functions only:
#   aimail_system bind  → system config/default worker/default workspace
#   aimail_address bind → current-workspace address binding
#   mail-hub            → SSE pending stream → fake Agent session
# no `aimail` CLI, no register-cli, no inbound listener/webhook.
#
# Required env:
#   GATEWAY_URL     live base/advanced gateway
#   SYSTEM_CODE     sys-... claimed on /apply
#   ADDRESS_CODE    addr-... for EMAIL
#   EMAIL           address to activate
#   DOMAIN          dedicated domain for the system code
#   SYSTEM_NAME     shared-domain system name (instead of DOMAIN)
#   WORKSPACE       workspace path used by the activation prompt
#   L2_HOME         scratch home (default: mktemp)
#   TSSDK_DIST      compiled agent-aimail dist (default: repo tssdk/.../dist)
#
# Exit: 0 PASS · 1 FAIL · 2 GAP/prerequisite missing
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
HARNESS="$HERE/../harness/dsh-harness.mjs"
GATEWAY_URL="${GATEWAY_URL:-}"
SYSTEM_CODE="${SYSTEM_CODE:-}"
ADDRESS_CODE="${ADDRESS_CODE:-}"
EMAIL="${EMAIL:-}"
DOMAIN="${DOMAIN:-}"
SYSTEM_NAME="${SYSTEM_NAME:-}"
WORKSPACE="${WORKSPACE:-}"
L2_HOME="${L2_HOME:-$(mktemp -d -t dsh-l2-sse-XXXXXX)}"
export AIMAIL_REPO="${AIMAIL_REPO:-$ROOT}"
export TSSDK_DIST="${TSSDK_DIST:-$ROOT/tssdk/packages/agent-aimail/dist}"

say(){ echo "  $*"; }
ok(){ echo "  ✓ $*"; }
bad(){ echo "  ✗ $*"; }
gap(){ echo "  ⚠ GAP[L2-SSE]: $*"; exit 2; }

command -v node >/dev/null 2>&1 || gap "node not in PATH"
[ -f "$HARNESS" ] || gap "dsh harness missing: $HARNESS"
[ -f "$TSSDK_DIST/dsh/index.js" ] || gap "compiled DSH adapter missing: $TSSDK_DIST (run pnpm build)"
[ -n "$GATEWAY_URL" ] || gap "GATEWAY_URL is required"
[ -n "$SYSTEM_CODE" ] || gap "SYSTEM_CODE is required"
[ -n "$ADDRESS_CODE" ] || gap "ADDRESS_CODE is required"
[ -n "$EMAIL" ] || gap "EMAIL is required"
[ -n "$DOMAIN$SYSTEM_NAME" ] || gap "DOMAIN or SYSTEM_NAME is required"
[ -n "$WORKSPACE" ] || gap "WORKSPACE is required"
mkdir -p "$WORKSPACE" "$L2_HOME"
WORKSPACE="$(cd "$WORKSPACE" && pwd -P)"
say "L2-SSE home=$L2_HOME workspace=$WORKSPACE"

# ── 1) System onboarding: aimail_system bind (no CLI) ─────────────────────
SYSTEM_ARGS=(--gateway "$GATEWAY_URL" --code "$SYSTEM_CODE")
[ -n "$DOMAIN" ] && SYSTEM_ARGS+=(--domain "$DOMAIN")
[ -n "$SYSTEM_NAME" ] && SYSTEM_ARGS+=(--system_name "$SYSTEM_NAME")
node "$HARNESS" system --home "$L2_HOME" "${SYSTEM_ARGS[@]}" >"$L2_HOME/system.json" 2>"$L2_HOME/system.err"
SYS_RC=$?
[ "$SYS_RC" -eq 0 ] || { bad "aimail_system failed (rc=$SYS_RC): $(tail -5 "$L2_HOME/system.err")"; exit 1; }
SID="$(python3 - "$L2_HOME/system.json" <<'PY'
import json,sys
d=json.load(open(sys.argv[1]))
assert d.get("success") is True, d
print(d.get("system_id") or "")
PY
)" || { bad "system result invalid"; exit 1; }
[ -n "$SID" ] || { bad "system_id empty"; exit 1; }
ok "system onboarded in-agent: $SID"

# ── 2) Address activation in the current workspace (no CLI) ───────────────
node "$HARNESS" address --home "$L2_HOME" --gateway "$GATEWAY_URL" \
  --email "$EMAIL" --code "$ADDRESS_CODE" --workspace "$WORKSPACE" \
  >"$L2_HOME/address.json" 2>"$L2_HOME/address.err"
ADDR_RC=$?
[ "$ADDR_RC" -eq 0 ] || { bad "aimail_address failed (rc=$ADDR_RC): $(tail -5 "$L2_HOME/address.err")"; exit 1; }
ok "address activated in-agent: $EMAIL"

# ── 3) Contract assertions on the address binding ─────────────────────────
BIND="$(python3 - "$L2_HOME/aimail/systems" "$SID" "$EMAIL" <<'PY'
import glob, json, os, re, sys
root, sid, email = sys.argv[1], sys.argv[2], sys.argv[3]
for p in glob.glob(os.path.join(root, sid, "*", "agentmail.json")):  # contract-allowed: L2 locates the real binding file for assertions
    try:
        d = json.load(open(p))
    except Exception:
        continue
    if str(d.get("email", "")).lower() == email.lower():
        print(p); raise SystemExit(0)
raise SystemExit(1)
PY
)" || { bad "binding not found for $EMAIL"; exit 1; }
python3 - "$BIND" "$WORKSPACE" <<'PY' || { bad "binding violates SSE contract"; exit 1; }
import json, os, sys
p, workspace = sys.argv[1], os.path.realpath(sys.argv[2])
d = json.load(open(p))
assert d.get("workspace_path"), f"workspace_path missing: {d}"
assert os.path.realpath(d["workspace_path"]) == workspace, (d.get("workspace_path"), workspace)
assert not d.get("webhook_url"), f"SSE binding must not carry webhook_url: {d}"
assert not d.get("session_id"), f"SSE binding must not persist session_id: {d}"
assert d.get("api_key"), "api_key missing"
PY
ok "binding workspace == activation session workspace; no webhook/session_id"
say "binding: $BIND"

# ── 4) SSE welcome → agent session in the bound workspace ─────────────────
node "$HARNESS" hub --home "$L2_HOME" --gateway "$GATEWAY_URL" --email "$EMAIL" --timeout 90 \
  >"$L2_HOME/hub.json" 2>"$L2_HOME/hub.err" &
HUB_PID=$!
sleep 2
node "$HARNESS" welcome --home "$L2_HOME" --gateway "$GATEWAY_URL" --system "$SID" --email "$EMAIL" \
  >"$L2_HOME/welcome.json" 2>"$L2_HOME/welcome.err"
WELCOME_RC=$?
[ "$WELCOME_RC" -eq 0 ] || { kill "$HUB_PID" 2>/dev/null; bad "welcome API failed (rc=$WELCOME_RC): $(tail -5 "$L2_HOME/welcome.err")"; exit 1; }
wait "$HUB_PID"; HUB_RC=$?
[ "$HUB_RC" -eq 0 ] || { bad "SSE hub received no followup (rc=$HUB_RC): $(tail -8 "$L2_HOME/hub.err")"; exit 1; }
python3 - "$L2_HOME/hub.json" "$WORKSPACE" <<'PY' || { bad "hub followup workspace mismatch"; exit 1; }
import json, os, sys
d = json.load(open(sys.argv[1]))
want = os.path.realpath(sys.argv[2])
assert d.get("status", {}).get("mode") == "sse", d
assert d.get("status", {}).get("connected") is True, d
assert d.get("followups"), f"no followup: {d}"
for f in d["followups"]:
    assert os.path.realpath(f.get("cwd") or "") == want, (f, want)
PY
ok "welcome delivered through SSE → agent session in bound workspace"

echo
echo "  RESULT: L2-SSE PASS"
echo "  evidence: $L2_HOME"
exit 0
