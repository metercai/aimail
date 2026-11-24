#!/usr/bin/env bash
# rebuild-deerflow-base.sh — 从**源码检出**重建 deer-flow 基础镜像 `deer-flow-gateway:<rev>`。
#
# 为什么需要它（2026-09-27 实机取证，2026-11-23 收编进仓库）：
#   早期 `deer-flow-gateway:latest` 的层是 2026-05-24，早于 `DEER_FLOW_AUTH_DISABLED` 支持：
#   镜像内 `app/gateway/csrf_middleware.py::should_check_csrf` **没有** `is_auth_disabled`
#   分支（连 `app/gateway/auth_disabled.py` 模块都不存在）⇒ 入站 POST 被 CSRF 中间件
#   403 拦在路由之前（L2 夹具会把这种情况读成“宿主没反应”，属假绿家族③“被测物旧”）。
#   L2 的 deerflow 宿主必须从这个脚本产出的 `src-<rev>` 镜像构建。
#
# 为什么不是 `docker build -f backend/Dockerfile`：
#   在无 registry 出口的机器上，任何 `FROM <registry 镜像>` 的构建都拉不到底板。故本脚本
#   **完全不碰 registry**：以本机既有 runtime 层为底板（已含 python3.12 + uv + node/npm/npx
#   + /app 结构 = backend/Dockerfile 的第三阶段产物），把**当前源码检出**的 backend 全量
#   替换进去，在容器内重跑同一个 `uv sync --locked --extra redis`（宿主 uv 缓存挂进去 ⇒
#   不必重新下载 wheel），再 commit 成新 tag。首方代码与依赖环境 = 当前源码 + 当前 lock。
#
# 用法：
#   DEERFLOW_SRC=/path/to/deer-flow bash tests/agent-env/docker/rebuild-deerflow-base.sh
#   bash tests/agent-env/docker/rebuild-deerflow-base.sh --check     # 只校验 pin 与本地镜像
#
# 环境变量：
#   DEERFLOW_SRC          源码检出（默认 $HOME/deer-flow；只读挂载，不改第三方树）
#   DEERFLOW_FLOOR_IMAGE  本地底板镜像（默认 deer-flow-gateway:latest）
#   UV_CACHE_HOST_DIR     宿主 uv 缓存（默认 $HOME/.cache/uv；挂进容器复用 wheel）
#   DEERFLOW_BASE_TAG     产物 tag（默认 deer-flow-gateway:src-<短 rev>）
#   UV_INDEX_URL          容器内索引（默认 https://pypi.org/simple）
#
# 退出码：0 = 已产出/已校验；1 = 前置或构建失败（fail-loud，绝不静默降级成“旧镜像也能用”）。
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_DIR="$(cd "$HERE/.." && pwd)"

SRC="${DEERFLOW_SRC:-$HOME/deer-flow}"
FLOOR="${DEERFLOW_FLOOR_IMAGE:-deer-flow-gateway:latest}"
UVCACHE="${UV_CACHE_HOST_DIR:-$HOME/.cache/uv}"
IDX="${UV_INDEX_URL:-https://pypi.org/simple}"
CHECK_ONLY=0
for a in "$@"; do
  case "$a" in
    --check) CHECK_ONLY=1 ;;
    -h|--help) sed -n '2,40p' "${BASH_SOURCE[0]}"; exit 0 ;;
    *) echo "unknown argument: $a" >&2; exit 2 ;;
  esac
done

say(){ echo "  $*"; }
die(){ echo "✗ rebuild-deerflow-base: $*" >&2; exit 1; }

pin() {  # pin <KEY> —— 读 pins.env（唯一维护点），缺失即空
  [ -f "$ENV_DIR/pins.env" ] || return 0
  grep -E "^$1=" "$ENV_DIR/pins.env" | head -1 | cut -d= -f2-
}
PIN_REV="$(pin DEERFLOW_REV)"
PIN_BASE="$(pin BASE_DEERFLOW)"

