#!/usr/bin/env bash
# verify-wheel.sh — build the aimail wheel and smoke-test it in a clean venv
# (three layouts: repo pysdk/ is exercised implicitly by the build; pip
# site-packages is the target here; bundle layout is covered by
# `cli/runtime_bundle.py check` at deploy time).
#
# Exit 0 = release-ready. Run before every publish:
#   bash tests/sdk/release/l2-verify-wheel.sh
# Overrides: AIMAIL_REPO (default: repo root, auto-detected), OUT_DIR
set -euo pipefail

REPO="${AIMAIL_REPO:-$(cd "$(dirname "$0")/../../.." && pwd)}"
OUT_DIR="${OUT_DIR:-$(mktemp -d /tmp/aimail-wheel.XXXXXX)}"
VENV_DIR="$(mktemp -d /tmp/aimail-venv.XXXXXX)"
PY="${PYTHON:-python3}"

cd "$REPO"
# owner 2026-10-07 (R5): package with no content change and already released ⇒ this gate SKIP (**loudly marked**, no silent false green)
bash scripts/what-changed.sh | sed 's/^/[L2][changed] /'
_pv=$(python3 -c "import re;print(re.search(r'(?m)^version = \"(.*)\"', open('pyproject.toml').read()).group(1))")
if git tag -l "pysdk-v$_pv" | grep -q . && git diff --quiet "pysdk-v$_pv" -- pysdk pyproject.toml; then
  echo "== verify-wheel: SKIP (pysdk no content changes, released pysdk-v$_pv —— no duplicate regression done)"; exit 0
fi
echo "== 1/5 build wheel → $OUT_DIR"
rm -rf "$OUT_DIR"
"$PY" -m build --wheel --outdir "$OUT_DIR" . >/dev/null 2>&1 || {
  echo "build failed (need: python3 -m pip install build)"; exit 1; }
# PEP 427:wheel filename uses normalized name(agent-aimail → agent_aimail,hyphen→underscore)
WHL=$(ls "$OUT_DIR"/agent_aimail-*.whl | head -1)
echo "   wheel: $WHL"

echo "== 2/5 clean venv install"
"$PY" -m venv "$VENV_DIR"
"$VENV_DIR/bin/pip" install -q "$WHL"

echo "== 3/5 import surface (all pip-layout modules)"
"$VENV_DIR/bin/python" - "$WHL" <<'PY'
import importlib, os, sys
import aimail

modules = [
    "aimail.aimail_base", "aimail.aimail_tools", "aimail.aimail_board",
    "aimail.gateway_api", "aimail.aimail_mcp_server", "aimail.aimail_agent",
    "aimail.aimail_contract",
    "aimail._resources_release", "aimail._aimail_bootstrap",
    "aimail.v2.binding", "aimail.hermes_plugin", "aimail.deerflow_plugin",
]
for m in modules:
    importlib.import_module(m)

# 0.3.0 breaking surface: the patch-era modules are NOT shipped any more. This used to be part of the
# list above, which made every 0.3.x release die at "import surface" (measured 2026-11-24, pypi-build
# run 45353491562: ModuleNotFoundError: aimail.hermes.patch_webhook). Asserting ABSENCE keeps the gate
# honest in the other direction too — a retired module coming back fails here.
removed_in_030 = [
    "aimail.hermes.patch_webhook", "aimail.hermes.patch_profiles",
    "aimail.hermes.ensure_config", "aimail.hermes.register_profiles",
    "aimail.hermes.toolsets",
    # v1 hermes adapter itself — retired 2026-11-25 (v2 = hermes_agent.plugins entry point).
    "aimail.hermes.aimail_hermes",
    # v1 push/CLI surface — retired 2026-11-25 ("keep only SSE-V2" ruling).
    "aimail.install", "aimail.sdk_ops", "aimail.hosts.registry",
    "aimail.deer-flow.aimail_inbound", "aimail.deer-flow.aimail_deerflow",
    "aimail.deer-flow.manage",
]
for m in removed_in_030:
    try:
        importlib.import_module(m)
    except ModuleNotFoundError:
        continue
    raise SystemExit(f"::error::{m} is back in the wheel — 0.3.0 removed the patch surface on purpose")
# path helpers resolve to real files inside the wheel
for d in (aimail.skills_dir(), aimail.board_role_prompt_dir()):
    assert os.path.isdir(d), f"missing dir: {d}"
assert os.path.isfile(aimail.mcp_server_path())
# The v1 deer-flow install scripts are gone with the adapter; the v2 extension needs no scripts.
import importlib.resources as _ir
assert not (_ir.files("aimail") / "deer-flow").is_dir(), "v1 deer-flow dir is back in the wheel"
print(f"   OK: {len(modules)} v2 modules + resource paths (aimail {aimail.__version__})")
PY

echo "== 4/5 retired entry points are absent"
"$VENV_DIR/bin/python" -c "import importlib; [importlib.import_module(m) for m in ('aimail.install','aimail.sdk_ops')]" \
    2>/dev/null && { echo "   retired CLI entries are still importable"; exit 1; } || true
echo "   OK: aimail.install / aimail.sdk_ops absent"

echo "== 5/5 version metadata"
EXPECT_VER="$("$PY" -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
"$VENV_DIR/bin/python" - "$EXPECT_VER" <<'PY'
import sys
from importlib.metadata import version
assert version("agent-aimail") == sys.argv[1], f"{version('agent-aimail')} != {sys.argv[1]}"
print(f"   OK: installed version {version('agent-aimail')} matches pyproject")
PY

rm -rf "$VENV_DIR"
echo "== verify-wheel: PASS (wheel at $WHL)"
echo "   publish with: twine upload $WHL"
