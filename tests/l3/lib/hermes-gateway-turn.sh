#!/usr/bin/env bash
# hermes-gateway-turn.sh — deliver ONE prompt to hermes through its gateway session (owner: option A).
#
# Why not `hermes -z "<prompt>"`: a short literal answers instantly, but our 795-byte activation
# prompt produced no output at 240/420/900s (measured). Over the gateway the same prompt runs as a
# normal session message, which is how the product's own TUI delivers it.
#
# The provider needs THREE things in hermes 0.23.4 (each missing one is a different error):
#   model.provider=openai-api   -> otherwise "No LLM provider configured"
#   model.base_url=<endpoint>   -> otherwise it calls api.openai.com: "HTTP 403: Country ... not supported"
#   model.default=<model>       -> otherwise the session picks hermes' own default
#                                  ("Unsupported model anthropic/claude-opus-4.6")
#   $HERMES_HOME/.env OPENAI_API_KEY -> a command-line env var alone: "No usable credentials"
set -uo pipefail
PROMPT="${1:?prompt file}"
TMO="${2:-420}"
MODEL="${LLM_MODEL:-mimo-v2.6-flash}"
BASE="${LLM_BASE_URL:-https://api.xiaomimimo.com/v1}"
[ -n "${LLM_API_KEY:-}" ] || { echo "LLM_API_KEY is empty"; exit 2; }

su hermes -s /bin/bash -c "export HERMES_HOME=/opt/data; \
  /opt/hermes/bin/hermes config set model.provider 'openai-api' >/dev/null 2>&1; \
  /opt/hermes/bin/hermes config set model.base_url '$BASE' >/dev/null 2>&1; \
  /opt/hermes/bin/hermes config set model.default '$MODEL' >/dev/null 2>&1; \
  printf 'OPENAI_API_KEY=%s\nOPENAI_BASE_URL=%s\n' '$LLM_API_KEY' '$BASE' > /opt/data/.env; \
  chmod 600 /opt/data/.env" >/dev/null 2>&1 || true

exec su hermes -s /bin/bash -c "HERMES_HOME=/opt/data HG_DUMP=/tmp/hermes-events.jsonl \
  timeout $TMO /opt/hermes/.venv/bin/python /work/hermes-gateway-turn.py '$PROMPT' $TMO"
