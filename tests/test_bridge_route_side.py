"""SDK side of the delivery-restore contract (owner ruling 2026-09-27 + 2026-09-28).

"registration succeeded" and "the environment can deliver to this host" are two
separate outcomes. Since the 2026-09-28 de-bridging ruling the ROUTE side is the
CLI's business — `cli/bridge_wire.py`, asserted in
`tests/test_inbound_live_notification.py`: `aimail address -a <addr> --inbound-live`
installs the route only while the host's inbound really serves, and
`--inbound-down` withdraws it.

The SDK's half of that contract is exactly ONE thing: it tells the environment
master (the `aimail` CLI) that its inbound for an address is live / down —
best-effort, never blocking, never fatal, payload = the address only. Everything
bridge-shaped (route POSTs, bridge admin ports, host:port probing) is gone from
`pysdk/` and `tssdk/`: that removal is pinned by the zero-bridge-symbol ratchet at
the bottom of this file (and, for the release path, by the `[contract]` step of
tests/sdk-release-gates/gate-tests.sh).

Assertions:
  1. the binary is resolved AIMAIL_BIN -> ~/.aimail/bin/aimail -> PATH, else '';
  2. no CLI => 'no_cli' (a skip with a reason, never a failure) and nothing spawned;
  3. live/down map to --inbound-live / --inbound-down; argv (never a shell);
     the payload carries the address and nothing else;
  4. a non-zero exit / a raising runner is REPORTED ('failed'), never raised;
  5. every binding of a system is notified exactly once;
  6. zero retired bridge symbols in pysdk/**.py and tssdk/packages/*/src/**.ts.

The TS hosts assert the mirrored contract in tssdk/test/startup-hook.test.ts
(wiring) and tssdk/test/inbound-notify.test.ts (behaviour).
"""
import ast
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
for _d in (ROOT / "pysdk",):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))

import aimail_base  # noqa: E402

#: Symbols retired by the 2026-09-28 de-bridging. The ratchet scans identifiers
#: (Python) / code (TypeScript), never comments or docstrings — a retirement note
#: in a docstring is documentation, not a reference.
RETIRED_PY = (
    "register_bridge_route", "bridge_admin_port", "bridge_listening",
    "ensure_bridge_route", "ensure_bridge_routes_for_system",
    "bridge_register_url_path", "store_bridge_register_url",
    "_align_registrations_to_bridge", "format_bridge_route_line",
    "route_outcome_is_warning", "is_deliverable_webhook_url",
    "is_bridge_host_port", "bridge_default_path",
)
RETIRED_TS = (
    "ensureBridgeRoutesForSystem", "ensureBridgeRoute", "registerBridgeRoute",
    "formatBridgeRouteLine", "isBridgeRouteWarning", "bridgeListening",
    "resolveBridgeAdminPort", "BridgeRouteOutcome", "BRIDGE_DEFAULT_PATH",
)


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


def _runner(rc=0, detail=""):
    """Test injection point: capture (bin, args, timeout) and answer (rc, detail)."""
    calls: list = []

    def run(binary, args, timeout):
        calls.append((binary, list(args), timeout))
        return rc, detail

    return calls, run


# ── 1. binary resolution ──────────────────────────────────────────────────────
def test_env_aimail_bin_wins(monkeypatch):
    monkeypatch.setenv("AIMAIL_BIN", "/custom/bin/aimail")
    assert aimail_base.resolve_aimail_bin() == "/custom/bin/aimail"


