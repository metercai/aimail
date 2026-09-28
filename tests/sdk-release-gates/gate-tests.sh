#!/usr/bin/env bash
# Release gate L0 — full test sweep before ANY publish (local + CI).
# Run from repo root (or anywhere; script locates the root itself).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "═══ [L0] python lint + unit tests ═══"
# SDK 资源单一真源(2026-09-25): 真源=仓根 resources/, 4 处分发点是物化产物(已 gitignore)。
# clean clone 里产物不存在 ⇒ 先物化, 否则 pysdk/runtime_bundle 的资源释放与 S9 探针会红。
bash scripts/materialize-resources.sh
bash scripts/materialize-resources.sh --verify
# 契约单一真源 gate(2026-09-27): agent 内部契约面(skill/toolset 注册名、绑定文件名、
# 指针文件名、入站路径)的单一真源 =
# contract/aimail-contract.json;由 ①两个常量模块(pysdk/aimail_contract.py +
# tssdk/.../mail-core/src/contract.ts)逐项 == 清单 ②5 份 SKILL.md frontmatter 的
# name:/toolset: == 清单 ③字面量位置棘轮基线(只许减不许增)三条规则守。Rust 侧
# (gateway/bridge)本批未覆盖, 脚本输出里登记为 GAP(脚本第 (a) 条)。
# ⚠ 必须放在 materialize-resources.sh 之后 —— 规则 ③ 要读 4 份物化 SKILL.md(生成物)。
_css_rc=0
python3 tests/contract/check-contract-single-source.py || _css_rc=$?
if [ "$_css_rc" -ne 0 ]; then
  echo "[L0] FAIL: contract single-source check rc=$_css_rc (1=drift/violation, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] contract single-source: consts == manifest; frontmatter 5/5; literal ratchet clean"
# 零桥符号棘轮(2026-09-28 SDK 去桥化, owner 裁决): 环境(桥/路由)由 CLI 自持 ——
# SDK 只做 best-effort 的"入站 live/down"通知(`aimail address -a <addr> --inbound-live`)。
# 静态负断言四条(命中 = 0): ①pysdk/**/*.py 的 AST 标识符 ②tssdk/packages/*/src + test
# (排除棘轮自身的符号表 startup-hook.test.ts)剥注释后的代码 ③契约真源不得含桥键
# ④**引用侧**(2026-09-28 补): pysdk/ tssdk/ cli/ tests/ 的 .py/.ts/.sh/.md **∪ 无扩展名
# 可执行/shebang 脚本**(2026-09-28 二次补: 扩展名白名单曾把无后缀主入口 cli/aimail 整个
# 跳过 —— 它的死引用 `from aimail_base import ensure_bridge_routes_for_system` 又躲过一轮,
# 门禁 L1 101 PASS/12 FAIL 全败于该 ImportError)里, 退役
# 符号不得被 import/别名/成员访问/定义/参数/动态调用名引用 —— 根因: ①②只守 SDK 自己的
# 文件, "别处 import 一个已删除的符号"漏网(实测 cli/setup_system.py + cli/repair.py 三处
# 死引用 ⇒ 门禁 L1 45 红全败于同一 ImportError), 而 pyflakes **不报**"从模块 import
# 不存在的名字"。注释/文档字符串不算引用; 显式 `retired:` 说明行单独成节打印。
# 新增一个桥符号/回退一处路由调用/别处引用退役符号 ⇒ 本步红(fail-closed, rc=2 判不了也红)。
_zb_rc=0
python3 tests/contract/check-zero-bridge.py . || _zb_rc=$?
if [ "$_zb_rc" -ne 0 ]; then
  echo "[L0] FAIL: zero-bridge ratchet rc=$_zb_rc (1=桥符号命中, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] zero-bridge: SDK/引用侧零桥符号"
