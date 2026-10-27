"""Address-level local lifecycle (owner rulings 2026-10-27).

`aimail_address(action=bind|pause|resume|unbind)` — the single agent-facing
lifecycle entry (D3 bind three-modes / D4 pause-resume-unbind / D2 archive).
Purely local: the cloud key stays active; unbind ARCHIVES the binding (the
key bytes survive), so a code-less re-bind restores receiving.

Covered here:
- bind mode ① idempotent (already bound ⇒ 0 gateway calls, code not consumed)
- bind mode ② local restore from archive (0 gateway calls, pointer written)
- bind mode ③ cloud activation (the existing persist chain, unchanged shape)
- pause / resume (local flag only; no gateway call; resume re-fires hot-add)
- unbind (archive + this profile's pointer cleared only on email match)
- pointer ownership = the executing profile (resolver injection, not a
  schema parameter)
- file-as-signal: a running loop stops on pause/archive within a beat
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

import aimail_base as base
from aimail_base import (
    BINDING_FILE,
    POINTER_FILE,
)

ADDR = "agent.demo@example.test"
SID = "shared_addr_example.test"


@pytest.fixture
def env_home(tmp_path, monkeypatch):
    """Isolated AIMAIL_HOME + profile dirs; no resolver injected (generic
    host ⇒ pointer effects only when profile_home is explicit). The
    process-wide agent context is snapshotted/restored: the lifecycle
    bind path injects it, and a leak would re-aim later tests in this
    process (same isolation the persist test uses)."""
    aimail_home = tmp_path / "aimail"
    prof_a = tmp_path / "profA"
    prof_b = tmp_path / "profB"
    monkeypatch.setenv("AIMAIL_HOME", str(aimail_home))
    monkeypatch.delenv("AIMAIL_GW_URL", raising=False)
    saved_resolver = base._PROFILE_DIR_RESOLVER
    base._PROFILE_DIR_RESOLVER = None
    saved_ctx = base._ACTIVE_AGENT_CONFIG
    base._ACTIVE_AGENT_CONFIG = None
    saved_loader = base._CONFIG_LOADER
    base._CONFIG_LOADER = None
    # set_agent_context (the bind path) also touches these env vars + the
    # loader; snapshot all of them so a later test in this process sees the
    # environment as it was before this file ran.
    _CTX_ENV = ("AIMAIL_AGENT_EMAIL", "AIMAIL_AGENT_ID", "AIMAIL_SYSTEM_ID")
    saved_env = {k: os.environ.get(k) for k in _CTX_ENV}
    for k in _CTX_ENV:
        os.environ.pop(k, None)
    yield {"aimail": aimail_home, "a": prof_a, "b": prof_b}
    for k, v in saved_env.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    base._ACTIVE_AGENT_CONFIG = saved_ctx
    base._CONFIG_LOADER = saved_loader
    base._PROFILE_DIR_RESOLVER = saved_resolver


def _binding_dir(aimail_home: Path) -> Path:
    # Same cleaning rule the SDK uses (address → dir name, @ ⇒ _).
    return aimail_home / "systems" / SID / base._clean_agent_dir_name(ADDR)


def _write_binding(aimail_home: Path, *, paused: bool = False,
                   archive: bool = False, key: str = "sk-test-1") -> Path:
    d = _binding_dir(aimail_home)
    d.mkdir(parents=True, exist_ok=True)
    p = d / BINDING_FILE
    cfg = {
        "agent_id": "agent.demo",
        "email": ADDR,
        "gateway_url": "http://127.0.0.1:1",
        "domain": "example.test",
        "system_id": SID,
        "api_key": key,
        "webhook_secret": "0" * 64,
    }
    if paused:
        cfg["paused"] = True
    p.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n")
    os.chmod(p, 0o600)
    if archive:
        a = d / (BINDING_FILE + base.BINDING_ARCHIVED_SUFFIX)
        os.rename(p, a)
        return a
    return p


def _write_pointer(prof: Path, email: str = ADDR, sid: str = SID) -> Path:
    prof.mkdir(parents=True, exist_ok=True)
    p = prof / POINTER_FILE
    p.write_text(json.dumps({"system_id": sid, "email": email}) + "\n")
    return p


class _NoNetwork:
    """Fail loudly if the lifecycle ever touches the gateway."""

    def __getattr__(self, name):
        def _boom(*a, **k):
            raise AssertionError("lifecycle action must not call the gateway")
        return _boom


def test_bind_idempotent_never_calls_gateway(env_home):
    """D3 mode ①: an existing active binding ⇒ already_bound, 0 gateway
    calls, a supplied code is NOT consumed, the key is untouched."""
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    p = _write_binding(home, key="sk-keep-me")
    before = p.read_text()

    res = aimail_address("bind", code="shared_a-NEW-CODE", email=ADDR)

    assert res["success"] is True, res
    assert res["reason"] == "already_bound", res
    assert res["gateway_calls"] == 0
    assert p.read_text() == before, "binding file must be untouched"
    # no archive side effect
    assert not (p.parent / (BINDING_FILE + base.BINDING_ARCHIVED_SUFFIX)).exists()


def test_bind_restores_archive_without_code(env_home):
    """D3 mode ②: unbound (archived) + no code ⇒ restore by rename,
    0 gateway calls, pointer written to the executing profile."""
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    a = _write_binding(home, key="sk-restored", archive=True)
    assert a.is_file() and not a.with_name(BINDING_FILE).is_file()

    res = aimail_address("bind", email=ADDR,
                         profile_home=str(env_home["a"]))

    assert res["success"] is True, res
    assert res["reason"] == "restored", res
    assert res["gateway_calls"] == 0
    assert res["system_id"] == SID
    p = a.with_name(BINDING_FILE)
    assert p.is_file() and not a.is_file()
    cfg = json.loads(p.read_text())
    assert cfg["api_key"] == "sk-restored"
    assert cfg.get("paused") is not True
    # pointer landed at the executing profile
    ptr = _pointer_file(env_home["a"])
    assert ptr.is_file()
    assert json.loads(ptr.read_text()) == {"system_id": SID, "email": ADDR}


def test_bind_cloud_activation_requires_code_and_gateway(env_home):
    """D3 mode ③: nothing local + no code ⇒ not-bound (never guessed)."""
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    (home / "systems").mkdir(parents=True, exist_ok=True)

    res = aimail_address("bind", email=ADDR)
    assert res["success"] is False
    assert res["reason"] == "not-bound", res

    # code given but no gateway url (and no env) ⇒ explicit failure
    res = aimail_address("bind", code="shared_a-X", email=ADDR)
    assert res["success"] is False
    assert res["reason"] == "gateway-url-required", res


def test_pause_resume_local_only(env_home):
    """D4: pause/resume flip the local flag; no gateway call; idempotent
    states reported; resume re-fires the hot-add hook."""
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    p = _write_binding(home)

    fired = []
    hook = lambda cfg: fired.append(cfg.get("email"))  # noqa: E731
    base.register_binding_created_hook(hook)
    try:
        r = aimail_address("pause", email=ADDR)
        assert r["success"] is True and r["reason"] == "paused", r
        assert json.loads(p.read_text())["paused"] is True

        # idempotent
        r = aimail_address("pause", email=ADDR)
        assert r["success"] is True and r["reason"] == "already-paused", r

        r = aimail_address("resume", email=ADDR)
        assert r["success"] is True and r["reason"] == "resumed", r
        assert "paused" not in json.loads(p.read_text())
        assert fired == [ADDR], "resume must re-fire the hot-add hook"

        r = aimail_address("resume", email=ADDR)
        assert r["reason"] == "already-active", r
    finally:
        base.unregister_binding_created_hook(hook)


def test_pause_resume_refuse_when_not_bound(env_home):
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    (home / "systems").mkdir(parents=True, exist_ok=True)

    r = aimail_address("pause", email=ADDR)
    assert r["success"] is False and r["reason"] == "not-bound", r

    # an archived binding exists ⇒ the error names the restore path
    _write_binding(home, archive=True)
    r = aimail_address("resume", email=ADDR)
    assert r["success"] is False and r["reason"] == "not-bound", r
    assert "bind" in r["error"]


def test_unbind_archives_and_clears_this_profile_pointer(env_home):
    """D2 + pointer ruling: unbind archives (key bytes survive) and clears
    ONLY this profile's pointer when its email matches."""
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    p = _write_binding(home, key="sk-survivor")
    ptr_a = _write_pointer(env_home["a"], email=ADDR)
    # a DIFFERENT profile's pointer (another mailbox) must never be touched
    ptr_b = _write_pointer(env_home["b"], email="other@example.test")

    r = aimail_address("unbind", email=ADDR, profile_home=str(env_home["a"]))

    assert r["success"] is True and r["reason"] == "archived", r
    a = p.with_name(BINDING_FILE + base.BINDING_ARCHIVED_SUFFIX)
    assert a.is_file() and not p.is_file()
    assert json.loads(a.read_text())["api_key"] == "sk-survivor", \
        "the key bytes must survive the archive"
    assert r["pointer_cleared"] is True
    assert not ptr_a.is_file()
    assert ptr_b.is_file(), "another mailbox's pointer must not be touched"

    # unbind again: no ACTIVE binding left ⇒ not-bound, and the hint
    # names the restore path (idempotent at the file level: the archive
    # is never destroyed by a second unbind)
    r = aimail_address("unbind", email=ADDR, profile_home=str(env_home["a"]))
    assert r["success"] is False and r["reason"] == "not-bound", r
    assert "bind" in r["error"]
    assert a.is_file(), "the archive must survive a second unbind attempt"

    # unbind with no local trace at all
    r = aimail_address("unbind", email="ghost@example.test",
                       profile_home=str(env_home["a"]))
    assert r["success"] is False and r["reason"] == "not-bound", r


