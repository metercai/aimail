#!/usr/bin/env bash
# install-skill-tools.sh — dsh agent 侧装配: 在 web profile 的 patch 层**启用**
#   skill-filesystem(扫 <dshHome>/skills 下的 SKILL.md)与 tool-skill(把 skill
#   目录渲染成模型可见的 catalog/tool)。
#
# 为什么必须启用: dsh-base 默认把这两行写成 `disabled: true` ⇒ skills 落点再对
#   也进不了会话交给模型的工具/技能清单(落点对、没暴露)。
# 落点: $DSH_HOME/profiles/$DSH_PROFILE/cordis.patch.yml —— dsh 自己的 patch 层
#   (bundle → profile patch → home patch → --patch, 后层按 id 覆盖前层)。
#   写 profile 层而不是 bundle: bundle 随 npm 包走 ⇒ 装配必须等发版; profile 层
#   安装时即可生效, 与既有 install-mcp.sh/install-skill.sh 同款"安装链写入"口径。
# 幂等: 两行都已在位且 disabled:false ⇒ 不写(重复 install 不产生重复写);
#   行在但 disabled:true ⇒ 就地翻 false(该行其它键原样保留); 行缺 ⇒ 追加。
# 任一步失败 ⇒ exit 1(装配失败必须可见, 不静默降级)。
set -euo pipefail

DSH_HOME="${DSH_HOME:-${HOME:-/root}/.dsh}"
DSH_PROFILE="${DSH_PROFILE:-web}"
PATCH="$DSH_HOME/profiles/$DSH_PROFILE/cordis.patch.yml"
IDS="skill-filesystem tool-skill"

if ! command -v awk >/dev/null 2>&1; then
  echo "ERROR: 未找到 awk —— 无法装配 dsh skill/tool 暴露层" >&2
  exit 1
fi

mkdir -p "$(dirname "$PATCH")"
TMP="$(mktemp)"
trap 'rm -f "$TMP" "$TMP.new"' EXIT
if [ -f "$PATCH" ]; then
  cp "$PATCH" "$TMP"
else
  : > "$TMP"
fi

# 归一化: 我们的两行统一成 `disabled: false`(行在就地翻, 行缺 EOF 追加);
# 只认列 0 的顶层 `- id:`(profile patch 的目标行形态), 不碰 `insert:` 里的嵌套行。
awk -v ids="$IDS" '
BEGIN { n = split(ids, ID, " "); for (i = 1; i <= n; i++) want[ID[i]] = 1; pending = "" }
{
  if ($0 ~ /^-[ \t]*id[ \t]*:/) {
    if (pending != "") { print "  disabled: false"; pending = "" }   # 上一行没写 disabled
    line = $0
    sub(/^-[ \t]*id[ \t]*:[ \t]*/, "", line)
    sub(/[ \t]*$/, "", line)
    if (line in want) { seen[line] = 1; pending = line }
    print; next
  }
  if (pending != "" && $0 ~ /^  disabled[ \t]*:/) { print "  disabled: false"; pending = ""; next }
  print
}
END {
  if (pending != "") print "  disabled: false"
  for (i = 1; i <= n; i++) if (!(ID[i] in seen)) { print "- id: " ID[i]; print "  disabled: false" }
}
' "$TMP" > "$TMP.new"

if [ -s "$PATCH" ] && cmp -s "$TMP" "$TMP.new"; then
  echo "  ✓ dsh skill/tool 暴露层已在位且启用(跳过写入): $PATCH"
else
  mv "$TMP.new" "$PATCH"
  TMP.new_removed=1
  echo "  ✓ dsh skill/tool 暴露层已写入 → $PATCH(skill-filesystem/tool-skill: disabled: false)"
fi

# 自检: 两行都必须真的在位且是 false, 否则装配不算完成(响亮失败)
for id in $IDS; do
  if ! awk -v id="$id" '
    /^-[ \t]*id[ \t]*:/ { line = $0; sub(/^-[ \t]*id[ \t]*:[ \t]*/, "", line);
                          sub(/[ \t]*$/, "", line); cur = (line == id); next }
    cur && /^  disabled[ \t]*:.*false/ { found = 1 }
    END { exit(found ? 0 : 1) }
  ' "$PATCH"; then
    echo "ERROR: $PATCH 里 $id 未处于 enabled(disabled: false)状态 —— 装配未生效" >&2
    exit 1
  fi
done
echo "verify: dsh --profile $DSH_PROFILE --dump-config | grep -A2 'id: skill-filesystem'"
