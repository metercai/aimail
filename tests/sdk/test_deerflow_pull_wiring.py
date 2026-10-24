"""deer-flow pull wiring: must "wrap host lifespan", and wiring failure must not be silent.

2026-09-28 real-machine defect (product side, fixed): `pysdk/deer-flow/aimail_inbound.py`'s
`start_pull_on_startup()` previously used `app.add_event_handler("startup"/"shutdown", …)`
for wiring, but the host deer-flow image (fastapi 0.136.1 / starlette 1.3.1) **has removed this API**
 ⇒ the call raises `AttributeError`, which the product's own `except Exception` **silently swallows** ⇒
 `app._aimail_pull_wired` is never set ⇒ the pure address-level (no system-level install) pull polling loop
**is never established** (gate probe ⑪ can only see "0 aimail-pull entries").

This file is a hermetic regression prevention layer (zero real host, zero network, zero third-party timing assumptions):
  ① Modern app shape (only `router.lifespan_context`) ⇒ our startup must be called,
     and the order (host ENTER → our startup → host state pass-through → our shutdown),
     shutdown direction are all correct;
  ② **Simulate `add_event_handler` not existing** ⇒ must still wire successfully (main path must not fall back to removed API);
     and prove that the "old implementation shape" must blow up on such an app (assertions are falsifiable, not spinning idly);
  ③ Wiring failure **must not be silent**: loud ERROR log (with exception type and reason) + return False +
     queryable failure flag on the app;
  ④ When the polling body itself throws an exception, it must also not be silent (cannot just leave a single "0 started").
"""
from __future__ import annotations

import ast
import asyncio
import contextlib
import importlib
import logging
import os
import sys

import pytest

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_MODULE_DIR = os.path.join(_REPO, "pysdk", "deer-flow")
_MODULE = os.path.join(_MODULE_DIR, "aimail_inbound.py")
_LOG = "aimail_inbound"  # in aimail_inbound.py logger = getLogger(__name__)

try:
    import fastapi  # noqa: F401  (module-level dependency; skip entire file if missing)
except ModuleNotFoundError as e:  # pragma: no cover - environments without fastapi
    pytest.skip(f"fastapi not installed (optional extra deerflow) ⇒ skip this layer: {e}",
                allow_module_level=True)

if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)

# Retrieved via importlib (same mounting method as test_deerflow_inbound_route.py: directory into sys.path)
ib = importlib.import_module("aimail_inbound")


# ── Minimal stand-in for modern FastAPI shape ─────────────────────────────────────
# Deliberately **only provides** `router.lifespan_context` (removed `add_event_handler` / `on_event`
# are not provided at all) —— this is exactly the actual shape of the host deer-flow image.


class _FakeRouter:
    def __init__(self, host_lifespan):
        self.lifespan_context = host_lifespan


class _FakeApp:
    def __init__(self, host_lifespan):
        self.router = _FakeRouter(host_lifespan)


def _host_lifespan(events, state=None):
    """Host's lifespan (simulating deer-flow `app.py:315-316`'s asynccontextmanager)."""

    @contextlib.asynccontextmanager
    async def _lifespan(app):
        events.append("host-enter")
        try:
            yield state
        finally:
            events.append("host-exit")

    return _lifespan


async def _use(app):
    """Run the host lifespan the way Starlette does, and hand back the yielded state."""
    async with app.router.lifespan_context(app) as st:
        return st


# ── ① Modern shape: our startup must be called (order/pass-through/shutdown direction) ──────────


def test_wrap_runs_our_startup_inside_host_lifespan_and_passes_state_through():
    events: list = []
    app = _FakeApp(_host_lifespan(events, state={"k": "v"}))

    async def _on_start():
        events.append("our-start")

    async def _on_stop():
        events.append("our-stop")

    ib._wire_pull_to_lifespan(app, _on_start, _on_stop)
    seen = asyncio.run(_use(app))

    assert events == ["host-enter", "our-start", "our-stop", "host-exit"], (
        f"Lifecycle order is incorrect: {events}")
    assert seen == {"k": "v"}, (
        f"the host's yielded state must be passed through as-is (dropping it would silently change host behavior); measured {seen!r}")


def test_wrapped_lifespan_runs_body_between_start_and_stop():
    """Entire lifecycle shape: host-enter → our-start → body → our-stop → host-exit."""
    events: list = []
    app = _FakeApp(_host_lifespan(events))

    async def _on_start():
        events.append("our-start")

    async def _on_stop():
        events.append("our-stop")

    ib._wire_pull_to_lifespan(app, _on_start, _on_stop)

    async def _run():
        async with app.router.lifespan_context(app):
            events.append("body")

    asyncio.run(_run())
    assert events == ["host-enter", "our-start", "body", "our-stop", "host-exit"], events


# ── ② add_event_handler does not exist ⇒ must still wire successfully ─────────────────────