def test_unbind_clears_pointer_only_on_email_match(env_home):
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    p = _write_binding(home)
    # pointer points at ANOTHER mailbox of the same profile
    ptr = _write_pointer(env_home["a"], email="other@example.test")

    r = aimail_address("unbind", email=ADDR, profile_home=str(env_home["a"]))
    assert r["success"] is True
    assert r["pointer_cleared"] is False
    assert ptr.is_file(), "email-mismatched pointer must stay"


def test_pointer_belongs_to_executing_profile_via_resolver(env_home,
                                                           monkeypatch):
    """The agent-visible schema has NO profile parameter: the pointer goes
    to the executing profile resolved by the injected resolver
    (hermes: HERMES_PROFILE_DIR → get_hermes_home() → ~/.hermes)."""
    from aimail_tools import aimail_address
    home = env_home["aimail"]
    _write_binding(home, key="sk-resolver", archive=True)

    monkeypatch.setattr(base, "_PROFILE_DIR_RESOLVER",
                        lambda: str(env_home["b"]))
    res = aimail_address("bind", email=ADDR)  # NO profile_home passed

    assert res["success"] is True, res
    assert _pointer_file(env_home["b"]).is_file()
    assert not _pointer_file(env_home["a"]).exists()


def test_bad_action_and_missing_email(env_home):
    from aimail_tools import aimail_address
    assert aimail_address("explode", email=ADDR)["reason"] == "bad-action"
    assert aimail_address("pause", email="")["reason"] == "missing-email"


