"""注册失败必须**不阻断** install/reset 主流程(F9 / 尽力语义)。

Pins the 2026-09-25 fix. `aimail_base.register_agent_email` raises **RuntimeError** on
any non-2xx gateway answer (`register failed: {'status': 401, 'error': ...}`), but
`_run_install_steps` only caught `SystemExit` around `register_default`/`register_all`
(and `cmd_reset` likewise). Consequence observed in the docker D1 gate: a 401 during
registration escaped to the top level → traceback, install rc≠0 — the opposite of the
declared "不阻塞 install 主流程(F9)" semantics, and the reason the D1 gate's register
step was silently broken for a whole round.

本测试用**合成平台定义**(不碰真实 registry / 不做任何网络或 docker 动作)隔离被测行为:
注册调用抛 RuntimeError ⇒ 执行器必须继续、必须发警告、警告里必须带异常内容(不许静默)。
"""
import importlib.util
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
if str(_REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(_REPO / "pysdk"))


def _load_cli():
    """cli/aimail has no .py suffix — load it as a module (top level only)."""
    loader = SourceFileLoader("aimail_cli_nonblocking_under_test", str(_CLI))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aimail_cli_nonblocking_under_test", loader))
    loader.exec_module(mod)
    return mod


class _Recorder:
    """替换 CLI 的输出函数, 收集 _warn/_ok/_fail 调用(不打印、不退出)。"""

    def __init__(self):
        self.warns, self.oks, self.fails = [], [], []

    def install(self, cli):
        cli._warn = lambda m: self.warns.append(str(m))
        cli._ok = lambda m: self.oks.append(str(m))
        cli._fail = lambda m, *a, **k: self.fails.append(str(m))


def _synthetic_platform(steps):
    return {"register": {"default_name": "default"}, "install_steps": steps}


def test_register_default_runtime_error_does_not_abort_install():
    cli = _load_cli()
    rec = _Recorder()
    rec.install(cli)
    cli._platform_def = lambda p: _synthetic_platform(
        [{"kind": "register_default", "warn_hint": "注册失败提示"}])

    def _boom(*_a, **_k):
        raise RuntimeError("register failed: {'status': 401, 'error': 'Invalid X-Api-Signature'}")

    cli._register_agent_now = _boom

    cli._run_install_steps("hermes", {"sid": "S1", "cfg": {}, "manager": "m@x", "home": "/tmp"})

    assert rec.fails == [], f"注册失败不得走 _fail: {rec.fails}"
    assert rec.oks == [], f"注册失败不得报成功: {rec.oks}"
    assert rec.warns, "注册失败必须发警告(不许静默)"
    joined = " ".join(rec.warns)
    assert "RuntimeError" in joined, f"警告须带异常类型: {rec.warns}"
    assert "Invalid X-Api-Signature" in joined, f"警告须带异常内容(可定位): {rec.warns}"


def test_register_all_runtime_error_does_not_abort_install():
    cli = _load_cli()
    rec = _Recorder()
    rec.install(cli)
    cli._platform_def = lambda p: _synthetic_platform(
        [{"kind": "register_all", "warn_hint": "全量注册失败提示"}])

    def _boom(*_a, **_k):
        raise RuntimeError("register failed: {'status': 500, 'error': 'boom'}")

    cli._run_register_all = _boom

    cli._run_install_steps("hermes", {"sid": "S1", "cfg": {}, "manager": "m@x",
                                      "home": "/tmp", "all_agents": True})

    assert rec.fails == [], f"全量注册失败不得走 _fail: {rec.fails}"
    assert rec.oks == [], f"全量注册失败不得报成功: {rec.oks}"
    assert rec.warns, "全量注册失败必须发警告"
    assert "RuntimeError" in " ".join(rec.warns)


def test_register_success_still_reports_ok():
    """反向保证: 修异常处理不得把成功路径也吞成警告。"""
    cli = _load_cli()
    rec = _Recorder()
    rec.install(cli)
    cli._platform_def = lambda p: _synthetic_platform(
        [{"kind": "register_default", "warn_hint": "注册失败提示"}])

    cli._register_agent_now = lambda *_a, **_k: None

    cli._run_install_steps("hermes", {"sid": "S1", "cfg": {}, "manager": "m@x", "home": "/tmp"})

    assert rec.warns == [], f"成功路径不得发警告: {rec.warns}"
    assert rec.oks, "成功路径必须报 ok"
