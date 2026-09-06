#!/usr/bin/env bash
# verify-wheel.sh — build the aimail wheel and smoke-test it in a clean venv
# (three layouts: repo pysdk/ is exercised implicitly by the build; pip
# site-packages is the target here; bundle layout is covered by
# `cli/runtime_bundle.py check` at deploy time).
#
# Exit 0 = release-ready. Run before every publish:
#   bash tests/release-gates/verify-wheel.sh
# Overrides: AIMAIL_REPO (default: repo root, auto-detected), OUT_DIR
set -euo pipefail

REPO="${AIMAIL_REPO:-$(cd "$(dirname "$0")/../.." && pwd)}"
OUT_DIR="${OUT_DIR:-$(mktemp -d /tmp/aimail-wheel.XXXXXX)}"
VENV_DIR="$(mktemp -d /tmp/aimail-venv.XXXXXX)"
PY="${PYTHON:-python3}"

cd "$REPO"
echo "== 1/5 build wheel → $OUT_DIR"
rm -rf "$OUT_DIR"
"$PY" -m build --wheel --outdir "$OUT_DIR" . >/dev/null 2>&1 || {
  echo "build failed (need: python3 -m pip install build)"; exit 1; }
WHL=$(ls "$OUT_DIR"/aimailsdk-*.whl | head -1)
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
    "aimail.gateway_api", "aimail.amail_mcp_server", "aimail.install",
    "aimail._resources_release", "aimail._aimail_bootstrap",
    "aimail.hermes.aimail_hermes", "aimail.hermes.patch_webhook",
    "aimail.hermes.patch_profiles", "aimail.hermes.ensure_config",
    "aimail.hermes.register_profiles", "aimail.hermes.toolsets",
    "aimail.deer-flow.manage", "aimail.deer-flow.amail_base",
    "aimail.openclaw.amail_base",
]
for m in modules:
    importlib.import_module(m)
# path helpers resolve to real files inside the wheel
for d in (aimail.skills_dir(), aimail.board_role_prompt_dir()):
    assert os.path.isdir(d), f"missing dir: {d}"
assert os.path.isfile(aimail.mcp_server_path())
print(f"   OK: {len(modules)} modules + resource paths (aimail {aimail.__version__})")
PY

echo "== 4/5 install entry contract"
"$VENV_DIR/bin/python" -m aimail.install --help >/dev/null
"$VENV_DIR/bin/python" -m aimail.install check-env --type hermes --home /nonexistent \
    >/dev/null 2>&1 && { echo "   check-env should fail on missing host"; exit 1; } || true
echo "   OK: aimail.install entry works"

echo "== 5/5 version metadata"
EXPECT_VER="$("$PY" -c "import tomllib; print(tomllib.load(open('pyproject.toml','rb'))['project']['version'])")"
"$VENV_DIR/bin/python" - "$EXPECT_VER" <<'PY'
import sys
from importlib.metadata import version
assert version("aimailsdk") == sys.argv[1], f"{version('aimailsdk')} != {sys.argv[1]}"
print(f"   OK: installed version {version('aimailsdk')} matches pyproject")
PY

rm -rf "$VENV_DIR"
echo "== verify-wheel: PASS (wheel at $WHL)"
echo "   publish with: twine upload $WHL"
