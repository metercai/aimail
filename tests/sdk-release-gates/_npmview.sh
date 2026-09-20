#!/usr/bin/env bash
# _npmview.sh — 让门禁脚本区分两种"npm view 拿不到版本"的情形(审计 2026-09-21):
#   · E404 / no match found  → 版本确实**未发布**(正常状态)
#   · 其他错误(ETIMEDOUT/EAI_AGAIN/ENOTFOUND/权限…) → **查询失败**(硬失败, 必须报出来)
# 原写法 `npm view … 2>/dev/null || true` 把两者都变成"空字符串" ⇒ 网络抖动被当成
# "未发布", 根因丢失(历史上 E415/空包事故即此类)。
#
# 用法: npm_version <pkg> [<ver>]
#   成功(已发布) → stdout 打印版本号, rc=0
#   未发布       → stdout 空, rc=0
#   查询失败     → stdout 空, rc=2(调用方须 exit 1 并打印原因)
npm_version() {
  local pkg="$1" ver="${2:-}" spec out
  if [ -n "$ver" ]; then spec="$pkg@$ver"; else spec="$pkg"; fi
  if out=$(npm view "$spec" version 2>&1); then
    printf '%s' "$out"; return 0
  fi
  if printf '%s' "$out" | grep -qiE 'E404|no match found|not found|could not be found'; then
    return 0
  fi
  printf '%s\n' "npm query failed for $spec: $out" >&2
  return 2
}