def test_missing_cli_is_the_empty_string(monkeypatch, tmp_path):
    monkeypatch.delenv("AIMAIL_BIN", raising=False)
    monkeypatch.setattr(aimail_base, "aimail_home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    assert aimail_base.resolve_aimail_bin() == ""


def test_canonical_home_binary_is_used_when_env_is_absent(monkeypatch, tmp_path):
    monkeypatch.delenv("AIMAIL_BIN", raising=False)
    monkeypatch.setattr(aimail_base, "aimail_home", lambda: tmp_path)
    monkeypatch.setattr("shutil.which", lambda _name: None)
    b = tmp_path / "bin" / "aimail"
    b.parent.mkdir(parents=True, exist_ok=True)
    b.write_text("#!/bin/sh\n")
    assert aimail_base.resolve_aimail_bin() == str(b)


# ── 2. no CLI is a skip with a reason, never a failure, never a spawn ─────────
def test_no_cli_is_a_skip_with_a_reason(monkeypatch):
    monkeypatch.setattr(aimail_base, "resolve_aimail_bin", lambda: "")
    out = aimail_base.notify_inbound_state("agent.acme@gw.test", "live")
    assert out["state"] == "no_cli"
    assert aimail_base.inbound_notify_is_warning(out) is False
    assert "no aimail CLI" in aimail_base.format_inbound_notify_line(out, "live")


# ── 3. argv, no shell, address-only payload ───────────────────────────────────
@pytest.mark.parametrize("state,flag", [("live", "--inbound-live"), ("down", "--inbound-down")])
def test_state_maps_to_its_flag_and_the_payload_is_the_address(monkeypatch, state, flag):
    monkeypatch.setattr(aimail_base, "resolve_aimail_bin", lambda: "/usr/local/bin/aimail")
    calls, run = _runner(rc=0)
    out = aimail_base.notify_inbound_state("agent.acme@gw.test", state, runner=run)

    assert out["state"] == "notified" and out["rc"] == 0
    assert calls == [("/usr/local/bin/aimail",
                      ["address", "-a", "agent.acme@gw.test", flag], 4.0)]
    # zero bridge semantics: no port, no protocol, no route verb in the payload
    assert not any(":" in a or "/api/" in a for a in calls[0][1][1:])


def test_bad_payload_is_reported_and_never_spawns(monkeypatch):
    monkeypatch.setattr(aimail_base, "resolve_aimail_bin", lambda: "/usr/local/bin/aimail")
    calls, run = _runner()
    assert aimail_base.notify_inbound_state("", "live", runner=run)["state"] == "failed"
    assert aimail_base.notify_inbound_state("a@gw.test", "sideways", runner=run)["state"] == "failed"
    assert calls == []


# ── 4. failures are reported, never raised ────────────────────────────────────
def test_a_nonzero_exit_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(aimail_base, "resolve_aimail_bin", lambda: "/usr/local/bin/aimail")
    _calls, run = _runner(rc=3, detail="exit 3")
    out = aimail_base.notify_inbound_state("agent.acme@gw.test", "live", runner=run)
    assert out["state"] == "failed" and out["rc"] == 3
    assert aimail_base.inbound_notify_is_warning(out) is True
    assert "not reported for agent.acme@gw.test" in aimail_base.format_inbound_notify_line(out, "live")


def test_a_raising_runner_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(aimail_base, "resolve_aimail_bin", lambda: "/usr/local/bin/aimail")

    def boom(_bin, _args, _timeout):
        raise TimeoutError("4s")

    out = aimail_base.notify_inbound_state("agent.acme@gw.test", "live", runner=boom)
    assert out["state"] == "failed" and "4s" in out["detail"]


# ── 5. one notification per binding ───────────────────────────────────────────
def _write_binding(home: pathlib.Path, sid: str, email: str) -> None:
    d = home / "systems" / sid / email.replace("@", "_").replace("/", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / "agentmail.json").write_text(
        '{"email": "%s", "system_id": "%s", "domain": "gw.test", "api_key": "%s", '
        '"webhook_url": "http://127.0.0.1:39100/aimail/inbound", "webhook_secret": "%s"}'
        % (email, sid, "k" * 64, "s" * 64))


def test_every_binding_gets_its_own_notification(home, monkeypatch):
    monkeypatch.setattr(aimail_base, "resolve_aimail_bin", lambda: "/usr/local/bin/aimail")
    _write_binding(home, "sys-1", "agent.one@gw.test")
    _write_binding(home, "sys-1", "agent.two@gw.test")

    calls, run = _runner(rc=0)
    out = aimail_base.notify_inbound_for_system("live", system_id="sys-1", runner=run)

    assert [(o["email"], o["state"]) for o in out] == [
        ("agent.one@gw.test", "notified"), ("agent.two@gw.test", "notified")]
    assert [c[1][2] for c in calls] == ["agent.one@gw.test", "agent.two@gw.test"]
    assert all(c[1][-1] == "--inbound-live" for c in calls)


def test_nothing_to_do_when_there_is_no_binding(home):
    assert aimail_base.notify_inbound_for_system("live", system_id="sys-1") == []
    assert aimail_base.format_inbound_notify_line({"state": "notified", "email": "a@gw.test",
                                                   "bin": "/x/aimail"}, "live") == \
        "inbound live reported for a@gw.test (/x/aimail)"


# ── 6. the ratchet: the SDK carries ZERO bridge symbols ───────────────────────
def _py_identifiers(tree: ast.AST) -> set:
    names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.arg):
            names.add(node.arg)
        elif isinstance(node, ast.alias):
            names.add((node.asname or node.name).split(".")[-1])
    return names


def test_pysdk_carries_zero_bridge_symbols():
    """Clause: no Python SDK module may define, call or import a retired symbol."""
    offenders = []
    for f in sorted((ROOT / "pysdk").rglob("*.py")):
        if "__pycache__" in f.parts:
            continue
        names = _py_identifiers(ast.parse(f.read_text(encoding="utf-8"), filename=str(f)))
        for sym in RETIRED_PY:
            if sym in names:
                offenders.append(f"{f.relative_to(ROOT)} references {sym}")
    assert offenders == [], "bridge symbols survived in the Python SDK: " + "; ".join(offenders)


def test_tssdk_carries_zero_bridge_symbols():
    """Clause: no shipped TS source may mention a retired symbol (comments stripped)."""
    import re
    offenders = []
    for f in sorted((ROOT / "tssdk" / "packages").glob("*/src/**/*.ts")):
        code = f.read_text(encoding="utf-8")
        code = re.sub(r"/\*.*?\*/", "", code, flags=re.S)
        code = re.sub(r"//[^\n]*", "", code)
        for sym in RETIRED_TS:
            if sym in code:
                offenders.append(f"{f.relative_to(ROOT)} references {sym}")
    assert offenders == [], "bridge symbols survived in the TS SDK: " + "; ".join(offenders)
