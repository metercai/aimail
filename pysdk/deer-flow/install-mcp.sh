#!/usr/bin/env bash
# install-mcp.sh -- write the aimail MCP server block to DeerFlow extensions_config.json
# Reuse the shared aimail_mcp_server.py (fallback MCP service, platform-agnostic, zero adaptation), pointing to
# the self-contained payload <program_root>/mcp/ (installed via runtime_bundle.py, source pip>repo, version stamp;
# decoupled from repo paths, renaming/mv does not affect execution; the destination is provided by runtime_bundle in a single point).
# idempotent: update path/env if the aimail server block exists, otherwise append.
#
# Destination (fixed 2026-09-27): must follow the same order as deer-flow's own
# `ExtensionsConfig.resolve_config_path()`, otherwise the written file will never be read by deer-flow
# ⇒ no tools will be loaded at all (and install_steps is on_error=warn ⇒ silent).
# deer-flow actual resolution (third-party read-only reference):
#   backend/packages/harness/deerflow/config/extensions_config.py:419+
#     ① explicit config_path → ② DEER_FLOW_EXTENSIONS_CONFIG_PATH →
#     ③ extensions_config.json / mcp_config.json **already existing** under project_root()
#        (`existing_project_file`, extensions_config.json first) → ④ backend/ and repo root fallback
#   backend/packages/harness/deerflow/config/runtime_paths.py:7-17
#     project_root() = $DEER_FLOW_PROJECT_ROOT or cwd
#     runtime_home() = $DEER_FLOW_HOME or project_root()/.deer-flow
#     => DEER_FLOW_HOME on the deer-flow side is a **state directory**, never an extensions-config directory
#       (this repo uses it as the "deer-flow checkout root", existing deployments persist based on this ⇒ kept as the last fallback).
#
# Selection order for this script (same order as above + one explicit ops override):
#   ① DEER_FLOW_EXT_CFG (this repo's ops override, highest priority)
#   ② DEER_FLOW_EXTENSIONS_CONFIG_PATH (deer-flow's own highest priority env, if set it must be written to)
#   ③ ${DEER_FLOW_PROJECT_ROOT:-$PWD}/extensions_config.json
#        file already exists ⇒ use it (deer-flow will prioritize reading it, writing elsewhere is equivalent to not writing)
#        DEER_FLOW_PROJECT_ROOT explicitly declared ⇒ create new in it (ops has asserted the project root)
#        (do not unconditionally use $PWD: that is the cwd when this script runs, which is not necessarily the same as the cwd of the deer-flow process
#         -- unconditionally creating new would result in it not being read and losing existing defaults)
#   ④ original default ${DEER_FLOW_HOME}/extensions_config.json (compatibility with existing deployments)
# The final destination and selection reason are always printed (for ops verification), and a hint is given for cases where it might be preempted by legacy mcp_config.json.
# env must be **self-sufficient** (2026-10-02 J4e root cause forensics):
#   deer-flow starts stdio server with only the env from this block (third-party read-only reference
#   backend/packages/harness/deerflow/mcp/client.py:32-34 `params["env"] = config.env`),
#   child process cannot get the installation-side environment ⇒ when AIMAIL_HOME is missing, aimail_base.aimail_home()
#   (pysdk/aimail_base.py:242-243) falls back to ~/.aimail ⇒ cannot scan local bindings ⇒ every tool call
#   throws `agent '<id>' not registered` (aimail_base.py:573-575) ⇒ agent can never send mail
#   (deerflow J4e history 13 red 0 green; counterfactual test: adding AIMAIL_HOME resolves it).
#   AIMAIL_SYSTEM_ID similarly: pin the system at install time -- only when multiple systems share the same agent_id will it not be
#   picked by _scan_systems_for_agent's "first match by directory order" to a different address (missing ⇒ server falls back to full scan, same as old behavior).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
# P3(2026-09-20, owner decided): no longer assemble repo-relative paths to call cli/runtime_bundle.py ——
# always use the CLI public command surface (aimail install --payload …); after rustification, the same command is implemented by the new binary, this script no longer needs changes.
command -v aimail >/dev/null 2>&1 || {
    echo "ERROR: aimail command not found. Please install the CLI (bootstrap) before running this script." >&2
    echo "       (no longer falling back to cli/runtime_bundle.py in the repo —— that is exactly the coupling to be eliminated)" >&2
    exit 1
}
DEER_FLOW_HOME="${DEER_FLOW_HOME:-$HOME/deer-flow}"
# deer-flow's project_root(): $DEER_FLOW_PROJECT_ROOT or cwd (runtime_paths.py)
DF_PROJECT_ROOT="${DEER_FLOW_PROJECT_ROOT:-$PWD}"
LEGACY_CFG="$DEER_FLOW_HOME/extensions_config.json"
AGENT_ID="${AIMAIL_AGENT_ID:-default}"
# = pysdk/aimail_base.aimail_home() semantics (env takes precedence, otherwise ~/.aimail) —— known on the install side
# home must be written along with env, otherwise the MCP subprocess will guess a different root based on its own HOME (see file header).
AIMAIL_HOME_RESOLVED="${AIMAIL_HOME:-$HOME/.aimail}"
# The assembly step (pysdk/install.py::_assembly_env) will setdefault; here we tolerate empty (empty ⇒ do not write the key,
# server side falls back to scanning all systems = old behavior).
AIMAIL_SYSTEM_ID_RESOLVED="${AIMAIL_SYSTEM_ID:-}"
# Host interpreter (2026-10-12): **the target system's own host interpreter** —— CLI command entry via AIMAIL_PYTHON
# (contract §4.1(2) single source of truth), default is PATH python3; explicit PY override takes precedence (ops/gatekeeping).
# Not using it = using the image system's python3 to `import aimail`: installing it is the same as not installing it (deerflow measured
# `ModuleNotFoundError: No module named 'aimail'` ⇒ install-skill.sh exit 1)。
PY="${PY:-${AIMAIL_PYTHON:-python3}}"

