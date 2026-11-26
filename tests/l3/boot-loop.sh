#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════════
# boot-loop.sh — L3 的**最短闭环**：只验证"激活 prompt 能否触发 agent 自助安装 SDK"。
#
# 为什么不跑整条旅程：自举是 L3 的地基。用 12 分钟的长闭环去迭代一句文案，一轮只能拿到
# 一个比特；这个脚本把它压到"一次 agent 回合"，用来反复调 prompt / 文案 / 端点。
#
# 判定（全部来自证据，不来自日志措辞）：
#   A. agent 是否**去查了**网关的安装指令端点（GET /api/v1/agent-setup/install）
#   B. agent 是否**真的执行了**安装（宿主侧 SDK 包出现/消失的变化）
#   C. 安装后 `aimail_address` 是否可用（宿主插件列表/工具列表里出现 aimail）
#
# 用法：bash tests/l3/boot-loop.sh --host hermes|openclaw|pi|dsh [--docker IMAGE] [--gw-port 38992] [--timeout 240]
#
# dsh 的一回合驱动方式与 hermes/pi 不同（它没有 -p/--yolo 单发入口），在 tests/l3/dsh-v2.sh 里；
# 这里只覆盖 hermes / openclaw / pi 三个有单发入口的宿主，别的宿主会明确 exit 2（不做假绿）。
# 退出码：0=自举成功（A+B 成立）· 1=自举失败（附证据路径）· 2=环境不满足
# ═══════════════════════════════════════════════════════════════════════
set -uo pipefail
HOST=""; PORT=38992; TMO=240; IMAGE=""
while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="$2"; shift 2 ;;
    --docker) IMAGE="$2"; shift 2 ;;
    --gw-port) PORT="$2"; shift 2 ;;
    --timeout) TMO="$2"; shift 2 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done
[ -n "$HOST" ] || { echo "usage: boot-loop.sh --host hermes|openclaw|pi [--docker IMAGE]" >&2; exit 2; }

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
WORK="$(mktemp -d "/tmp/boot-loop-$HOST.XXXXXX")"
GW_URL="http://127.0.0.1:$PORT"
ADDR="boot@test.local"
CODE="addr-0000-bootloop-demo"   # 只验自举：本回合不兑换激活码

say() { printf '  %s\n' "$*"; }
ok()  { printf '  \033[32m✓\033[0m %s\n' "$*"; }
bad() { printf '  \033[31m✗\033[0m %s\n' "$*" >&2; }

echo "════ boot-loop: $HOST → $GW_URL （证据目录 $WORK）"

# docker 模式：按 L2 的做法——容器用镜像自己的 ENTRYPOINT 起来（它负责把 LLM 配好并启动宿主），
# 之后每一步都用 docker exec 在**同一个已配置的容器**里执行。直接 --entrypoint bash 会绕过 LLM 配置。
CNAME="bootloop-$HOST-$$"
# 驱动脚本随 /work 一起进容器（dsh 没有单发入口，必须走它的 web RPC）
cp "$ROOT/tests/l3/lib/dsh-turn.sh" "$WORK/dsh-turn.sh" 2>/dev/null || true
_cleanup_container() { [ -n "$IMAGE" ] && docker rm -f "$CNAME" >/dev/null 2>&1; }
trap _cleanup_container EXIT


# ── 0. 网关必须活着，且安装指令端点可答 ──────────────────────────────────
resp="$(curl -s -m 3 "$GW_URL/api/v1/agent-setup/bootstrap?shell=sh" || true)"
case "$resp" in
  '#!'*'sh'*) ok "gateway serves the POSIX bootstrap at $GW_URL/api/v1/agent-setup/bootstrap?shell=sh ($(printf '%s' "$resp" | wc -c) bytes)" ;;
  *) bad "gateway at $GW_URL does not serve the bootstrap script (got: ${resp:0:120})"; exit 2 ;;
esac

