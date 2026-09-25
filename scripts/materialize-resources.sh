#!/usr/bin/env bash
# materialize-resources.sh — SDK 资源单一真源 → 各分发点物化。
#
# 单一真源 = 仓根 resources/{board,skills}(git 跟踪, 唯一可编辑处)。
# 物化点(生成物, 已 gitignore, **禁提交**):
#   pysdk/resources                              (仓库态 pysdk 代码路径 + CLI 载荷源)
#   tssdk/packages/<pkg>/resources               (npm 产物自包含: dsh/openclaw/pi)
#
# 用法:
#   scripts/materialize-resources.sh [pysdk|dsh|openclaw|pi]...   # 无参 = 全部
#   scripts/materialize-resources.sh --verify                    # 校验 4 点与真源逐字节一致(门禁用)
#
# 谁该调它: ①开发态改动 resources/ 后 ②L0 门禁/CI 打包前 ③TS 包 prepack(见各 package.json)
# 幂等: rsync 可用时只同步差异; 否则整体重写。--verify 不改任何文件。
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SRC="$REPO/resources"

target_path() {
  case "$1" in
    pysdk)    echo "$REPO/pysdk/resources" ;;
    dsh)      echo "$REPO/tssdk/packages/dsh-aimail/resources" ;;
    openclaw) echo "$REPO/tssdk/packages/openclaw-aimail/resources" ;;
    pi)       echo "$REPO/tssdk/packages/pi-aimail/resources" ;;
    *)        echo "" ;;
  esac
}
ALL_TARGETS=(pysdk dsh openclaw pi)

if [ ! -d "$SRC/board" ] || [ ! -d "$SRC/skills" ]; then
  echo "ERROR: canonical resources 缺失: $SRC/{board,skills} 不存在" >&2
  exit 2
fi

fingerprint() {  # 目录内容指纹(相对路径 + 内容), 与 /tmp 版比对脚本同源判据
  ( cd "$1" && find . -type f -print0 | LC_ALL=C sort -z | xargs -0 sha256sum )
}

if [ "${1:-}" = "--verify" ]; then
  bad=0
  want="$(fingerprint "$SRC")"
  for t in "${ALL_TARGETS[@]}"; do
    dst="$(target_path "$t")"
    if [ ! -d "$dst" ]; then
      echo "  ✗ $t 未物化: ${dst#$REPO/} 不存在(跑 scripts/materialize-resources.sh)"
      bad=$((bad + 1))
    elif [ "$(fingerprint "$dst")" != "$want" ]; then
      echo "  ✗ $t 与真源不一致: ${dst#$REPO/}"
      bad=$((bad + 1))
    else
      echo "  ✓ $t 与真源逐字节一致"
    fi
  done
  [ "$bad" -eq 0 ] || { echo "resources 物化校验失败($bad 处)"; exit 1; }
  echo "resources 物化校验通过(4 点 == 仓根 resources/)"
  exit 0
fi

want=("$@")
[ "${#want[@]}" -eq 0 ] && want=("${ALL_TARGETS[@]}")
for t in "${want[@]}"; do
  dst="$(target_path "$t")"
  if [ -z "$dst" ]; then
    echo "ERROR: 未知目标 '$t'(可选: ${ALL_TARGETS[*]})" >&2
    exit 2
  fi
  if command -v rsync >/dev/null 2>&1; then
    # --delete: 目标多出的文件也清掉 ⇒ 结果与真源严格一致(旧文件不会残留)
    rsync -a --delete "$SRC/" "$dst/"
  else
    rm -rf "$dst"
    mkdir -p "$dst"
    cp -a "$SRC/." "$dst/"
  fi
  echo "materialized: $t → ${dst#"$REPO"/}"
done
