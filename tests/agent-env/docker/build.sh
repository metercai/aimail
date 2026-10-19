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

# Pinned bases (single source: ../pins.env, maintained by ../update-pins.sh). A rebuild
# must not silently pick up a newer base image.
[ -f "$ENVDIR/pins.env" ] || { echo "✗ $ENVDIR/pins.env missing (the pinned versions are the testing start point)" >&2; exit 1; }
. "$ENVDIR/pins.env"

for p in "${PLATFORMS[@]}"; do
  case "$p" in
    hermes)   BASE="$BASE_HERMES" ;;
    openclaw) BASE="$BASE_OPENCLAW" ;;
    dsh|pi)   BASE="${BASE_IMG:-$BASE_NODE}" ;;
    deerflow)
      BASE="${DEERFLOW_BASE_IMG:-$BASE_DEERFLOW}"
      docker image inspect "$BASE" >/dev/null 2>&1 \
        || { echo "✗ deerflow base missing: $BASE — build it from source first (rebuild-deerflow-base.sh); update pins.env when you move the source commit" >&2; exit 1; } ;;
    *) echo "✗ unknown agent: $p" >&2; exit 2 ;;
  esac
  [ -f "$CTX/Dockerfile.$p" ] || { echo "✗ missing $CTX/Dockerfile.$p" >&2; exit 2; }

  # Digest-first: the pin is <repo>:<tag>@<digest>, and the digest is what must be enforced.
  # Some registry paths (mirrors) cannot serve a digest ref at all — fall back to the tag
  # and say so, rather than failing the build or pretending the base is pinned.
  case "$BASE" in
    *@sha256:*)
      if ! docker pull -q "$BASE" >/dev/null 2>&1; then
        _tag="$(printf '%s' "$BASE" | sed 's/@sha256:.*//')"
        echo "  ! base digest not resolvable through this registry path:" >&2
        echo "    $BASE" >&2
        echo "    falling back to the tag ($_tag) — the built image may differ from the pin" >&2
        BASE="$_tag"
        docker pull -q "$BASE" >/dev/null 2>&1 || true
      fi ;;
  esac

  TMP="$(mktemp -d)"
  mkdir -p "$TMP/agent-env/lib" "$TMP/extras"
  cp "$ENVDIR/prepare.sh" "$ENVDIR/verify.sh" "$ENVDIR/pins.env" "$ENVDIR/agent-paths.sh" "$TMP/agent-env/"
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