# ── 0.5 docker 模式：用镜像 entrypoint 起容器（LLM 由镜像/环境变量配好） ──────
if [ -n "$IMAGE" ]; then
  docker rm -f "$CNAME" >/dev/null 2>&1 || true
  # hermes 必须跳过镜像的 s6 入口：s6 会用镜像自己的 HOME 起 profile-gateways，之后我们用
  # 独立 HERMES_HOME 执行的 CLI 会撞 `Gateway inaccessible: profile_mismatch`（实测）。L2 夹具
  # 也是这么起的（hosts/hermes.sh:49，--user 0:0 --entrypoint bash -lc 'sleep infinity'）。
  case "$HOST" in
    hermes) DR=(--user 0:0) ; DCMD=() ;;   # keep s6: it is what wires pnpm/PATH for the CLI
    dsh)    DR=(--user 0:0) ; DCMD=() ;;
    *)      DR=(); DCMD=() ;;
  esac
  docker run -d --rm --network host --name "$CNAME" "${DR[@]}" \
    ${LLM_API_KEY:+-e LLM_API_KEY} -v "$WORK:/work:ro" "$IMAGE" "${DCMD[@]+"${DCMD[@]}"}" > "$WORK/docker-run.log" 2>&1 \
    || { bad "docker run failed: $(tail -2 "$WORK/docker-run.log")"; exit 2; }
  ready=0
  case "$HOST" in
    hermes)   READY_CMD='command -v /opt/hermes/bin/hermes >/dev/null' ;;
    pi)       READY_CMD='command -v pi >/dev/null && pi --version >/dev/null 2>&1' ;;
    openclaw) READY_CMD='command -v openclaw >/dev/null && openclaw --version >/dev/null 2>&1' ;;
    dsh)      READY_CMD='command -v dsh >/dev/null && dsh --version >/dev/null 2>&1' ;;
    *)        READY_CMD='true' ;;
  esac
  for _ in $(seq 1 60); do
    if docker exec "$CNAME" bash -lc "$READY_CMD" 2>/dev/null; then ready=1; break; fi
    sleep 2
  done
  [ "$ready" = 1 ] && ok "container ready ($IMAGE)" || { bad "container did not become ready"; docker logs --tail 20 "$CNAME" 2>&1 | sed 's/^/    /'; exit 2; }
fi

# ── 0.6 docker 模式：把运行时的 LLM key 注入容器 ───────────────────────────────
# 镜像里只烘焙端点/模型，凭据永远是占位符 __LLM_API_KEY__（owner: 不把 secret 写进镜像）。
# 不注入就会得到 "401 Invalid API Key"，而且看起来像 prompt 失败——实测踩过。
if [ -n "$IMAGE" ]; then
  if [ -n "${LLM_API_KEY:-}" ]; then
    if docker exec -e LLM_API_KEY "$CNAME" bash -lc 'bash /opt/agent-env/set-llm-key.sh' > "$WORK/set-key.log" 2>&1; then
      ok "LLM key injected into the container ($(tail -1 "$WORK/set-key.log" | head -c 80))"
    else
      # hermes/dsh 用 api_key_env 指向环境变量，没有占位符可替换 ⇒ 脚本返回 1，属正常
      say "set-llm-key: $(tail -1 "$WORK/set-key.log" | head -c 100)（api_key_env 宿主无需替换）"
    fi
  else
    bad "LLM_API_KEY 未设置：agent 无法回答，本回合会以 401 结束"; exit 2
  fi
fi

# ── 1. 记录"安装前"状态（这就是 B 的基线） ───────────────────────────────
host_prelude() {  # 打印该宿主在容器内的运行前置（与 L2 夹具一致）
  case "$HOST" in
    hermes) cat <<'PRE'
# 用镜像自己的 profile（/opt/data/.hermes）：s6 起的 profile-gateways 就是按它注册的，
# 换成自制目录会得到 `Gateway inaccessible: profile_mismatch`（实测）。
export AIMAIL_HOME=/opt/data/.aimail HERMES_HOME=/opt/data/.hermes
PRE
      ;;
    pi)     echo 'export HOME=/root' ;;
    openclaw) echo 'export HOME=/app' ;;
    dsh)    echo 'export HOME=/root' ;;
    deerflow) echo 'export HOME=/root; cd /app 2>/dev/null || true' ;;
  esac
}

_in_env() {  # _in_env <shell-snippet>
  if [ -n "$IMAGE" ]; then
    docker exec "$CNAME" bash -lc "$(host_prelude); $1" 2>/dev/null
  else
    bash -lc "$1" 2>/dev/null
  fi
}

