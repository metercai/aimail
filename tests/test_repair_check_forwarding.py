"""repair must forward the resolved platform home to check, and degrade (never abort) when check yields nothing.

2026-09-29 repair 方案A (owner-approved spec; do not redesign):

  A1  `_run_check(sid, home="")` forwards a non-empty home as `--agent-home`.
      `cli/check_status.py` parses no `-H` at all (`grep -c -- '-H'` -> 0; it only
      knows `--agent-home` / `--system-id` / `--agent` / `--agent-type` / `--json` /
      `--verbose` / `--ping`), so the repair CLI's own `-H/--home` used to die inside
      the check subprocess and the operator only ever saw "check produced no output".
      With an empty home the command stays byte-identical. The call now also returns
      the subprocess output tail (stdout head/tail + stderr tail), because
      `capture_output=True` swallows stderr.

  A2  both `if not checks:` sites degrade and continue the unconditional ladder:
      explicit warning + subprocess tail echo, `fails=[]`, no return. The anchor
      phrase `check produced no output` is kept (journey-in-host.sh:1272 and
      cli-in-host.sh:412 grep it). The empty RE-CHECK must never read as
      "all green" -- `all([])` is True, so a green rc there would be zero-evidence
      (GAP != green): rc follows the ladder's own result instead.

  :928 the all-green early exit ("check is all green, nothing to repair" -> rc 0)
      is untouched, and a check WITH output must behave exactly as before.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "cli"))

import repair  # noqa: E402

SID = "unit-sid"
HOME = "/tmp/unit-platform-home"
ANCHOR = "check produced no output"

# ladder steps, in the order repair's `plan` runs them
LADDER = [
    "_ensure_bridge_running",
    "_refresh_routes",
    "_repair_webhook_pairing",
    "_repair_gateway_config",
    "_repair_pointer",
    "_repair_runtime_resources",
    "_repair_mcp_payload",
    "_repair_agentmail_json",
    "_repair_routes_entries",
    "_repair_pull_entry_key",
]

EMPTY_TAIL = ("stdout: <empty>  |  stderr tail: Traceback (most recent call last): "
              "RuntimeError: check exploded")


class _Proc:
    def __init__(self, stdout="", stderr=""):
        self.stdout = stdout
        self.stderr = stderr


def _patch_subprocess(monkeypatch, calls, replies):
    """Fake repair.subprocess.run: record every cmd, return replies in order (last one repeats)."""
    remaining = list(replies)

    def fake(cmd, **kw):
        calls.append(list(cmd))
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    monkeypatch.setattr(repair.subprocess, "run", fake)


def _patch_check(monkeypatch, replies):
    """Fake repair._run_check: record (sid, home) per call, return replies in order (last repeats)."""
    calls = []
    remaining = list(replies)

    def fake(sid, home=""):
        calls.append((sid, home))
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    monkeypatch.setattr(repair, "_run_check", fake)
    return calls


def _patch_ladder(monkeypatch, ran, failing=None):
    """Replace every ladder step with a recorder (failing=<name> raises there)."""
    failing = failing or set()

    def make(name):
        def step(*a, **k):
            ran.append(name)
            if name in failing:
                raise RuntimeError(f"{name} exploded")
            return True
        return step

    for name in LADDER:
        monkeypatch.setattr(repair, name, make(name))


def _checks(passes):
    return [{"level": "L0", "check": f"c{i}", "pass": bool(p), "detail": "unit detail"}
            for i, p in enumerate(passes)]


# ── A1: --agent-home forwarding ────────────────────────────────────────────────

def test_run_check_appends_agent_home_only_when_home_is_given(monkeypatch):
    calls = []
    ok = _checks([True])
    _patch_subprocess(monkeypatch, calls, [_Proc(stdout=json.dumps({"checks": ok}))])

    passed, checks, tail = repair._run_check(SID)
    assert calls[0] == [sys.executable, str(repair.SCRIPTS_DIR / "check_status.py"),
                        "--json", "--system-id", SID], "empty home must keep the command byte-identical"
    assert "--agent-home" not in calls[0]
    assert "-H" not in calls[0], "check_status.py does not parse -H"
    assert passed is True and len(checks) == 1 and isinstance(tail, str)

    repair._run_check(SID, HOME)
    assert calls[1][-2:] == ["--agent-home", HOME]
    assert "-H" not in calls[1], "the home must be forwarded as --agent-home, never as -H"
    assert calls[1][:-2] == calls[0]


def test_run_check_returns_the_subprocess_tail_when_output_is_empty(monkeypatch):
    calls = []
    _patch_subprocess(monkeypatch, calls, [_Proc(stdout="", stderr="Traceback (most recent call last)")])

    passed, checks, tail = repair._run_check(SID, HOME)
    assert checks == []
    assert passed is False, "empty check output must never read as all-pass (GAP != green)"
    assert "Traceback (most recent call last)" in tail, "stderr is swallowed by capture_output -> echo it"
    assert "stdout: <empty>" in tail


def test_run_check_timeout_still_returns_three_values(monkeypatch):
    def boom(cmd, **kw):
        raise repair.subprocess.TimeoutExpired(cmd=cmd, timeout=120)
    monkeypatch.setattr(repair.subprocess, "run", boom)

    passed, checks, tail = repair._run_check(SID, HOME)
    assert passed is False and len(checks) == 1 and "timed out" in tail


# ── A2: degrade and continue ───────────────────────────────────────────────────

def test_empty_check_keeps_the_anchor_and_runs_the_ladder(monkeypatch, capsys):
    checks_reply = (False, [], EMPTY_TAIL)
    calls = _patch_check(monkeypatch, [checks_reply])
    ran = []
    _patch_ladder(monkeypatch, ran)

    rc = repair.repair(SID, home=HOME)
    out = capsys.readouterr().out

    assert ANCHOR in out, "the in-host gates grep this anchor (J4d / F8)"
    assert "unjudgeable" in out
    assert EMPTY_TAIL in out, "the subprocess tail must be echoed, not swallowed"
    assert "refresh bridge routes" in out, "the unconditional ladder must run"
    assert ran == LADDER, "every ladder step, in plan order"
    assert calls[0] == (SID, HOME) and calls[1] == (SID, HOME), "A1 at both call sites"
    assert "check is all green" not in out, "an empty check must not read as green"
    # re-check is empty too -> unjudgeable, rc = the ladder's own result (all steps True here)
    assert rc == 0
    assert "re-check is all green" not in out
    assert "UNJUDGEABLE" in out


def test_empty_recheck_with_a_failing_ladder_step_is_red(monkeypatch, capsys):
    _patch_check(monkeypatch, [(False, [], EMPTY_TAIL)])
    ran = []
    _patch_ladder(monkeypatch, ran, failing={"_refresh_routes"})

    rc = repair.repair(SID, home=HOME)
    out = capsys.readouterr().out

    assert rc == 1, "unjudgeable re-check + a failed ladder step must not be green"
    assert ANCHOR in out
    assert "re-check is all green" not in out
    assert "failed step(s)" in out


def test_dry_run_with_an_empty_check_returns_zero_and_lists_the_plan(monkeypatch, capsys):
    """cli-in-host.sh F8: `repair -n` used to return 1 on 'check produced no output'."""
    calls = _patch_check(monkeypatch, [(False, [], EMPTY_TAIL)])
    ran = []
    _patch_ladder(monkeypatch, ran)

    rc = repair.repair(SID, dry_run=True, home=HOME)
    out = capsys.readouterr().out

    assert rc == 0, "the F8 gap must become a real assertion: rc=0 + plan listed"
    assert "[dry-run] plan:" in out
    assert "refresh bridge routes" in out
    assert ANCHOR in out and EMPTY_TAIL in out
    assert ran == [], "dry-run must not execute the ladder"
    assert len(calls) == 1, "dry-run stops before the re-check"


# ── reverse: a check WITH output behaves exactly as before ─────────────────────

def test_all_green_early_exit_is_untouched(monkeypatch, capsys):
    green = (True, _checks([True, True]), "stdout: {...}")
    _patch_check(monkeypatch, [green])
    ran = []
    _patch_ladder(monkeypatch, ran)

    rc = repair.repair(SID, home=HOME)
    out = capsys.readouterr().out

    assert rc == 0
    assert "check is all green, nothing to repair" in out
    assert ran == [], "the :928 early exit must return before the ladder"
    assert ANCHOR not in out


def test_check_with_output_behaves_exactly_as_before(monkeypatch, capsys):
    first = (False, _checks([True, False]), "stdout: {...}")
    second = (True, _checks([True, True]), "stdout: {...}")
    calls = _patch_check(monkeypatch, [first, second])
    ran = []
    _patch_ladder(monkeypatch, ran)

    rc = repair.repair(SID, home=HOME)
    out = capsys.readouterr().out

    assert rc == 0
    assert "re-check is all green" in out, "a green re-check keeps its rc=0 verdict"
    assert ANCHOR not in out and "UNJUDGEABLE" not in out, "the degrade branch must stay out of it"
    assert "check ✗ L0/c1: unit detail" in out, "the failing check is still listed as before"
    assert ran == LADDER, "the ladder still runs in plan order"
    assert calls == [(SID, HOME), (SID, HOME)], "A1: home forwarded on the check and on the re-check"
    assert out.index("── bridge alive") < out.index("── refresh bridge routes") < out.index("-- re-check --")