# ── 0. Destination resolution (see file header; print final destination + reason)─────────────────────────
if [ -n "${DEER_FLOW_EXT_CFG:-}" ]; then
  CFG="$DEER_FLOW_EXT_CFG"
  CFG_REASON="DEER_FLOW_EXT_CFG (explicit override, highest priority)"
elif [ -n "${DEER_FLOW_EXTENSIONS_CONFIG_PATH:-}" ]; then
  CFG="$DEER_FLOW_EXTENSIONS_CONFIG_PATH"
  CFG_REASON="DEER_FLOW_EXTENSIONS_CONFIG_PATH (deer-flow's own highest priority env; if set, it must be written)"
elif [ -f "$DF_PROJECT_ROOT/extensions_config.json" ]; then
  CFG="$DF_PROJECT_ROOT/extensions_config.json"
  CFG_REASON="project root already has extensions_config.json (deer-flow resolve_config_path will read it first)"
elif [ -n "${DEER_FLOW_PROJECT_ROOT:-}" ]; then
  CFG="$DF_PROJECT_ROOT/extensions_config.json"
  CFG_REASON="DEER_FLOW_PROJECT_ROOT explicitly declared (create new inside it; deer-flow only searches project root)"
else
  CFG="$LEGACY_CFG"
  CFG_REASON="original default DEER_FLOW_HOME/extensions_config.json (compatibility with existing deployments)"
fi
CFG="${CFG/#\~/$HOME}"          # Fallback expansion of ~ in variable (env parameters do not go through shell expansion)
echo "extensions config: $CFG"
echo "  selected by: $CFG_REASON"
echo "  deer-flow project root: $DF_PROJECT_ROOT (DEER_FLOW_PROJECT_ROOT or cwd)"
echo "  deer-flow state home  : $DEER_FLOW_HOME (DEER_FLOW_HOME; deer-flow side = state directory)"
# Ops verification: when the destination is not under deer-flow's project root, but a legacy mcp_config.json lies there,
# deer-flow's search will hit that file → this write will not take effect (must explicitly point the way, no silent behavior).
if [ "$CFG" != "$DF_PROJECT_ROOT/extensions_config.json" ] \
   && [ -f "$DF_PROJECT_ROOT/mcp_config.json" ]; then
  echo "WARNING: $DF_PROJECT_ROOT/mcp_config.json exists —— deer-flow will read it instead of this destination" >&2
  echo "         Set DEER_FLOW_EXTENSIONS_CONFIG_PATH=$CFG to make deer-flow and this write point to the same file" >&2
fi

# ── 1. MCP service taken from installed package (owner 2026-10-06 target state: **zero-copy** ✗ self-contained payload/repo state all cancelled ✓)──
SERVER="$("$PY" -c 'import aimail, os, sys
d = os.path.dirname(aimail.__file__)
p = os.path.join(d, "aimail_mcp_server.py")
sys.stdout.write(p if os.path.isfile(p) else "")')"
# owner 2026-10-06 (target state): MCP service **taken from installed package** (`site-packages/aimail/aimail_mcp_server.py`) ✓ ——
# No longer use SDK file copying from `<program_root>/mcp/` ✗ (self-contained payload/repo state all cancelled ✓).
if [ -z "$SERVER" ]; then
  echo "ERROR: aimail_mcp_server.py missing in installed package (reinstall agent-aimail or check package integrity)" >&2
  exit 1
fi
[ -f "$SERVER" ] || { echo "MCP payload missing: $SERVER" >&2; exit 1; }

