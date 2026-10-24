#!/usr/bin/env bash
# install-skill.sh — install aimail skill to DeerFlow skills directory
# SKILL.md is generic email processing specification (shared same source with Hermes/OpenClaw), copied from package resources
# (source resolution pip aimail > repo root resources/skills, via `aimail install --payload resource skills`).
# Directory name = agent side contract name agentmail (not product name aimail): deer-flow enforces
# name == dirname(skills/export.py),SKILL.md frontmatter `name: agentmail`
# ⇒ directory must have same name, otherwise deer-flow cannot load that skill.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# P3 (2026-09-20, owner decision): changed to CLI public command surface (same description style as install-mcp.sh)
command -v aimail >/dev/null 2>&1 || { echo "ERROR: aimail command not found, please install CLI (bootstrap) first." >&2; exit 1; }
DEER_FLOW_HOME="${DEER_FLOW_HOME:-$HOME/deer-flow}"
# deer-flow's project_root(): $DEER_FLOW_PROJECT_ROOT or cwd (runtime_paths.py:7-17)
DF_PROJECT_ROOT="${DEER_FLOW_PROJECT_ROOT:-$PWD}"

# ── 0. Landing point resolution (same order as install-mcp.sh:49-64 + one explicit ops override)────────────
# deer-flow source of truth (third-party read-only reference):
#   config/skills_config.py:41-70 get_skills_path() =
#     ① config.skills.path → ② DEER_FLOW_SKILLS_PATH → ③ project_root()/skills
#       → ④ legacy candidates → fallback project_default
#   skills/storage/local_skill_storage.py:30-36 layout = <skills root>/public/<name>/SKILL.md
# Selection order for this script (same order as above):
#   ① DEER_FLOW_SKILLS_DIR (ops override in this repo, highest priority)
#   ② DEER_FLOW_SKILLS_PATH (deer-flow's own highest priority env, must write to it if set)
#   ③ ${DEER_FLOW_PROJECT_ROOT}/skills/public (ops has asserted project root ⇒ land in it)
#   ④ Original default ${DEER_FLOW_HOME}/skills/public (existing deployment compatibility)
# Final landing point and selection reason are always printed (for ops verification).
# 2026-09-30 measured root cause: when ③ is not passed, the landing point is ④ ($DEER_FLOW_HOME/skills/public),
#   while in-container sessions always read from project_root()/skills ⇒ skill files are on disk but sessions can never read them.
if [ -n "${DEER_FLOW_SKILLS_DIR:-}" ]; then
  PUBLIC_DIR="$DEER_FLOW_SKILLS_DIR"
  SKILLS_REASON="DEER_FLOW_SKILLS_DIR (explicit override, highest priority)"
elif [ -n "${DEER_FLOW_SKILLS_PATH:-}" ]; then
  PUBLIC_DIR="$DEER_FLOW_SKILLS_PATH/public"
  SKILLS_REASON="DEER_FLOW_SKILLS_PATH (deer-flow get_skills_path ② env; must write to it if set)"
elif [ -n "${DEER_FLOW_PROJECT_ROOT:-}" ]; then
  PUBLIC_DIR="$DF_PROJECT_ROOT/skills/public"
  SKILLS_REASON="DEER_FLOW_PROJECT_ROOT explicitly declared (deer-flow get_skills_path ③ project_root()/skills)"
else
  PUBLIC_DIR="$DEER_FLOW_HOME/skills/public"
  SKILLS_REASON="Original default DEER_FLOW_HOME/skills/public (existing deployment compatibility)"
fi
DST_DIR="$PUBLIC_DIR/agentmail"
echo "skills landing: $DST_DIR/SKILL.md"
echo "  selected by: $SKILLS_REASON"

# Host interpreter (2026-10-12, same as install-mcp.sh): CLI returns host interpreter via AIMAIL_PYTHON,
# Default is PATH python3 —— otherwise the SDK already installed in the host venv is judged as "not installed" by the mirror python3.
SKILLS_SRC="$("${AIMAIL_PYTHON:-python3}" -c 'import aimail, os, sys
d = os.path.dirname(aimail.__file__)
p = os.path.join(d, "resources", "skills")
sys.stdout.write(p if os.path.isdir(p) else "")')"
# owner 2026-10-06 (target state): skills **taken from installed package** (`site-packages/aimail/resources/skills`) ✓
# —— no longer copied via CLI payload ✗ (self-contained payload/repo state all cancelled ✓)
if [ -z "$SKILLS_SRC" ]; then
  echo "ERROR: resources/skills missing in installed package (reinstall agent-aimail or check package integrity)" >&2
  exit 1
fi
SRC_SKILL="$SKILLS_SRC/SKILL.md"
if [ ! -f "$SRC_SKILL" ]; then
  echo "SKILL source not found: $SRC_SKILL" >&2
  exit 1
fi
mkdir -p "$DST_DIR"
if [ -f "$DST_DIR/SKILL.md" ] && cmp -s "$SRC_SKILL" "$DST_DIR/SKILL.md"; then
  echo "  ✓ SKILL is in place and consistent (skip copy)"
else
  cp "$SRC_SKILL" "$DST_DIR/SKILL.md"
  echo "  ✓ SKILL copied → $DST_DIR/SKILL.md"
fi
cp "$SKILLS_SRC/DESCRIPTION.md" "$DST_DIR/DESCRIPTION.md" 2>/dev/null || true

echo "verify: ls $DST_DIR"