require_source() {
  [ -d "$SRC" ] || die "源码检出不存在：$SRC（设 DEERFLOW_SRC=<path>）"
  [ -f "$SRC/backend/pyproject.toml" ] || die "不是 deer-flow 检出（缺 backend/pyproject.toml）：$SRC"
  [ -f "$SRC/backend/uv.lock" ] || die "缺 backend/uv.lock（锁定安装需要）"
  command -v git >/dev/null 2>&1 || die "缺 git（要记录源码版本）"
  [ -d "$SRC/.git" ] || die "不是 git 检出，无法记录版本：$SRC"
  # 被测支持必须在源码里，否则重建出来的东西照样不合格。
  grep -q "is_auth_disabled" "$SRC/backend/app/gateway/csrf_middleware.py" 2>/dev/null \
    || die "源码 csrf_middleware.py 无 is_auth_disabled 分支 ⇒ 重建也修不了 CSRF 403；检查检出/版本"
  [ -f "$SRC/backend/app/gateway/auth_disabled.py" ] \
    || die "源码缺 app/gateway/auth_disabled.py ⇒ 不是含该支持的版本"
}

REV=""; REVDATE=""; DIRTY=""; TAG=""
resolve_rev() {
  REV="$(git -C "$SRC" rev-parse --short HEAD 2>/dev/null)" || die "取不到源码 rev：$SRC"
  REVDATE="$(git -C "$SRC" log -1 --format='%cI %s' 2>/dev/null)"
  DIRTY="$(git -C "$SRC" status --porcelain 2>/dev/null | wc -l | tr -d ' ')"
  TAG="${DEERFLOW_BASE_TAG:-deer-flow-gateway:src-$REV}"
}

# ── --check：pin ⇄ 源码 ⇄ 本地镜像三方对齐（E 轨道的入口断言） ─────────────────
if [ "$CHECK_ONLY" = 1 ]; then
  echo "══ deer-flow pin 校验"
  [ -n "$PIN_REV" ] || die "pins.env 缺 DEERFLOW_REV（E1 未完成）"
  [ -n "$PIN_BASE" ] || die "pins.env 缺 BASE_DEERFLOW（E1 未完成）"
  say "pins: DEERFLOW_REV=$PIN_REV BASE_DEERFLOW=$PIN_BASE"
  if [ -d "$SRC/.git" ]; then
    resolve_rev
    say "源码: $SRC @ $REV (dirty=$DIRTY)"
    [ "$REV" = "$PIN_REV" ] || die "源码 rev $REV != pins DEERFLOW_REV $PIN_REV（先对齐 pin 或换检出）"
    [ "$DIRTY" = 0 ] || die "源码检出有 $DIRTY 个未提交改动 —— 重建结果不可复现，先清理"
  else
    say "源码: 不存在（跳过 rev 比对）"
  fi
  docker image inspect "$PIN_BASE" >/dev/null 2>&1 \
    && say "本地镜像: $PIN_BASE 存在（$(docker image inspect "$PIN_BASE" --format '{{.Id}}' | cut -c1-19)）" \
    || say "本地镜像: $PIN_BASE 不存在 —— 需要跑一次重建（不带 --check）"
  echo "OK: pin 一致"
  exit 0
fi

require_source
resolve_rev
docker image inspect "$FLOOR" >/dev/null 2>&1 \
  || die "底板镜像不在本地：$FLOOR（本机不能拉 registry，见文件头）"
[ -d "$UVCACHE" ] || die "uv 缓存目录不存在：$UVCACHE（设 UV_CACHE_HOST_DIR=<path>）"

echo "══ deer-flow 基础镜像重建（源码 → 本地底板，不碰 registry）"
say "源码: $SRC @ $REV ($REVDATE；未提交改动 $DIRTY 个文件)"
say "底板: $FLOOR ($(docker image inspect "$FLOOR" --format '{{.Id}}' | cut -c1-19))"
say "uv 缓存: $UVCACHE"
say "产物 tag: $TAG"
[ -n "$PIN_REV" ] && [ "$PIN_REV" != "$REV" ] \
  && say "注意: pins.env DEERFLOW_REV=$PIN_REV 与本次源码 $REV 不同 —— 产出后需更新 pin 或换检出"

CT="deerflow-base-rebuild-$$"
cleanup(){ docker rm -f "$CT" >/dev/null 2>&1 || true; }
trap cleanup EXIT

docker run -d --network host --name "$CT" \
  -v "$SRC/backend:/src-backend:ro" \
  -v "$UVCACHE:/root/.cache/uv" \
  "$FLOOR" tail -f /dev/null >/dev/null || die "构建容器起不来"
say "构建容器: $CT（源码 = /src-backend:ro）"

