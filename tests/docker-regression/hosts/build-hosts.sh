#!/usr/bin/env bash
# build-hosts.sh — 构建/拉取五平台宿主镜像(PKG_SOURCE: registry=真实发布包 / repo=工作区)
# 用法: bash tests/docker-regression/hosts/build-hosts.sh [registry|repo] [version] [platform...]
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
cd "$ROOT"
SRC="${1:-registry}"; VER="${2:-0.1.10}"; shift 2 || true
PLATFORMS=("$@"); [ ${#PLATFORMS[@]} -eq 0 ] && PLATFORMS=(hermes openclaw dsh pi deerflow)
BASE_IMG=nikolaik/docker-python-nodejs:python3.12-nodejs22-slim
CTX=tests/docker-regression/hosts

for p in "${PLATFORMS[@]}"; do
  case "$p" in
    hermes)   echo "── pull 官方 hermes: ghcr.io/nousresearch/hermes-agent"; docker pull ghcr.io/nousresearch/hermes-agent:latest >/dev/null; docker tag ghcr.io/nousresearch/hermes-agent:latest aimail-host-hermes ;;
    openclaw) echo "── pull 官方 openclaw: ghcr.io/openclaw/openclaw"; docker pull ghcr.io/openclaw/openclaw:latest >/dev/null; docker tag ghcr.io/openclaw/openclaw:latest aimail-host-openclaw ;;
    dsh|pi)
      echo "── build $p(基于 $BASE_IMG + 官方 npm 包)"
      docker build -t "aimail-host-$p" \
        --build-arg BASE_IMG="$BASE_IMG" \
        --build-arg PKG_SOURCE="$SRC" --build-arg VERSION="$VER" \
        -f "$CTX/Dockerfile.$p" "$CTX" ;;
    deerflow)
      if docker image inspect deer-flow-gateway:latest >/dev/null 2>&1; then
        echo "── 复用本地自建 deer-flow-gateway"; docker tag deer-flow-gateway:latest aimail-host-deerflow
      else
        echo "deer-flow: 本地无自建镜像——先构建 deer-flow(bytedance/deer-flow 源码)"; exit 1
      fi ;;
    *) echo "未知平台 $p"; exit 1 ;;
  esac
done
echo "── 完成:"; docker images --format '{{.Repository}}:{{.Tag}}' | grep aimail-host
