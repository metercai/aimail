#!/usr/bin/env bash
# Release gate L0 — full test sweep before ANY publish (local + CI).
# Run from repo root (or anywhere; script locates the root itself).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

echo "═══ [L0] python lint + unit tests ═══"
# SDK resource single true source (2026-09-25): true source=repo root resources/ (git tracked, only editable place).
# The copy bundled with each adapter package is a generated artifact **staged by each package's own prepack** (already gitignored) —— npm
# tarball can only pack files within the package directory, so staging is part of each package's packaging process, no repo-level unified script.
# pysdk side does not need a copy: in repo checkout, SDK reads repo root resources/ directly (_resources_release.resources_root).
# The gate here stages the copy for each of the 3 packages, so that contract rule ③ can read it (in a clean clone, the generated artifact does not exist).
bash tests/cli/stage-resources.sh
# Contract single true source gate (2026-09-27): agent internal contract surface (skill/toolset registration names, binding file names,
# Single source of truth for (pointer filename, inbound path) =
# contract/aimail-contract.json; guarded by ① two constant modules (pysdk/aimail_contract.py +
# tssdk/.../mail-core/src/contract.ts) item-by-item == checklist ② 5 SKILL.md frontmatter
# name:/toolset: == checklist ③ literal location ratchet baseline (only decrease allowed, no increase) three rules. Rust side
# (gateway/bridge) not covered in this batch, registered as GAP in script output (script item (a)).
# ⚠ Must be placed after each package resource staging —— Rule ③ needs to read 3 in-package SKILL.md (generated artifacts).
_css_rc=0
# The contract gate judges the in-package SKILL.md frontmatter, which is a *generated*
# artifact (tssdk prepack stages repo-root resources/ into the package; gitignored).
# A fresh checkout therefore has no such file — stage it first, exactly as prepack does,
# otherwise the gate can only answer CANNOT JUDGE (rc=2) and L0 fails closed on a clean tree.
_st_rc=0
node tssdk/packages/agent-aimail/scripts/stage-resources.mjs \
  --dest tssdk/packages/agent-aimail/resources >/dev/null 2>&1 || _st_rc=$?
if [ "$_st_rc" -ne 0 ]; then
  echo "[L0] FAIL: stage-resources rc=$_st_rc (in-package SKILL.md cannot be produced; contract gate would be blind)"
  exit 1
fi
python3 tests/contract/check-contract-single-source.py || _css_rc=$?
if [ "$_css_rc" -ne 0 ]; then
  echo "[L0] FAIL: contract single-source check rc=$_css_rc (1=drift/violation, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] contract single-source: consts == manifest; frontmatter 5/5; literal ratchet clean"
# Zero-bridge symbol ratchet (2026-09-28 SDK de-bridging, owner ruling): Environment (bridge/routing) is self-held by CLI ——
# SDK only does best-effort "inbound live/down" notification (`aimail address -a <addr> --inbound-live`).
# Static negative assertions four (hit = 0): ① AST identifiers in pysdk/**/*.py ② tssdk/packages/*/src + test
# (excluding ratchet's own symbol table startup-hook.test.ts) code after stripping comments ③ Contract source of truth must not contain bridge keys
# ④**Citation side**(2026-09-28 supplement): .py/.ts/.sh/.md in pysdk/ tssdk/ cli/ tests/ **∪ no extension
# executable/shebang scripts** (2026-09-28 second supplement: extension whitelist previously skipped the entire no-suffix main entry cli/aimail
# —— its dead reference `from aimail_base import ensure_bridge_routes_for_system` slipped through another round,
# Gate L1 101 PASS/12 FAIL all failed due to that ImportError) where, retired
# symbols must not be referenced by import/alias/member access/definition/parameter/dynamic call name —— Root cause: ①② only guard SDK's own
# files, "importing a deleted symbol elsewhere" slipped through (measured cli/setup_system.py + cli/repair.py three places
# dead references ⇒ Gate L1 45 red all failed due to same ImportError), and pyflakes **does not report** "importing
# non-existent name from module". Comments/docstrings do not count as references; explicit `retired:` explanation lines are printed in a separate section.
# Adding a bridge symbol/rolling back a routing call/referencing retired symbol elsewhere ⇒ this step red (fail-closed, rc=2 cannot-judge also red).
_zb_rc=0
python3 tests/contract/check-zero-bridge.py . || _zb_rc=$?
if [ "$_zb_rc" -ne 0 ]; then
  echo "[L0] FAIL: zero-bridge ratchet rc=$_zb_rc (1=bridge symbol hit, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] zero-bridge: SDK/reference side zero bridge symbols"
