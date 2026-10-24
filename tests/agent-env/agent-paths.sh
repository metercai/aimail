#!/usr/bin/env bash
# agent runtime path **single source of truth** (owner 2026-10-11 ruling).
#
# Background (why this file exists): the launcher candidate order was originally written in verify.sh (two places) and tests/l3/run-l3.sh
# (one assignment) separately; on 2026-10-11 when wiring L3 to the single-source agent-env, the assignment in run-l3.sh along with
# the `[ -x ]` guard was removed, leaving only consumers ⇒ CI reported `HERMES_BIN: unbound variable`, L3 9/9 red
# (set -u interrupted directly, hermes host never started). After converging to this file, there is no second copy that can drift.
#
# Usage (only source in bash scripts; this file has no side effects):
#   . "$(dirname "${BASH_SOURCE[0]}")/agent-paths.sh"     # same directory
#   bin="$(agent_launcher hermes "$HOME_DIR" || true)"
#
# agent_launcher <agent> [home_dir] —— on success, prints the absolute path of the executable launcher and returns 0;
# returns non-zero if not found (caller decides die / red / gap).

agent_launcher() {
  local agent="$1" home_dir="${2:-}" b="" t="" tree=""

  case "$agent" in
    hermes)
      # Official host install: source tree is at <home>/hermes-agent, launcher is at <tree>/.hermes/bin/hermes,
      # and a copy is placed in PATH (~/.local/bin/hermes). In docker images it is /opt/hermes/bin/hermes.
      # HERMES_HOME env (host real home): when verify.sh's home_dir is scratch,
      # the real install tree is only at $HERMES_HOME/hermes-agent.
      for t in "${home_dir:-/nonexistent}/hermes-agent" "${HERMES_HOME:-/nonexistent}/hermes-agent" "/opt/hermes"; do
        [ -f "$t/gateway/platforms/webhook.py" ] && { tree="$t"; break; }
      done
      # Windows (official install.ps1): launcher is published to <HermesHome>/bin/hermes.{exe,cmd}
      # (git-bash's [ -x ] works for .exe); unix layout unchanged.
      for b in "${tree:-/nonexistent}/.hermes/bin/hermes" "/opt/hermes/bin/hermes" \
               "${home_dir:-/nonexistent}/.local/bin/hermes" "$HOME/.local/bin/hermes" \
               "${HERMES_HOME:-/nonexistent}/bin/hermes.exe" "${HERMES_HOME:-/nonexistent}/bin/hermes.cmd" \
               "${home_dir:-/nonexistent}/bin/hermes.exe" "${home_dir:-/nonexistent}/bin/hermes.cmd" \
               "${tree:-/nonexistent}/.hermes/bin/hermes.exe" \
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