def test_paused_binding_not_polled(env_home):
    """A paused binding is a not-enabled pull decision (reason=paused)."""
    home = env_home["aimail"]
    p = _write_binding(home, paused=True)
    cfg = json.loads(p.read_text())
    cfg["_config_path"] = str(p)
    d = base.resolve_agent_pull_settings(cfg, env={})
    assert d["enabled"] is False and d["reason"] == "paused", d


def test_file_as_signal_stops_the_loop_on_pause(env_home):
    """The running loop re-checks its own binding file on every natural
    beat: pausing the binding stops the loop within one interval (no
    cross-process hook)."""
    from aimail_tools import aimail_address

    class _Client:
        def __init__(self):
            self.rounds = 0

        def start_polling(self, on_email, interval, limit, max_rounds=None,
                          stop_event=None, mode=None):
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                self.rounds += 1
                stop_event.wait(0.02)
            return {"pulled": 0, "acked": 0, "errors": 0}

    home = env_home["aimail"]
    p = _write_binding(home)
    cfg = json.loads(p.read_text())
    cfg["_config_path"] = str(p)

    client = _Client()
    handle = base.start_agent_pull_entry(
        cfg, env={"AIMAIL_PULL_INTERVAL_MS": "20"},
        client_for=lambda _c: client)
    assert handle is not None
    # let it run a couple of beats
    for _ in range(30):
        if client.rounds >= 2:
            break
        time.sleep(0.01)
    assert client.rounds >= 2, "loop must be running"

    r = aimail_address("pause", email=ADDR)
    assert r["success"] is True

    # within a bounded number of beats the loop must notice the pause
    deadline = time.time() + 2.0
    while handle.alive() and time.time() < deadline:
        time.sleep(0.02)
    assert not handle.alive(), "paused binding must stop the loop ≤1 beat"
    # the handle records WHY it stopped (file-signal ⇒ re-armable on resume)
    assert handle._stop_reason == "file"


def test_file_as_signal_stops_the_loop_on_archive(env_home):
    from aimail_tools import aimail_address

    class _Client:
        def __init__(self):
            self.rounds = 0

        def start_polling(self, on_email, interval, limit, max_rounds=None,
                          stop_event=None, mode=None):
            while True:
                if stop_event is not None and stop_event.is_set():
                    break
                self.rounds += 1
                stop_event.wait(0.02)
            return {"pulled": 0, "acked": 0, "errors": 0}

    home = env_home["aimail"]
    p = _write_binding(home)
    cfg = json.loads(p.read_text())
    cfg["_config_path"] = str(p)

    client = _Client()
    handle = base.start_agent_pull_entry(
        cfg, env={"AIMAIL_PULL_INTERVAL_MS": "20"},
        client_for=lambda _c: client)
    assert handle is not None
    for _ in range(30):
        if client.rounds >= 2:
            break
        time.sleep(0.01)

    r = aimail_address("unbind", email=ADDR)
    assert r["success"] is True

    deadline = time.time() + 2.0
    while handle.alive() and time.time() < deadline:
        time.sleep(0.02)
    assert not handle.alive(), "archived binding must stop the loop ≤1 beat"
    assert handle._stop_reason == "file"


def _pointer_file(prof: Path) -> Path:
    return prof / POINTER_FILE