# File ownership gate (owner layered ruling A, 2026-09-28; integration same as [zero-bridge]):
#   System-level environment file (aimail_gateway.json) only written by **CLI**; per-agent binding file
#   (contract key binding_file, no literal written to avoid hitting literal ratchet) only written by **SDK**; reverse only read allowed.
#   CLI modifying binding content must go through pysdk's semantic thin functions (update_binding / backfill_binding /
#   rename_binding → internally via save_agent_config, atomic tmp+rename+0600 invariant),
#   no longer self-holding write calls.
# Four judgment evidence items (a)(b)(c)(d) see tests/contract/check-file-ownership.py module docstring;
# Baseline tests/contract/file-ownership-baseline.json currently **empty** ⇒ any new out-of-bounds write
# immediately red (rc=1); inline escape hatch file-ownership-allowed:<reason> lines are **printed in a separate section** by script
# (visible not silent); cannot-judge (baseline missing/path unreadable) rc=2 ⇒ fail-closed also red.
_fo_rc=0
python3 tests/contract/check-file-ownership.py . || _fo_rc=$?
if [ "$_fo_rc" -ne 0 ]; then
  echo "[L0] FAIL: file-ownership rc=$_fo_rc (1=out-of-bounds write, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] file-ownership: CLI ↛ binding file / SDK ↛ system env (baseline empty)"
# Documentation↔Implementation consistency (2026-09-27, user ruling "capabilities promised in docs must be backed by implementation"):
# docs/agent-self-setup.md (EN-only since 2026-10-25; the _zh pair, when present, is checked too) ① SDK symbols in backticks must
# actually have definitions ② en/zh title structure item-by-item consistent (skipped when the zh pair is absent) ③ contract values
# in docs must hit the single source of truth checklist verbatim.
# rc: 0=pass 1=violation (with file:line) 2=cannot-judge (docs/checklist unreadable) —— fail-closed.
_doc_rc=0
python3 tests/contract/check-docs-consistency.py || _doc_rc=$?
if [ "$_doc_rc" -ne 0 ]; then
  echo "[L0] FAIL: docs↔impl consistency rc=$_doc_rc (1=symbol/contract/structure violation, 2=cannot-judge; fail-closed)"
  exit 1
fi
echo "[L0] docs↔impl: symbols defined; en/zh heading structure equal; contract values == manifest"
# Platform boundary gate: CLI code must not contain platform literal branches (adding platform/multi-agent registration only changes
# platform-boundary (CLI contains no platform literals) migrated to CLI domain gate: tests/cli/run-cli-gates.sh
# Core runtime modules: strict (no unused/undefined). Deploy-time patch
# scripts (hermes/patch_* etc.) intentionally import `aimail` for
# side-effect/eval use — syntax-check only those.
python3 -m pyflakes pysdk/aimail_base.py pysdk/aimail_board.py pysdk/aimail_tools.py \
  pysdk/aimail_mcp_server.py pysdk/aimail_agent.py pysdk/hermes/aimail_hermes.py \
  pysdk/deer-flow/*.py 2>/dev/null \
  || { echo "[L0] FAIL: pyflakes (core)"; exit 1; }
for f in $(find pysdk -name '*.py' -not -path '*__pycache__*'); do
  python3 -m py_compile "$f" || { echo "[L0] FAIL: py_compile $f"; exit 1; }
done
python3 -m pytest tests/sdk/ -q 2>&1 | tail -2

echo "═══ [L0] tssdk: build (pnpm build) + orphan guard + vitest ═══"
cd tssdk
# Single entry: root package.json build script encapsulates 5 packages tsc (avoiding three checklists maintained separately —— audit P2)
pnpm build
# Orphan artifact guard (audit 2026-09-21): *.js in lib/dist must have a corresponding src/*.ts.
# Counterexample: mail-core's install.ts has been deleted, but the stale lib/install.js is still referenced by package.json's
# files glob packed into the release bundle (14.5KB dead code + .d.ts inconsistent with src).
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
echo "[L0] orphan-artifact guard: all lib/dist artifacts have corresponding src"
pnpm test 2>&1 | tail -2

echo "═══ [L0] PASS — all gates green ═══"