# 文件归属 gate(owner 分层裁决 A, 2026-09-28; 接法同 [zero-bridge]):
#   系统级环境文件(aimail_gateway.json)只由 **CLI** 写; per-agent 绑定文件
#   (契约键 binding_file, 不写字面量以免撞字面量棘轮)只由 **SDK** 写; 反向只许读。
#   CLI 改绑定内容必须经 pysdk 的语义化薄函数(update_binding / backfill_binding /
#   rename_binding → 内部走 save_agent_config, 原子 tmp+rename+0600 不变),
#   不再自持写调用。
# 判定证据四条(a)(b)(c)(d)见 tests/contract/check-file-ownership.py 模块 docstring;
# 基线 tests/contract/file-ownership-baseline.json 当前**为空** ⇒ 任何一处新越界写
# 立刻红(rc=1); 行内逃生门 file-ownership-allowed:<理由> 的行由脚本**单独成节打印**
# (可见不静默); 判不了(基线缺失/路径读不到)rc=2 ⇒ fail-closed 也红。
_fo_rc=0
python3 tests/contract/check-file-ownership.py . || _fo_rc=$?
if [ "$_fo_rc" -ne 0 ]; then
  echo "[L0] FAIL: file-ownership rc=$_fo_rc (1=越界写, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] file-ownership: CLI ↛ 绑定文件 / SDK ↛ 系统 env (baseline 空)"
# 文档↔实现一致性(2026-09-27, 用户裁决「文档承诺的能力必须被实现兜住」):
# docs/agent-self-setup{,_zh}.md ①反引号里的 SDK 符号必须真有定义
# ②en/zh 标题结构逐条一致 ③文档里的契约值必须逐字命中单一真源清单。
# rc: 0=通过 1=违约(带 file:line) 2=判不了(文档/清单读不到) —— fail-closed。
_doc_rc=0
python3 tests/contract/check-docs-consistency.py || _doc_rc=$?
if [ "$_doc_rc" -ne 0 ]; then
  echo "[L0] FAIL: docs↔impl consistency rc=$_doc_rc (1=symbol/contract/structure violation, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] docs↔impl: symbols defined; en/zh heading structure equal; contract values == manifest"
# 平台边界 gate:CLI 代码不得出现平台字面分支(新增平台/多 agent 注册只改
# cli/platforms.json + SDK,CLI 零改动)。白名单 = 空(cmd_reset 特例已随
# register_all 表化删除)——出现任何平台字面即红。
_LIT=$(grep -nE '(platform|agent_type|kind|tgt) == "(hermes|openclaw|deerflow|dsh|pi)"' cli/aimail cli/check_status.py cli/repair.py 2>/dev/null || true)
if [ -n "$_LIT" ]; then
  echo "[L0] FAIL: platform literals leaked into CLI code (registry is the single platform source):"
  echo "$_LIT"; exit 1
fi
echo "[L0] platform-boundary: CLI clean of platform literals (registry-driven)"
# Core runtime modules: strict (no unused/undefined). Deploy-time patch
# scripts (hermes/patch_* etc.) intentionally import `aimail` for
# side-effect/eval use — syntax-check only those.
python3 -m pyflakes pysdk/aimail_base.py pysdk/aimail_board.py pysdk/aimail_tools.py \
  pysdk/aimail_mcp_server.py pysdk/hermes/aimail_hermes.py pysdk/deer-flow/*.py 2>/dev/null \
  || { echo "[L0] FAIL: pyflakes (core)"; exit 1; }
for f in $(find pysdk -name '*.py' -not -path '*__pycache__*'); do
  python3 -m py_compile "$f" || { echo "[L0] FAIL: py_compile $f"; exit 1; }
done
python3 -m pytest tests/ -q 2>&1 | tail -2

echo "═══ [L0] tssdk: build (pnpm build) + orphan guard + vitest ═══"
cd tssdk
# 单一入口: 根 package.json 的 build 脚本封装 5 包 tsc(避免三处清单各自维护 —— 审计 P2)
pnpm build
# 孤儿产物守卫(审计 2026-09-21): lib/dist 里的 *.js 必须有对应 src/*.ts。
# 反例: mail-core 的 install.ts 已删, 但陈旧 lib/install.js 仍被 package.json 的
# files glob 打进发布包(14.5KB 死代码 + 与 src 不符的 .d.ts)。
orphans=0
for pkg in packages/*/; do
  for d in lib dist; do
    [ -d "$pkg$d" ] || continue
    for f in "$pkg$d"/*.js; do
      [ -f "$f" ] || continue
      base=$(basename "$f" .js)
      find "$pkg/src" -name "$base.ts" -print -quit | grep -q . \
        || { echo "  ✗ orphan artifact (no src/$base.ts): $f"; orphans=$((orphans+1)); }
    done
  done
done
[ "$orphans" -eq 0 ] || { echo "[L0] FAIL: $orphans orphan artifact(s) in lib/dist"; exit 1; }
echo "[L0] orphan-artifact guard: 所有 lib/dist 产物均有对应 src"
pnpm test 2>&1 | tail -2

echo "═══ [L0] PASS — all gates green ═══"