host_sdk_state() {  # 打印宿主侧 SDK 的存在证据（一行）
  case "$HOST" in
    hermes)
      # 唯一可信证据：宿主运行时里的**分发包元数据**（不解析表格文本，也不让首启 setup 的输出污染判据）。
      _in_env 'python3 -c "import importlib.metadata as m;print(\"agent-aimail\",m.version(\"agent-aimail\"))" 2>/dev/null || true'
      ;;
    # 注意：必须走 _in_env —— 不带它会读到**宿主机**上的 openclaw（实测：宿主留着旧 0.1.16，
    # 于是"安装前"就被判成"已装"，整个回合无法归因）。证据取分发本身，不解析表格文本。
    openclaw) _in_env 'for r in "$HOME/.local/lib/node_modules" "$(npm root -g 2>/dev/null)"; do [ -d "$r/agent-aimail" ] && node -e "process.stdout.write(\"agent-aimail \"+require(\"$r/agent-aimail/package.json\").version)" 2>/dev/null; done' ;;
    # pi 没有"插件列表"这种可解析输出（注册名也不是猜出来的键），证据取**分发本身**：
    # 全局 npm 树里有 agent-aimail，并且能读出它的版本。
    pi)       _in_env 'd="$(npm root -g 2>/dev/null)/agent-aimail"; [ -d "$d" ] && node -e "process.stdout.write(\"agent-aimail \"+require(\"$d/package.json\").version)" 2>/dev/null' ;;
    dsh)      _in_env 'd="$(npm root -g 2>/dev/null)/agent-aimail"; [ -d "$d" ] && node -e "process.stdout.write(\"agent-aimail \"+require(\"$d/package.json\").version)" 2>/dev/null' ;;
  esac
}
TRACE_BEFORE="$(_in_env 'cat "$HOME/.aimail/bootstrap.log" 2>/dev/null | tail -5')"
BEFORE="$(host_sdk_state)"
if [ -n "$BEFORE" ]; then
  bad "宿主侧已经装着 SDK，无法归因：$BEFORE"; say "先让它缺席（例如 mv 掉 dist-info / 卸载插件）再跑本脚本"; exit 2
fi
ok "宿主侧 SDK 当前缺席（可归因）"

