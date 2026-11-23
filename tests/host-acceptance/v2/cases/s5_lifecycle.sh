#!/usr/bin/env bash
# S5 — lifecycle: pause marks the binding paused; unbind removes it.
set -uo pipefail
CASE="S5 lifecycle"
out="$1"
state="$(cat "$out/journey_state" 2>/dev/null || echo '')"

if [ "${LIFECYCLE:-0}" != "1" ]; then
  gap "lifecycle stage not requested (LIFECYCLE=0)"
  return 0
fi
[ -n "$state" ] || { gap "journey state missing (see $out/rc)"; return 0; }

paused="$(sed -n 's/.*paused_seen=\([A-Za-z]*\).*/\1/p' <<<"$state")"
final="$(sed -n 's/.*final_bindings=\([0-9]*\).*/\1/p' <<<"$state")"

maxb="$(sed -n 's/.*max_bindings=\([0-9]*\).*/\1/p' <<<"$state")"

# Attribute before judging (2026-11-23, dsh cell): pause/unbind are MODEL actions. If the
# tool surface was present (`mail tools: N`, N>0) but the session produced no tool-call
# frame at all, the model simply never asked for pause/unbind — that is an LLM/fixture GAP,
# not a product defect. If the surface was empty (N=0) it IS a product defect (the plug-in
# did not expose the tools). Never let either case read as green.
_tools="$(sed -n 's/.*mail tools: \([0-9][0-9]*\).*/\1/p' "$out/host.log" 2>/dev/null | tail -1)"
_toolcalls="$(grep -acE '"type":"item","streamId":"[^"]*","value":\{"type":"event","event":\{"type":"tool' "$out/act.log" 2>/dev/null || true)"
_toolcalls="${_toolcalls:-0}"
if [ -n "${_tools:-}" ] && [ "${_tools:-0}" -eq 0 ]; then
  red "mail tool surface missing (mail tools: 0) — the plugin exposed no tools, pause/unbind cannot run"
  return 0
fi
if [ "${_toolcalls:-0}" -eq 0 ] && [ "$paused" != "True" ]; then
  gap "the session made no tool call (0 frames) although the surface was present (mail tools: ${_tools:-unknown}) — the model never asked for pause/unbind; fixture/LLM GAP, not a product defect"
  return 0
fi

[ "$paused" = "True" ] && pass "pause marked the binding (paused=True)" || red "pause did not mark the binding (paused=$paused)"
if [ "${final:-9}" -lt "${maxb:-0}" ]; then
  pass "unbind removed the binding ($maxb -> $final)"
else
  red "unbind did not remove a binding (final=$final, max=$maxb)"
fi
