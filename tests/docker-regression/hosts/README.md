# 五平台宿主镜像 — 构建/拉取清单与用法
# 目的:纯净容器内测 aimail SDK × 真实宿主(官方/社区镜像优先;
# 无镜像的平台以 nikolaik/docker-python-nodejs:python3.12-nodejs22-slim
# 为基础自建,宿主本体用官方发布物(npm 包/源码)安装)。避免环境干扰。
#
# 镜像清单:
#   1. aimail-host-hermes   FROM ghcr.io/nousresearch/hermes-agent:latest   (官方)
#   2. aimail-host-openclaw FROM ghcr.io/openclaw/openclaw:latest            (官方)
#   3. aimail-host-dsh      FROM nikolaik/docker-python-nodejs:python3.12-nodejs22-slim
#                           + npx 安装官方 @deepseek-ai/dsh(无官方镜像)
#   4. aimail-host-pi       FROM nikolaik/... + npm -g @earendil-works/pi-coding-agent(自建)
#   5. aimail-host-deerflow FROM deer-flow-gateway:latest(本地既有自建;或 bytedance/deer-flow 源码构建)
#
# 构建:bash tests/docker-regression/hosts/build-hosts.sh [registry|repo] [version]
# 回归:bash tests/docker-regression/hosts/run-host-regression.sh <platform> <sid>
#       (每平台容器内:SDK 注册 → 绑定落盘 → 发信 → 清理;收信双路 E2E 宿主侧)
