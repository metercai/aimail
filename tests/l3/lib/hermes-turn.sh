#!/usr/bin/env bash
# hermes-turn.sh — drive ONE hermes agent turn the way the host itself expects.
#
# Measured facts this encodes (2026-11-27):
#   * hermes runs as uid 10000 (`hermes`) with home /opt/data; a CLI turn started as root
#     against a copied HOME either cannot write /opt/data/.hermes/cron or ends in
#     `Gateway inaccessible: profile_mismatch`.
#   * the provider base URL must be set (`model.base_url`), otherwise the built-in
#     `openai-api` provider talks to api.openai.com and answers
#     "HTTP 403: Country, region, or territory not supported".
#   * the container has no DNS unless it runs with --network host; without it the model
#     call hangs silently to the timeout (this is what "hermes hangs" actually was).
#   * `install out of sync (pnpm: ...)` is hermes' own pm self-check about its JS
#     workspaces — cosmetic for a turn, and our SDK is a wheel: pip installs it, pnpm is
#     irrelevant to it.
#   * the prompt must not be spliced into a shell string: it contains backticks and quotes,
#     and an inline `"$(cat ...)"` inside a `su -c` string gets re-parsed (measured: hermes
#     silently fell back to its interactive TUI because -z received nothing). So the runner
#     script is written with a QUOTED heredoc and reads the prompt from a file at run time.
#
# Usage (inside the container, as root): bash hermes-turn.sh <prompt-file> [timeout]
set -uo pipefail
PROMPT="${1:?prompt file}"
TMO="${2:-420}"
MODEL="${LLM_MODEL:-mimo-v2.6-flash}"
BASE="${LLM_BASE_URL:-https://api.xiaomimimo.com/v1}"

[ -n "${LLM_API_KEY:-}" ] || { echo "LLM_API_KEY is empty"; exit 2; }

# Provider selection is THREE things in hermes 0.23.4, and all three are required
# (measured one by one):
#   config model.provider   - without it: ProviderNotConfiguredError "No LLM provider configured"
#   config model.base_url   - without it the built-in openai-api route calls api.openai.com
#                             and answers "HTTP 403: Country, region, or territory not supported"
#   $HERMES_HOME/.env key   - an env var on the command line is NOT enough:
#                             "No usable credentials found for provider 'openai-api'"
# NOTE the live config is /opt/data/config.yaml; prepare.sh writes /opt/data/.hermes/config.yaml,
# which hermes does not read for the model block (that mismatch cost several runs).
su hermes -s /bin/bash -c "export HERMES_HOME=/opt/data; \
  /opt/hermes/bin/hermes config set model.provider 'openai-api' >/dev/null 2>&1; \
  /opt/hermes/bin/hermes config set model.base_url '$BASE' >/dev/null 2>&1; \
  /opt/hermes/bin/hermes config set model.default '$MODEL' >/dev/null 2>&1; \
  printf 'OPENAI_API_KEY=%s\\nOPENAI_BASE_URL=%s\\n' '$LLM_API_KEY' '$BASE' > /opt/data/.env; \
  chmod 600 /opt/data/.env" >/dev/null 2>&1 || true

RUNNER=/tmp/hermes-turn-$$.sh
cat > "$RUNNER" <<'RUNEOF'
#!/bin/bash
set -uo pipefail
export HERMES_HOME=/opt/data
export AIMAIL_HOME=/opt/data/.aimail
export OPENAI_API_KEY="__KEY__"
export OPENAI_BASE_URL="__BASE__"
export LLM_API_KEY="__KEY__"
exec timeout __TMO__ /opt/hermes/bin/hermes -m "__MODEL__" --yolo -z "$(cat "__PROMPT__")"
RUNEOF
# fill the placeholders without letting the prompt text near a shell parser
python3 - "$RUNNER" "$PROMPT" "$TMO" "$MODEL" "$BASE" "$LLM_API_KEY" <<'PY'
import sys
run, prompt, tmo, model, base, key = sys.argv[1:7]
s = open(run, encoding="utf-8").read()
s = (s.replace("__KEY__", key).replace("__BASE__", base)
       .replace("__TMO__", tmo).replace("__MODEL__", model).replace("__PROMPT__", prompt))
open(run, "w", encoding="utf-8").write(s)
PY
chmod 0755 "$RUNNER"

su hermes -s /bin/bash -c "$RUNNER"
rc=$?
rm -f "$RUNNER"
exit "$rc"
