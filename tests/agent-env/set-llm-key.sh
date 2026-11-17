#!/bin/bash
# set-llm-key.sh — inject the live LLM credential into a prepared host at RUN time.
#
# Why: gate images ship the endpoint/model but never a secret (owner 2026-11-18:
# hosts must use the same OpenAI-compatible endpoint as L3, with the key coming from
# CI secrets). The writers emit __LLM_API_KEY__ (pi/openclaw) or api_key_env
# (hermes/dsh); this script turns the placeholder into the real value inside the
# container, so the host can actually answer.
#
# Usage (inside the container): LLM_API_KEY=... /opt/agent-env/set-llm-key.sh [platform]
# Exit: 0 = a credential is now in place for the detected platform; 1 = nothing to patch.
set -uo pipefail
KEY="${LLM_API_KEY:-}"
PLAT="${1:-${PLATFORM:-}}"
[ -n "$KEY" ] || { echo "✗ LLM_API_KEY is empty — refusing to write a bogus credential" >&2; exit 1; }

patched=0
if [ -z "$PLAT" ]; then
  for p in pi openclaw dsh hermes deerflow; do
    if [ -f "$HOME/.pi/agent/models.json" ] && [ "$p" = pi ]; then PLAT=pi; break; fi
    if [ -f "$HOME/.openclaw/openclaw.json" ] || [ -f /app/.openclaw/openclaw.json ]; then PLAT=openclaw; break; fi
    if [ -f "$HOME/.dsh/profiles/web/cordis.patch.yml" ]; then PLAT=dsh; break; fi
    if [ -f /opt/data/.hermes/config.yaml ] || [ -f "$HOME/.hermes/config.yaml" ]; then PLAT=hermes; break; fi
  done
fi
[ -n "$PLAT" ] || { echo "✗ cannot detect the host platform" >&2; exit 1; }

case "$PLAT" in
  pi)
    f="$HOME/.pi/agent/models.json"
    python3 - "$f" "$KEY" <<'PY'
import json, sys
p, key = sys.argv[1], sys.argv[2]
d = json.load(open(p))
for prov in (d.get("providers") or {}).values():
    prov["apiKey"] = key
json.dump(d, open(p, "w"), indent=2)
print(f"  ✓ pi: apiKey injected into {p}")
PY
    ;;
  openclaw)
    for f in "$HOME/.openclaw/openclaw.json" /app/.openclaw/openclaw.json; do
      [ -f "$f" ] || continue
      python3 - "$f" "$KEY" <<'PY'
import json, sys
p, key = sys.argv[1], sys.argv[2]
d = json.load(open(p))
for prov in ((d.get("models") or {}).get("providers") or {}).values():
    prov["apiKey"] = key
json.dump(d, open(p, "w"), indent=2)
print(f"  ✓ openclaw: apiKey injected into {p}")
PY
    done
    ;;
  hermes)
    for f in /opt/data/.hermes/config.yaml "$HOME/.hermes/config.yaml"; do
      [ -f "$f" ] || continue
      python3 - "$f" "$KEY" <<'PY'
import re, sys
p, key = sys.argv[1], sys.argv[2]
lines = open(p).read().splitlines()
out, in_model = [], False
for ln in lines:
    if re.match(r"^model:\s*$", ln):
        in_model = True
        out.append(ln)
        continue
    if in_model and ln and not ln[0].isspace():
        in_model = False
    if in_model and re.match(r"^\s+api_key_env:\s*", ln):
        continue  # replaced by the explicit key below
    out.append(ln)
txt = "\n".join(out) + "\n"
if re.search(r"^\s+api_key:\s*", txt, re.M):
    txt = re.sub(r"^(\s+)api_key:\s*.*$", lambda m: f"{m.group(1)}api_key: {key}", txt, count=1, flags=re.M)
else:
    txt = re.sub(r"^(model:\s*)$", lambda m: f"{m.group(1)}\n  api_key: {key}", txt, count=1, flags=re.M)
open(p, "w").write(txt)
print(f"  ✓ hermes: api_key injected into {p}")
PY
    done
    ;;
  dsh)
    # dsh's patch layer names the env var (apiKeyEnv: LLM_API_KEY); the host reads the
    # process environment, so exporting it for the host process is the whole job.
    echo "  ✓ dsh: apiKeyEnv=LLM_API_KEY — export LLM_API_KEY to the host process"
    ;;
  *) echo "✗ unsupported platform: $PLAT" >&2; exit 1 ;;
esac
