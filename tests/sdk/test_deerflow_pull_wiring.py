"""deer-flow pull 接线: 必须"包装宿主 lifespan", 且接线失败不得静默。

2026-09-28 实机缺陷(产品侧, 已修): `pysdk/deer-flow/aimail_inbound.py` 的
`start_pull_on_startup()` 曾用 `app.add_event_handler("startup"/"shutdown", …)`
接线, 而宿主 deer-flow 镜像(fastapi 0.136.1 / starlette 1.3.1)**已删除该 API**
⇒ 调用抛 `AttributeError`, 被产品自己的 `except Exception` **静默吞掉** ⇒
`app._aimail_pull_wired` 从未置位 ⇒ 纯地址级(无系统级安装)的 pull 轮询循环
**从未建立**(门禁探针⑪ 只能看到 "0 条 aimail-pull")。

本文件是 hermetic 防退化层(零真宿主、零网络、零第三方时序假设):
  ① 现代 app 形态(只有 `router.lifespan_context`)⇒ 我们的 startup 必被调用,
     且次序(宿主 ENTER → 我们的 startup → 宿主 state 透传 → 我们的 shutdown)、
     关停方向都对;
  ② **模拟 `add_event_handler` 不存在** ⇒ 仍必须成功接线(主路径不得回退到已删 API);
     并证明"旧实现形态"在这种 app 上必炸(断言可证伪, 不是空转);
  ③ 接线失败**不得静默**: 大声 ERROR 日志(带异常类型与原因) + 返回 False +
     app 上可查询的失败标志;
  ④ 轮询体自身抛异常时同样不得静默(不能只剩一句 "0 started")。
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

_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_DIR = os.path.join(_REPO, "pysdk", "deer-flow")
_MODULE = os.path.join(_MODULE_DIR, "aimail_inbound.py")
_LOG = "aimail_inbound"  # aimail_inbound.py 里 logger = getLogger(__name__)

try:
    import fastapi  # noqa: F401  (模块级依赖; 缺则整文件跳过)
except ModuleNotFoundError as e:  # pragma: no cover - 无 fastapi 的环境
    pytest.skip(f"fastapi 未安装(可选 extra deerflow)⇒ 本层跳过: {e}",
                allow_module_level=True)

if _MODULE_DIR not in sys.path:
    sys.path.insert(0, _MODULE_DIR)

# 经 importlib 取(与 test_deerflow_inbound_route.py 同一挂载方式: 目录进 sys.path)
ib = importlib.import_module("aimail_inbound")


# ── 现代 FastAPI 形态的最小替身 ─────────────────────────────────────
# 故意**只给** `router.lifespan_context`(已删的 `add_event_handler` / `on_event`
# 一概不提供) —— 这正是宿主 deer-flow 镜像的实际形态。


class _FakeRouter:
    def __init__(self, host_lifespan):
        self.lifespan_context = host_lifespan


class _FakeApp:
    def __init__(self, host_lifespan):
        self.router = _FakeRouter(host_lifespan)


def _host_lifespan(events, state=None):
    """宿主的 lifespan(模拟 deer-flow `app.py:315-316` 的 asynccontextmanager)。"""

    @contextlib.asynccontextmanager
    async def _lifespan(app):
        events.append("host-enter")
        try:
            yield state
        finally:
            events.append("host-exit")

    return _lifespan


async def _use(app):
    """像 Starlette 那样跑一遍宿主 lifespan, 并把 yield 到的 state 交回。"""
    async with app.router.lifespan_context(app) as st:
        return st


# ── ① 现代形态: 我们的 startup 必被调用(次序/透传/关停方向) ──────────


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
        f"生命周期次序不对: {events}")
    assert seen == {"k": "v"}, (
        f"宿主 yield 的 state 必须原样透传(丢了会静默改宿主行为), 实测 {seen!r}")


def test_wrapped_lifespan_runs_body_between_start_and_stop():
    """整段生命周期形状: host-enter → our-start → body → our-stop → host-exit。"""
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


# ── ② add_event_handler 不存在 ⇒ 仍必须接线成功 ─────────────────────


def test_wiring_succeeds_when_add_event_handler_does_not_exist(monkeypatch):
    events: list = []
    app = _FakeApp(_host_lifespan(events))
    # 现代形态: 已删 API 确实不存在 —— 这就是"模拟不存在"的实证, 不是假设
    assert not hasattr(app, "add_event_handler")
    with pytest.raises(AttributeError):
        # 旧实现形态在此必炸(可证伪): 用 getattr 取, 保持"已删 API"语义
        getattr(app, "add_event_handler")("startup", lambda: None)

    calls: list = []

    def _fake_start(**kw):
        calls.append(kw)
        return []

    monkeypatch.setattr(ib._ab, "start_agent_pull_entries", _fake_start)
    monkeypatch.setattr(ib, "_pull_system_id", lambda: "")

    assert ib.start_pull_on_startup(app) is True, "主路径不得依赖已删除的 API"
    assert getattr(app, "_aimail_pull_wired", "MISSING") is True
    assert getattr(app, "_aimail_pull_wire_error", "MISSING") is None

    # 绿口径 = 我们那条线**真起来了**: 真跑一遍宿主 lifespan, startup 必被调用
    asyncio.run(_use(app))
    assert len(calls) == 1, f"我们的 startup 没被宿主 lifespan 调到: {calls}"

    # 幂等: 重复调用不重复包装
    wrapped = app.router.lifespan_context
    assert ib.start_pull_on_startup(app) is True
    assert app.router.lifespan_context is wrapped, "重复调用又包了一层"
    asyncio.run(_use(app))
    assert len(calls) == 2, "重复包装 ⇒ startup 被多次调用"


def test_source_never_calls_removed_event_hook_api():
    """静态闸(AST): 适配层不得再出现 add_event_handler / on_event 的**代码**引用。

    按 AST 判(不是 grep) —— 文档串/注释里解释这段历史仍合法。
    """
    with open(_MODULE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read(), filename=_MODULE)
    bad = [(n.attr, n.lineno) for n in ast.walk(tree)
           if isinstance(n, ast.Attribute) and n.attr in {"add_event_handler", "on_event"}]
    assert bad == [], f"适配层又用上已删除的事件钩子 API: {bad}"
    # 主路径必须真用 router.lifespan_context(否则本断言会因为"什么都没接"而真空绿)
    used = [n.attr for n in ast.walk(tree)
            if isinstance(n, ast.Attribute) and n.attr == "lifespan_context"]
    assert used, "适配层没有引用 router.lifespan_context ⇒ 防退化断言空转"


def test_survives_fastapi_lifespan_remerge_after_wiring():
    """真宿主形状: 补丁调用点**之后** host 还会 include_router(实测 deer-flow 35 次),
    而 FastAPI 的 include_router 会用 `_merge_lifespan_context(self.…, router.…)`
    把 `lifespan_context` **重新 merge** ⇒ 我们的包装被嵌套进链条(行为保留)但已不是
    最外层 callable。本断言钉两件事: ① 轮询 startup 仍被调到 ② app 级标志不受 merge 影响。
    """
    from fastapi.routing import _merge_lifespan_context  # 宿主同款实现

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

    # 模拟后续 include_router 触发的 re-merge(把我们的包装当 original_context)
    app.router.lifespan_context = _merge_lifespan_context(
        app.router.lifespan_context, _default)
    assert not hasattr(app.router.lifespan_context, "_aimail_pull_wrapped"), (
        "前提失效: 本环境的 FastAPI 没有把包装藏进 merge 链 ⇒ 该断言需复核")

    asyncio.run(_use(app))
    assert "our-start" in events, f"re-merge 之后我们的 startup 丢了: {events}"


# ── ③ 接线失败不得静默 ─────────────────────────────────────────────


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
        "接线失败不许把幂等位当成功"
    assert "boom-wiring" in (getattr(app, "_aimail_pull_wire_error", "") or ""), \
        "失败原因必须留在 app 上可查询"
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("wiring FAILED" in m and "boom-wiring" in m for m in msgs), \
        f"接线失败必须大声记 ERROR(带原因); 实测日志: {msgs}"


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


# ── ④ 轮询体自身抛异常时也不得静默 ─────────────────────────────────


def test_pull_start_exception_is_loud_but_does_not_kill_host(monkeypatch, caplog):
    events: list = []
    app = _FakeApp(_host_lifespan(events))

    def _boom(**kw):
        raise RuntimeError("boom-start")

    monkeypatch.setattr(ib._ab, "start_agent_pull_entries", _boom)
    monkeypatch.setattr(ib, "_pull_system_id", lambda: "")

    assert ib.start_pull_on_startup(app) is True, "接线本身是成功的"

    with caplog.at_level(logging.ERROR, logger=_LOG):
        asyncio.run(_use(app))          # 宿主 lifespan 不被拖垮
    msgs = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    assert any("pull entry start FAILED" in m and "boom-start" in m for m in msgs), \
        f"轮询起不来必须大声(不能只剩一句 '0 started'); 实测日志: {msgs}"
    assert events == ["host-enter", "host-exit"], (
        f"循环起不来不许拖垮宿主 lifespan: {events}")
