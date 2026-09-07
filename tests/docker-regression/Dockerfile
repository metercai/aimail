# SDK 上线回归 — Docker 纯净环境 + 真实网关(aimail.token.tm)
# 用途:L2.5 发布前宿主行为回归的容器化执行器。
# 纯净容器(无本机宿主污染)重演主流场景;云侧网关 = 生产 aimail.token.tm
# (真实环境),发布后用真实 registry 包做最后上线回归,确保万无一失。
#
# Build:
#   docker build -t aimail-regression -f tests/docker-regression/Dockerfile .
#   (仓库根上下文;--build-arg PKG_SOURCE=registry 用真实发布包,
#    PKG_SOURCE=repo 用当前工作区(rc 开发期))
# Run:
#   docker run --rm --network host \
#     -v "$HOME/.aimail:/root/.aimail:ro" \        # 系统 cfg(admin_key 复用)
#     aimail-regression bash /opt/regression/run-scenarios.sh <sid> <gateway-url>
FROM node:20-slim AS ts-base
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-pip \
    python3-venv curl jq && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/regression
ARG PKG_SOURCE=registry
ARG VERSION=0.1.10
ENV PKG_SOURCE=${PKG_SOURCE} VERSION=${VERSION}

# 1) CLI(共享库 aimailsdk;cli/aimail 经 bootstrap 或 pysdk 包)
RUN python3 -m venv /opt/venv && /opt/venv/bin/pip install -q -U pip
RUN if [ "$PKG_SOURCE" = "registry" ]; then \
      /opt/venv/bin/pip install -q aimailsdk==${VERSION}; \
    fi

# 2) TS SDK 真实包(dsh-aimail / pi-aimail / openclaw-aimail / mail-core / mail)
RUN mkdir -p /opt/host-profiles/web/node_modules /opt/host-pi/agent/npm/node_modules
RUN npm install --prefix /opt/tsdeps --no-audit --no-fund \
      dsh-aimail@${VERSION} pi-aimail@${VERSION} openclaw-aimail@${VERSION} 2>/dev/null; \
    cp -r /opt/tsdeps/node_modules/* /opt/host-profiles/web/node_modules/ 2>/dev/null; \
    mkdir -p /opt/host-pi/agent/npm/node_modules && \
    cp -r /opt/tsdeps/node_modules/pi-aimail /opt/tsdeps/node_modules/@aimail /opt/host-pi/agent/npm/node_modules/ 2>/dev/null; \
    true

# 场景脚本 + 断言库
COPY tests/docker-regression/*.sh /opt/regression/
RUN chmod +x /opt/regression/*.sh

# 默认入口:打印场景清单(参数化运行见 README)
CMD ["bash", "/opt/regression/run-scenarios.sh"]
