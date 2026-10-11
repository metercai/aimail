#!/usr/bin/env bash
# agent 运行时路径的**单真源**(owner 2026-10-11 裁决)。
#
# 背景(为什么有本文件):launcher 的候选顺序原本在 verify.sh(两处)与 tests/l3/run-l3.sh
# (一处赋值)各写一份;2026-10-11 把 L3 接线到单源 agent-env 时, run-l3.sh 那份赋值连同
# `[ -x ]` 守卫被删掉, 只留下消费者 ⇒ CI 报 `HERMES_BIN: unbound variable`, L3 9/9 红
# (set -u 直接中断, hermes host 从未启动)。收敛到本文件后不再有第二份可漂移的副本。
#
# 用法(仅在 bash 脚本里 source;本文件无副作用):
#   . "$(dirname "${BASH_SOURCE[0]}")/agent-paths.sh"     # 同目录
#   bin="$(agent_launcher hermes "$HOME_DIR" || true)"
#
# agent_launcher <agent> [home_dir] —— 成功时打印可执行 launcher 的绝对路径并返回 0;
# 找不到则返回非零(调用方自行决定 die / red / gap)。

agent_launcher() {
  local agent="$1" home_dir="${2:-}" b="" t="" tree=""

  case "$agent" in
    hermes)
      # 官方宿主安装: 源码树在 <home>/hermes-agent, launcher 在 <tree>/.hermes/bin/hermes,
      # 并把一份放到 PATH(~/.local/bin/hermes)。docker 镜像里是 /opt/hermes/bin/hermes。
      for t in "${home_dir:-/nonexistent}/hermes-agent" "/opt/hermes"; do
        [ -f "$t/gateway/platforms/webhook.py" ] && { tree="$t"; break; }
      done
      for b in "${tree:-/nonexistent}/.hermes/bin/hermes" "/opt/hermes/bin/hermes" \
               "${home_dir:-/nonexistent}/.local/bin/hermes" "$HOME/.local/bin/hermes" \
               "$(command -v hermes 2>/dev/null)"; do
        [ -n "$b" ] && [ -x "$b" ] && { printf '%s\n' "$b"; return 0; }
      done
      return 1
      ;;
    dsh|pi|openclaw)
      b="$(command -v "$agent" 2>/dev/null)" || return 1
      [ -n "$b" ] || return 1
      printf '%s\n' "$b"
      return 0
      ;;
    *) return 1 ;;
  esac
}