# 复刻 Dockerfile 的 `COPY backend ./backend`（全量替换，避免旧首方文件残留）
docker exec "$CT" bash -lc '
  set -e
  rm -rf /app/backend
  mkdir -p /app/backend
  cd /src-backend
  tar cf - --exclude=.venv --exclude=__pycache__ --exclude=.deer-flow . | (cd /app/backend && tar xf -)
  mkdir -p /app/backend/.deer-flow
  echo "copied_files=$(find /app/backend -type f | wc -l)"
' || die "源码拷贝失败"

say "uv sync --locked --extra redis（容器内；大步骤，可能数分钟）…"
if ! docker exec "$CT" bash -lc "cd /app/backend && UV_INDEX_URL=$IDX uv sync --locked --extra redis"; then
  say "uv sync 输出末尾："
  docker exec "$CT" bash -lc "cd /app/backend && UV_INDEX_URL=$IDX uv sync --locked --extra redis 2>&1 | tail -20" || true
  die "uv sync 失败（见上；若是编译类失败，说明底板缺工具链）"
fi

VERIFY="$(docker exec "$CT" bash -lc '
  cd /app/backend
  uv run --no-sync python - <<PY 2>&1 | tail -40
import importlib.metadata as md, inspect
from app.gateway.auth_disabled import is_auth_disabled, AUTH_DISABLED_ENV_VAR
import app.gateway.csrf_middleware as csrf
import app.gateway.auth_middleware as auth
print("auth_disabled_module=ok env_var=" + AUTH_DISABLED_ENV_VAR)
print("csrf_has_is_auth_disabled=%s" % ("is_auth_disabled" in inspect.getsource(csrf.should_check_csrf)))
print("authmw_has_is_auth_disabled=%s" % ("is_auth_disabled" in inspect.getsource(auth)))
for p in ("fastapi", "starlette", "uvicorn", "langgraph", "deerflow-harness"):
    try: print("pkg %s==%s" % (p, md.version(p)))
    except Exception as e: print("pkg %s MISSING (%s)" % (p, e))
PY
')" || die "验收脚本执行失败"
printf '%s\n' "$VERIFY" | sed 's/^/  │ /'
case "$VERIFY" in
  *"csrf_has_is_auth_disabled=True"*) : ;;
  *) die "验收失败：新镜像 should_check_csrf 仍不含 is_auth_disabled ⇒ 不许当重建成功" ;;
esac
case "$VERIFY" in
  *"auth_disabled_module=ok"*) : ;;
  *) die "验收失败：app.gateway.auth_disabled 导不进来" ;;
esac
case "$VERIFY" in
  *" MISSING ("*) die "验收失败：关键依赖缺失（见上 pkg 行）⇒ 环境不完整，不许打 tag" ;;
esac

docker commit \
  --change 'WORKDIR /app' \
  --change 'EXPOSE 8001' \
  --change 'CMD ["sh","-c","cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]' \
  "$CT" "$TAG" >/dev/null || die "docker commit 失败"

CMD_NOW="$(docker image inspect "$TAG" --format '{{json .Config.Cmd}}')"
EP_NOW="$(docker image inspect "$TAG" --format '{{json .Config.Entrypoint}}')"
[ "$CMD_NOW" = '["sh","-c","cd backend && PYTHONPATH=. uv run --no-sync uvicorn app.gateway.app:app --host 0.0.0.0 --port 8001"]' ] \
  || die "新镜像 CMD 不对：$CMD_NOW"
[ "$EP_NOW" = "null" ] || die "新镜像 ENTRYPOINT 被污染：$EP_NOW"

echo "── 重建结果"
say "IMAGE=$TAG"
say "image_id=$(docker image inspect "$TAG" --format '{{.Id}}' | cut -c1-19) size=$(docker image inspect "$TAG" --format '{{.Size}}')"
say "source_rev=$REV ($REVDATE)"
say "source_dirty_files=$DIRTY"
say "floor_image=$FLOOR"
say "cmd=$CMD_NOW"
say "entrypoint=$EP_NOW"
if [ -n "$PIN_BASE" ] && [ "$PIN_BASE" != "$TAG" ]; then
  say "注意: BASE_DEERFLOW=$PIN_BASE 与实际产物 $TAG 不同 —— 更新 pins.env 后才能被 Dockerfile/CI 引用"
fi
echo "OK: $TAG"
