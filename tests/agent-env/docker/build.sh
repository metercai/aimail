#!/usr/bin/env bash
#
# agent-env/docker/build.sh — build the five agent test host images from ONE source.
#
# The images are pure native: the aimail CLI / pysdk / tssdk are NOT baked in (see the
# contract in ../README.md). Preparing the environment (official install + base env +
# release-age gates removed + LLM config) is done by ../prepare.sh, and accepted by
# ../verify.sh at build time (fail-closed, offline).
#
# Usage: build.sh [--assets DIR] [--no-accept] [agent...]
#   --assets DIR : extra files copied into /opt/regression (harness regression scripts)
#   --no-accept  : skip the build-time acceptance of the environment (not recommended)
#   agent...     : subset to build (default: hermes openclaw dsh pi deerflow)
#
set -uo pipefail
CTX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENVDIR="$(cd "$CTX/.." && pwd)"

ASSETS=""; PLATFORMS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --assets)    ASSETS="${2:-}"; shift 2 ;;
    --no-accept) ACCEPT=0; shift ;;
    *)           PLATFORMS+=("$1"); shift ;;
  esac
done
ACCEPT="${ACCEPT:-1}"
[ ${#PLATFORMS[@]} -eq 0 ] && PLATFORMS=(hermes openclaw dsh pi deerflow)

LLM_BASE_URL="${LLM_BASE_URL:-http://127.0.0.1:8000/v1}"
LLM_MODEL="${LLM_MODEL:-Qwen3.8-27B-UD-Q4_K_M}"

for p in "${PLATFORMS[@]}"; do
  case "$p" in
    hermes)   BASE="nousresearch/hermes-agent:latest" ;;
    openclaw) BASE="ghcr.io/openclaw/openclaw:latest" ;;
    dsh|pi)   BASE="${BASE_IMG:-nikolaik/python-nodejs:python3.12-nodejs22}" ;;
    deerflow)
      BASE="${DEERFLOW_BASE_IMG:-$(docker images --format '{{.Repository}}:{{.Tag}}' deer-flow-gateway 2>/dev/null | grep '^deer-flow-gateway:src-' | head -1)}"
      [ -n "$BASE" ] || { echo "✗ deerflow base missing — build it from source first (rebuild-deerflow-base.sh)" >&2; exit 1; } ;;
    *) echo "✗ unknown agent: $p" >&2; exit 2 ;;
  esac
  [ -f "$CTX/Dockerfile.$p" ] || { echo "✗ missing $CTX/Dockerfile.$p" >&2; exit 2; }

  TMP="$(mktemp -d)"
  mkdir -p "$TMP/agent-env/lib" "$TMP/extras"
  cp "$ENVDIR/prepare.sh" "$ENVDIR/verify.sh" "$TMP/agent-env/"
  cp "$ENVDIR/lib/llm-config.py" "$TMP/agent-env/lib/"
  if [ -n "$ASSETS" ]; then
  # scripts only: the harness assets are shell/python/js; the tree also holds Dockerfiles
  # and docs that must not be baked into /opt/regression
  find "$ASSETS" -maxdepth 1 -type f \( -name '*.sh' -o -name '*.py' -o -name '*.js' \) \
    -exec cp -f {} "$TMP/extras/" \;
fi
  cp "$CTX/Dockerfile.$p" "$TMP/Dockerfile"

  echo "── build $p (base=$BASE, assets=${ASSETS:-none}, accept=$ACCEPT)"
  docker build --network host \
    -t "aimail-host-$p:native" -t "aimail-host-$p" \
    --build-arg BASE_IMG="$BASE" \
    --build-arg LLM_BASE_URL="$LLM_BASE_URL" --build-arg LLM_MODEL="$LLM_MODEL" \
    -f "$TMP/Dockerfile" "$TMP" \
    || { echo "✗ build failed: $p" >&2; rm -rf "$TMP"; exit 1; }
  rm -rf "$TMP"
done

echo "── built (pure native, environment accepted at build time):"
docker images --format '{{.Repository}}:{{.Tag}}  {{.CreatedSince}}' | grep aimail-host