def test_wiring_succeeds_when_add_event_handler_does_not_exist(monkeypatch):
    events: list = []
    app = _FakeApp(_host_lifespan(events))
    # Modern shape: removed API indeed does not exist —— this is empirical proof of "simulating non-existence", not an assumption
    assert not hasattr(app, "add_event_handler")
    with pytest.raises(AttributeError):
        # Old implementation shape must blow up here (falsifiable): use getattr to retrieve, maintaining "removed API" semantics
        getattr(app, "add_event_handler")("startup", lambda: None)

    calls: list = []

    def _fake_start(**kw):
        calls.append(kw)
        return []

    monkeypatch.setattr(ib._ab, "start_agent_pull_entries", _fake_start)
    monkeypatch.setattr(ib, "_pull_system_id", lambda: "")

    assert ib.start_pull_on_startup(app) is True, "Main path must not rely on deleted API"
    assert getattr(app, "_aimail_pull_wired", "MISSING") is True
    assert getattr(app, "_aimail_pull_wire_error", "MISSING") is None

    # Green criterion = our line **actually started**: run the host lifespan once, startup must be called
    asyncio.run(_use(app))
    assert len(calls) == 1, f"Our startup was not called by host lifespan: {calls}"

    # Idempotent: repeated calls do not re-wrap
    wrapped = app.router.lifespan_context
    assert ib.start_pull_on_startup(app) is True
    assert app.router.lifespan_context is wrapped, "Repeated call wrapped another layer"
    asyncio.run(_use(app))
    assert len(calls) == 2, "Duplicate wrapping ⇒ startup called multiple times"


def test_source_never_calls_removed_event_hook_api():
    """Static gate (AST): the adapter layer must no longer contain any **code** references to add_event_handler / on_event.

    Judged by AST (not grep) —— explaining this history in docstrings/comments is still legal.
    """
    with open(_MODULE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=_MODULE)
    bad = [(n.attr, n.lineno) for n in ast.walk(tree)
           if isinstance(n, ast.Attribute) and n.attr in {"add_event_handler", "on_event"}]
    assert bad == [], f"Adapter layer used deleted event hook API again: {bad}"
    # Main path must actually use router.lifespan_context (otherwise this assertion would be vacuously green because "nothing is connected")
    used = [n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "lifespan_context"]
    assert used, "Adapter layer does not reference router.lifespan_context ⇒ anti-regression assertion is a no-op"


def test_survives_fastapi_lifespan_remerge_after_wiring():
    """Real host shape: **after** the patch call site the host still include_router-s (measured 35 times in deer-flow),
    and FastAPI's include_router uses `_merge_lifespan_context(self.…, router.…)`
    re-merging `lifespan_context` ⇒ our wrapper gets nested into the chain (behavior preserved) but is no longer
    the outermost callable. This assertion pins down two things: ① polling startup is still called ② app-level flags are not affected by the merge.
    """
    from fastapi.routing import _merge_lifespan_context  # Same implementation as host

    events: list = []
    app = _FakeApp(_host_lifespan(events))

    async def _on_start():
        events.append("our-start")

    async def _noop():
        pass

    ib._wire_pull_to_lifespan(app, _on_start, _noop)

    @contextlib.asynccontextmanager
    async def _default(_app):
        events.append("router-default")
        yield None

    # Simulate the re-merge triggered by subsequent include_router (treating our wrapper as original_context)
    app.router.lifespan_context = _merge_lifespan_context(
        app.router.lifespan_context, _default)
    assert not hasattr(app.router.lifespan_context, "_aimail_pull_wrapped"), (
        "Premise failed: FastAPI in this environment does not hide the wrapper in the merge chain ⇒ this assertion needs review")

    asyncio.run(_use(app))
    assert "our-start" in events, f"after the re-merge our startup was lost: {events}"


# ── ③ Wiring failure must not be silent ─────────────────────────────────────────────


def test_wiring_failure_is_loud_and_queryable(caplog):
    class _BrokenRouter:
        @property
        def lifespan_context(self):
            return object()

        @lifespan_context.setter
        def lifespan_context(self, value):
            raise RuntimeError("boom-wiring")

    class _BrokenApp:
        router = _BrokenRouter()

    app = _BrokenApp()
    with caplog.at_level(logging.ERROR, logger=_LOG):
        assert ib.start_pull_on_startup(app) is False

    assert getattr(app, "_aimail_pull_wired", "MISSING") is False, \
        "Wiring failure must not treat idempotency flag as success"
    assert "boom-wiring" in (getattr(app, "_aimail_pull_wire_error", "") or ""), \
        "Failure reason must remain queryable on app"
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("wiring FAILED" in m and "boom-wiring" in m for m in msgs), \
        f"Wiring failure must loudly log ERROR (with reason); measured logs: {msgs}"


def test_missing_lifespan_context_is_also_loud(caplog):
    class _NoLifespan:
        pass

    class _App:
        router = _NoLifespan()

    app = _App()
    with caplog.at_level(logging.ERROR, logger=_LOG):
        assert ib.start_pull_on_startup(app) is False
    assert "lifespan_context" in (getattr(app, "_aimail_pull_wire_error", "") or "")
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("wiring FAILED" in m for m in msgs), msgs


# ── ④ Must not silently swallow exceptions thrown by the polling body itself ─────────────────────────────────


def test_pull_start_exception_is_loud_but_does_not_kill_host(monkeypatch, caplog):
    events: list = []
    app = _FakeApp(_host_lifespan(events))

    def _boom(**kw):
        raise RuntimeError("boom-start")

    monkeypatch.setattr(ib._ab, "start_agent_pull_entries", _boom)
    monkeypatch.setattr(ib, "_pull_system_id", lambda: "")

    assert ib.start_pull_on_startup(app) is True, "Wiring itself is successful"

    with caplog.at_level(logging.ERROR, logger=_LOG):
        asyncio.run(_use(app))          # Host lifespan is not dragged down
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("pull entry start FAILED" in m and "boom-start" in m for m in msgs), \
        f"Polling failure to start must be loud (cannot just be '0 started'); measured logs: {msgs}"
    assert events == ["host-enter", "host-exit"], (
        f"Loop failure to start must not drag down host lifespan: {events}")