# Real version detection (only reports detection results, no guessing): version in backend/pyproject.toml
# Candidate order: deer-flow checkout root (=DEER_FLOW_HOME, existing convention in this repo) -> project root (only when
# it looks like a deer-flow checkout, to avoid misreporting identity on another repo's pyproject.toml).
DF_VERSION="unknown"
_VER_CANDIDATES=("$DEER_FLOW_HOME/backend/pyproject.toml" "$DEER_FLOW_HOME/pyproject.toml")
if [ -d "$DF_PROJECT_ROOT/backend" ]; then
  _VER_CANDIDATES+=("$DF_PROJECT_ROOT/backend/pyproject.toml" "$DF_PROJECT_ROOT/pyproject.toml")
fi
for pp in "${_VER_CANDIDATES[@]}"; do
  if [ -f "$pp" ]; then
    DF_VERSION="$(grep -m1 '^version' "$pp" | sed -E 's/.*=\s*"([^"]+)".*/\1/' || true)"
    if [ -n "$DF_VERSION" ]; then
      break
    fi
    DF_VERSION="unknown"
  fi
done
IDENTITY="deerflow/${DF_VERSION:-unknown}"

# Ensure config file exists (if missing, use example as template; example first searched in destination directory, then falls back to DEER_FLOW_HOME)
if [ ! -f "$CFG" ]; then
  mkdir -p "$(dirname "$CFG")"
  TPL=""
  for t in "$(dirname "$CFG")/extensions_config.example.json" \
           "$DEER_FLOW_HOME/extensions_config.example.json"; do
    if [ -f "$t" ]; then
      TPL="$t"
      break
    fi
  done
  if [ -n "$TPL" ]; then
    cp "$TPL" "$CFG"
    echo "created $CFG from $TPL"
  else
    echo '{"mcpServers": {}}' > "$CFG"
    echo "created empty $CFG"
  fi
fi

"$PY" - "$CFG" "$SERVER" "$AGENT_ID" "$IDENTITY" \
    "$AIMAIL_HOME_RESOLVED" "$AIMAIL_SYSTEM_ID_RESOLVED" "$PY" <<'PY'
import json, sys

(cfg_path, server, agent_id, identity,
 aimail_home, system_id, interpreter) = sys.argv[1:8]
with open(cfg_path) as f:
    data = json.load(f)

servers = data.setdefault("mcpServers", {})
# AIMAIL_SYSTEM_ID = **first** assembly of this host's system = primary identity, set-if-absent:
# a later reinstall for a different system (e.g. the `install -c` shared-domain buyer system in journey J2) must not rewrite it —
# last-writer would pin the MCP subprocess to the shared system, skewing the welcome email reply identity to the shared address
# (2026-10-02 j4e4 measured: outbound lands in agent.j*@shared-e2e.local => 0 lines bound this round => J4e still red).
# When the pinned system has no binding, the server side **loudly falls back** to full scan (aimail_mcp_server._agent_ctx).
_prev_env = ((servers.get("aimail") or {}).get("env")) or {}
_sid_out = _prev_env.get("AIMAIL_SYSTEM_ID") or system_id
_sid_note = ("kept previous pin" if _prev_env.get("AIMAIL_SYSTEM_ID")
             else ("pinned at first assembly" if system_id else
                   "absent => server scans all systems"))
servers["aimail"] = {
    "enabled": True,
    "type": "stdio",
    # MCP stdio subprocess interpreter = host interpreter (otherwise deer-flow uses image python3 to start server =>
    # subprocess cannot import aimail, every tool call fails).
    "command": interpreter,
    "args": [server],
    # env names consistent with server reading (AIMAIL_*). deer-flow only passes this env to the stdio
    # subprocess (see file header), so all variables required for server location binding must be included here.
    "env": {"AIMAIL_AGENT_ID": agent_id,
            "AIMAIL_AGENT_IDENTITY": identity,
            "AIMAIL_HOME": aimail_home}
           | ({"AIMAIL_SYSTEM_ID": _sid_out} if _sid_out else {}),
    "tool_name_prefix": True,
    "session_init_timeout": 60,
    "tool_call_timeout": 60,
    "description": "AIMail email tools (shared fallback MCP server)",
}

with open(cfg_path, "w") as f:
    json.dump(data, f, indent=2, ensure_ascii=False)
    f.write("\n")
print(f"wrote mcpServers.aimail → {cfg_path}")
print(f"  server: {server}")
print(f"  AIMAIL_AGENT_ID: {agent_id}")
print(f"  AIMAIL_AGENT_IDENTITY: {identity}")
print(f"  AIMAIL_HOME: {aimail_home}")
print(f"  AIMAIL_SYSTEM_ID: {_sid_out or '(absent => server scans all systems)'} [{_sid_note}]")
PY

echo "landing point: $CFG"
echo "verify: $PY -c \"import json; d=json.load(open('$CFG')); print(d['mcpServers']['aimail']['args'])\""
