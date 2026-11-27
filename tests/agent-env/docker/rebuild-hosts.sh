#!/usr/bin/env bash
# rebuild-hosts.sh — 本地重建 L2/L3 宿主镜像（与 .github/workflows/host-images.yml 同一套参数）。
#
# 为什么需要：镜像里 agent 的版本由 pins.env 钉死；升版本或修镜像（例如给 hermes 的 venv 补 pip）
# 之后必须重建，否则 L2/L3 跑的还是旧层——"被测物旧"是历史上最常见的假绿来源。
#
# 用法：bash tests/agent-env/docker/rebuild-hosts.sh [dsh pi openclaw hermes deerflow]   # 默认全建
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../../.." && pwd)"
LLM_BASE_URL="${LLM_BASE_URL:-https://api.xiaomimimo.com/v1}"
LLM_MODEL="${LLM_MODEL:-mimo-v2.6-flash}"
HOSTS=("$@"); [ ${#HOSTS[@]} -gt 0 ] || HOSTS=(dsh pi openclaw hermes deerflow)

# shellcheck disable=SC1091
. "$ROOT/tests/agent-env/pins.env"

for a in "${HOSTS[@]}"; do
  case "$a" in
    dsh)      BASE="$BASE_NODE" ;;
    pi)       BASE="$BASE_NODE" ;;
    openclaw) BASE="$BASE_OPENCLAW" ;;
    hermes)   BASE="$BASE_HERMES" ;;
    deerflow) BASE="$BASE_DEERFLOW" ;;
    *) echo "unknown host $a" >&2; exit 2 ;;
  esac
  CTX="$(mktemp -d)"; mkdir -p "$CTX/agent-env/lib" "$CTX/extras" "$CTX/docker"
  cp "$ROOT"/tests/agent-env/{prepare.sh,verify.sh,pins.env,agent-paths.sh,set-llm-key.sh} "$CTX/agent-env/"
  cp "$ROOT/tests/agent-env/lib/llm-config.py" "$CTX/agent-env/lib/"
  cp "$ROOT/tests/agent-env/docker/pnpm-warm.json" "$CTX/docker/"
  cp "$ROOT/tests/agent-env/docker/Dockerfile.$a" "$CTX/Dockerfile"
  EXTRA=(); [ "$a" = deerflow ] && EXTRA=(--build-arg "DEERFLOW_REV=${DEERFLOW_REV}")
  echo "── build $a (base=${BASE%%@*}@…)"
  build_with() {
    docker build --network host -t "aimail-host-$a:latest" \
      --build-arg BASE_IMG="$1" \
      --build-arg LLM_BASE_URL="$LLM_BASE_URL" --build-arg LLM_MODEL="$LLM_MODEL" \
      "${EXTRA[@]+"${EXTRA[@]}"}" -f "$CTX/Dockerfile" "$CTX"
  }
  # A digest HEAD against a mirror can hang instead of failing, which would wedge the rebuild before
  # the fallback below ever runs. Probe first, with a deadline (FORCE_LOCAL_BASE=1 skips probing).
  probe_base() {
    [ "${FORCE_LOCAL_BASE:-0}" = 1 ] && return 1
    timeout "${BASE_PROBE_TIMEOUT:-20}" docker manifest inspect "$1" >/dev/null 2>&1
  }
  local_tag_fallback() {
    # Offline/behind-a-mirror machines cannot resolve a digest HEAD (HTTP 403 from the mirror).
    # Fall back to the LOCAL tag — only safe because we verify it is the pinned digest.
    TAGREF="${BASE%@*}"
    LOCAL_DIGEST="$(docker image inspect "$TAGREF" --format '{{index .RepoDigests 0}}' 2>/dev/null | sed 's/.*@//')"
    PINNED_DIGEST="${BASE##*@}"
    if [ -z "$LOCAL_DIGEST" ] || [ "$LOCAL_DIGEST" != "$PINNED_DIGEST" ]; then
      echo "build $a FAILED: pinned digest not resolvable and local tag does not match (local=${LOCAL_DIGEST:-none} pinned=$PINNED_DIGEST)" >&2
      exit 1
    fi
    echo "   (registry HEAD blocked; local $TAGREF already IS $PINNED_DIGEST → building from the local tag)"
    build_with "$TAGREF" || { echo "build $a FAILED" >&2; exit 1; }
  }
  if probe_base "$BASE"; then
    build_with "$BASE" || { echo "build $a FAILED" >&2; exit 1; }
  else
    local_tag_fallback
  fi
  rm -rf "$CTX"
  echo "── $a ok"
done
