#!/bin/bash
# audit-hosts.sh — prove a host image is CLEAN and its LLM config really answers.
#
# Owner criteria (2026-11-18): a host environment is only usable when
#   [1] it is clean  — no SDK/CLI of ours pre-installed (the SDK must be installed by
#       the test itself, never baked into the image), and
#   [2] its LLM config actually runs a reply — not "a config file exists", but a real
#       completion against the configured base URL returning content.
#
# The images are built for a local OpenAI-compatible server (LLM_BASE_URL, default
# http://127.0.0.1:8000/v1), so the audit runs with --network host and uses the
# config the image ships. Nothing is installed or written into the image.
#
# Usage: bash tests/agent-env/audit-hosts.sh [image:platform ...]
#   default: aimail-host-pi:pi aimail-host-dsh:dsh aimail-host-openclaw:openclaw aimail-host-hermes:hermes
# Exit: 0 = every image passes both criteria; 1 = at least one FAIL (fail closed).
set -uo pipefail

SPECS=("$@")
: "${LLM_API_KEY:?LLM_API_KEY is required (CI secret XIAOMI_API_KEY) — without it this gate cannot judge}"
EXPECT_BASE="${LLM_BASE_URL:-https://api.xiaomimimo.com/v1}"
EXPECT_MODEL="${LLM_MODEL:-mimo-v2.6-flash}"
[ ${#SPECS[@]} -eq 0 ] && SPECS=(aimail-host-pi:pi aimail-host-dsh:dsh aimail-host-openclaw:openclaw aimail-host-hermes:hermes)

PROBE="$(mktemp)"; trap 'rm -f "$PROBE"' EXIT
# 0600 from mktemp is unreadable for images that run as a non-root user (openclaw = node)
chmod 644 "$PROBE"
cat > "$PROBE" <<'PY'
import json, os, re, sys, urllib.request
plat = os.environ.get("PLAT", ""); home = os.path.expanduser("~")
base = model = key = None; cfg_used = None
def q(s):
    if s is None: return None
    s = s.strip()
    while len(s) >= 2 and s[0] == s[-1] and s[0] in "\"'": s = s[1:-1]
    return s
def jload(p):
    try:
        with open(p) as f: return json.load(f)
    except Exception: return None
if plat == "pi":
    p = os.path.join(home, ".pi/agent/models.json"); d = jload(p)
    if d and d.get("providers"):
        cfg_used = p; prov = list(d["providers"].values())[0]
        base, key = q(prov.get("baseUrl")), q(prov.get("apiKey"))
        model = (prov.get("models") or [{}])[0].get("id")
elif plat == "openclaw":
    for p in [os.path.join(home, "openclaw.json"), "/app/openclaw.json", os.path.join(home, ".openclaw/openclaw.json")]:
        d = jload(p)
        if d and (d.get("models") or {}).get("providers"):
            cfg_used = p; prov = list(d["models"]["providers"].values())[0]
            base, key = q(prov.get("baseUrl")), q(prov.get("apiKey"))
            model = (prov.get("models") or [{}])[0].get("id"); break
elif plat == "dsh":
    p = os.path.join(home, ".dsh/profiles/web/cordis.patch.yml")
    if os.path.exists(p):
        cfg_used = p; t = open(p).read()
        m = re.search(r"baseURL:\s*(\S+)", t); base = q(m.group(1)) if m else None
        m = re.search(r"apiKeyEnv:\s*(\S+)", t); key = os.environ.get(q(m.group(1)) if m else "", "dummy")
        m = re.search(r"model:\s*(\S+)", t); model = q(m.group(1)) if m else "default"
else:
    for p in [os.path.join(home, ".hermes/config.yaml"), "/opt/data/.hermes/config.yaml"]:
        if os.path.exists(p):
            cfg_used = p; t = open(p).read()
            m = re.search(r"base_url:\s*(\S+)", t); base = q(m.group(1)) if m else None
            m = re.search(r"^\s+default:\s*(\S+)", t, re.M); model = q(m.group(1)) if m else None
            m = re.search(r"api_key:\s*(\S+)", t); key = q(m.group(1)) if m else "dummy"
            break
exp_base = os.environ.get("EXPECT_BASE", "")
exp_model = os.environ.get("EXPECT_MODEL", "")
base = (base or "").rstrip("/")
exp_base = exp_base.rstrip("/")
print("config:", cfg_used or "NOT FOUND", "| base:", base, "| model:", (model or "")[:40],
      "| key:", "yes" if key else "no", "| expect:", exp_base, exp_model)
if not base:
    print("RESULT: FAIL (no usable config)"); sys.exit(1)
if exp_base and base != exp_base:
    print("RESULT: FAIL (endpoint is not the gate endpoint)"); sys.exit(1)
if exp_model and model and model != exp_model:
    print("RESULT: FAIL (model is not the gate model)"); sys.exit(1)
body = json.dumps({"model": model, "messages": [{"role": "user", "content": "Reply with: ok"}], "max_tokens": 64}).encode()
req = urllib.request.Request(base.rstrip("/") + "/chat/completions", data=body, method="POST",
    headers={"Content-Type": "application/json", "Authorization": "Bearer " + (key or "dummy")})
try:
    with urllib.request.urlopen(req, timeout=90) as r:
        d = json.loads(r.read()); msg = (d.get("choices") or [{}])[0].get("message", {})
        txt = (msg.get("content") or msg.get("reasoning_content") or "").strip()
        print("RESULT:", "PASS" if txt else "FAIL(empty)", "| reply:", repr(txt[:60]))
        sys.exit(0 if txt else 1)
except Exception as e:
    print("RESULT: FAIL |", type(e).__name__, str(e)[:120]); sys.exit(1)
PY

FAILED=0
for spec in "${SPECS[@]}"; do
  img="${spec%%:*}"; plat="${spec##*:}"
  echo "════════ $img  (platform=$plat)"
  docker run --rm --network host -e PLAT="$plat" -e LLM_API_KEY="$LLM_API_KEY" \
    -e EXPECT_BASE="$EXPECT_BASE" -e EXPECT_MODEL="$EXPECT_MODEL" \
    -v "$PROBE:/opt/probe.py:ro" --entrypoint bash "$img" -lc '
    set +e
    echo "── [1] SDK must be absent"
    command -v aimail >/dev/null 2>&1 && echo "  ✗ aimail CLI present: $(command -v aimail)" || echo "  ✓ no aimail CLI"
    g=$(npm ls -g --depth=0 2>/dev/null | grep -iE "agent-aimail|@aimail/"); [ -n "$g" ] && echo "  ✗ npm: $g" || echo "  ✓ npm global: no aimail"
    p=$(pnpm ls -g --depth 0 2>/dev/null | grep -iE "agent-aimail|@aimail/"); [ -n "$p" ] && echo "  ✗ pnpm: $p" || echo "  ✓ pnpm global: no aimail"
    python3 -c "import aimail" 2>/dev/null && echo "  ✗ python aimail importable" || echo "  ✓ python cannot import aimail"
    r=$(find /root /app /opt /home -maxdepth 8 \( -name "agent-aimail" -o -iname "*agent-aimail*" -o -iname "*aimailsdk*" \) -not -path "*/agent-env/*" -not -path "/opt/regression/*" -not -path "/proc/*" 2>/dev/null | head -5); [ -n "$r" ] && echo "  ✗ residue: $r" || echo "  ✓ no agent-aimail dirs or cache entries"
    echo "── [2] LLM config (gate endpoint) + real completion with the CI key"
    bash /opt/agent-env/set-llm-key.sh "$PLAT" 2>&1 | sed "s/^/  /"
    python3 /opt/probe.py 2>&1 | sed "s/^/  /"
  ' 2>&1 | tee "/tmp/host-audit-$plat.log" | tail -18
  grep -q "✗" "/tmp/host-audit-$plat.log" && FAILED=1
  grep -q "RESULT: PASS" "/tmp/host-audit-$plat.log" || FAILED=1
done
[ "$FAILED" = 0 ] && echo "── AUDIT PASS (all hosts clean + LLM answers)" || echo "── AUDIT FAIL (see ✗ / RESULT lines above)"
exit "$FAILED"