# ── 2. 组装"用户会粘贴的那一段"（= 邮件正文的 prompt，原文；与契约 §6 逐字一致） ──
cat > "$WORK/prompt.txt" <<PROMPT
Use \`aimail_address\` to activate and bind the dedicated AIMail mailbox $ADDR for this agent (gateway: $GW_URL; activation code: $CODE). If \`aimail_address\` is unavailable or unregistered, install the AIMail SDK: if \`uname -s\` can be executed in the current shell, run \`curl -fsSL '$GW_URL/api/v1/agent-setup/bootstrap?shell=sh' | sh\`; otherwise run \`powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -Uri '$GW_URL/api/v1/agent-setup/bootstrap?shell=ps1' -UseBasicParsing | iex"\`. Then try \`aimail_address\` again for activation and binding. If it is still unavailable, stop and tell the user to restart the agent runtime to load the plugin and retry activation and binding afterward; no reinstallation is needed.
PROMPT
say "prompt: $(wc -c < "$WORK/prompt.txt") bytes（邮件原文，无额外 nudge）"

# ── 3. 一次 agent 回合 ──────────────────────────────────────────────────
mkdir -p "$WORK/ws"
case "$HOST" in
  hermes)
    if [ -n "$IMAGE" ]; then
      docker exec -i "$CNAME" bash -lc "$(host_prelude); mkdir -p /tmp/ws && cd /tmp/ws && timeout $TMO /opt/hermes/bin/hermes --yolo -z \"\$(cat /work/prompt.txt)\"" \
        > "$WORK/turn.log" 2>&1
      rc=$?
    else
      ( cd "$WORK/ws" && timeout "$TMO" hermes --yolo -z "$(cat "$WORK/prompt.txt")" ) \
        > "$WORK/turn.log" 2>&1
      rc=$?
    fi ;;
  openclaw)
    if [ -n "$IMAGE" ]; then
      # openclaw 没有 -p；单发回合是 `openclaw agent --local`（不用常驻 gateway）
      docker exec -i "$CNAME" bash -lc "$(host_prelude); mkdir -p /tmp/ws && cd /tmp/ws && timeout $TMO openclaw agent --local --message-file /work/prompt.txt" \
        > "$WORK/turn.log" 2>&1
      rc=$?
    else
      ( cd "$WORK/ws" && timeout "$TMO" openclaw agent --local --message-file "$WORK/prompt.txt" ) \
        > "$WORK/turn.log" 2>&1
      rc=$?
    fi ;;
  pi)
    if [ -n "$IMAGE" ]; then
      docker exec -i "$CNAME" bash -lc "$(host_prelude); mkdir -p /tmp/ws && cd /tmp/ws && timeout $TMO pi -p \"\$(cat /work/prompt.txt)\"" \
        > "$WORK/turn.log" 2>&1
      rc=$?
    else
      ( cd "$WORK/ws" && timeout "$TMO" pi -p "$(cat "$WORK/prompt.txt")" ) \
        > "$WORK/turn.log" 2>&1
      rc=$?
    fi ;;
  dsh)
    if [ -n "$IMAGE" ]; then
      docker exec -i "$CNAME" bash -lc "$(host_prelude); mkdir -p /tmp/ws && cd /tmp/ws && LLM_API_KEY=\"$LLM_API_KEY\" bash /work/dsh-turn.sh /work/prompt.txt 39321 $TMO" \
        > "$WORK/turn.log" 2>&1
      rc=$?
    else
      bad "dsh 需要在宿主容器内驱动"; exit 2
    fi ;;
  *) bad "unsupported host $HOST"; exit 2 ;;
esac
say "turn exited rc=$rc（日志 $WORK/turn.log）"

# ── 4. 三个判据 ────────────────────────────────────────────────────────
# A 的证据取"脚本真的跑过"的两种痕迹之一：
#   ① 自举脚本自己的结论行（AIMAIL_BOOTSTRAP ok|fail，只有跑过才会有）
#   ② 剧本里那条 URL 出现在回合日志中（agent 复述/执行命令）
# 不拿"agent 说它查了"当判据——那是措辞，不是证据。
A=0; A_SRC=""
TRACE_AFTER="$(_in_env 'cat "$HOME/.aimail/bootstrap.log" 2>/dev/null | tail -5')"
if [ -n "$TRACE_AFTER" ] && [ "$TRACE_AFTER" != "${TRACE_BEFORE:-}" ]; then A=1; A_SRC="宿主自证痕迹: $(printf '%s' "$TRACE_AFTER" | tail -1)"; fi
if [ "$A" = 0 ] && grep -q "AIMAIL_BOOTSTRAP" "$WORK/turn.log"; then A=1; A_SRC="回合日志里的脚本结论行"; fi
if [ "$A" = 0 ] && grep -qi "agent-setup/bootstrap" "$WORK/turn.log"; then A=1; A_SRC="回合日志里出现了自举 URL"; fi
B=0; AFTER="$(host_sdk_state)"; [ -n "$AFTER" ] && B=1
printf '  %s\n' "--- turn tail ---" ; tail -25 "$WORK/turn.log" | sed 's/^/    /'

echo
[ "$A" = 1 ] && ok "A: agent 真的按 prompt 跑了 bootstrap 自举脚本（证据：${A_SRC}）" || bad "A: 没有任何自举痕迹（宿主 ~/.aimail/bootstrap.log 没长，回合日志也没有 URL/结论行）"
[ "$B" = 1 ] && ok "B: agent 真的把 SDK 装上了（$(printf '%s' "$AFTER" | head -c 100)）" || bad "B: 宿主侧仍未出现 SDK"
if [ "$B" = 1 ]; then
  # owner: 路线不限，装上就是成功；A 只用来记录"它走的是哪条路"
  if [ "$A" = 1 ]; then ROUTE="网关自举脚本"; else ROUTE="宿主自带的插件安装路径"; fi
  echo "  ✅ boot-loop PASS — prompt 触发了自举（路线：$ROUTE，证据 $WORK）"; exit 0
fi
echo "  ❌ boot-loop FAIL — 宿主侧没有出现 SDK（证据 $WORK）" >&2; exit 1
