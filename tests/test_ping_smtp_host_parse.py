"""SMTP 发送路径的 host 解析守卫（2026-09-29 CLI L2 / J5-1 实测）。

故障原文（iso L2 · openclaw 容器）：
    File "/app/.aimail/bin/aimail-src/cli/ping_test.py", line 125, in _smtp_send_ping
      s.connect((host, 25))
  socket.gaierror: [Errno -2] Name or service not known

成因：`_smtp_send_ping` 只剥 scheme（顺带剥 path），带端口的 `gateway_url`
（`http://127.0.0.1:34401` —— 夹具与生产都是这一形态）整串被当成 HOST ⇒
`getaddrinfo("127.0.0.1:34401")` 必失败，SMTP 路径只对默认端口的网关可用。
修法：与 `cli/send_welcome.py` 同名机制同一修法（`urlparse(...).hostname`）；
端口仍保持 25（SMTP 约定；SMTP 不在 25 的网关需要 SMTP 侧旋钮 = 已登记项，
不在本修范围）。

可证伪（红锚）：把 host 解析退回「只剥 scheme」的旧表达式，
`test_red_anchor_old_parsing_yields_host_with_port` 里的同一条断言即红
（该测试真的把旧写法跑出来，证明它不是装饰）。
"""
import importlib.util
import pathlib
import socket
import sys

import pytest

REPO = pathlib.Path(__file__).resolve().parents[1]
PING_TEST = REPO / "cli" / "ping_test.py"

_FIXED = ('    from urllib.parse import urlparse\n'
          '    raw = gw_url if "//" in gw_url else f"http://{gw_url}"\n'
          '    host = urlparse(raw).hostname or ""\n')
_OLD = ('    host = gw_url.replace("https://", "").replace("http://", "").split("/")[0]\n')


class _Stop(Exception):
    """sentinel: raised from the stubbed connect() to stop the SMTP flow."""

    def __init__(self, addr):
        super().__init__(str(addr))
        self.addr = addr


class _RecordingSocket:
    """socket stub: records the address handed to connect(), then aborts."""

    def __init__(self, *a, **k):
        pass

    def settimeout(self, _t):
        pass

    def connect(self, addr):
        raise _Stop(addr)


def _load(name: str, path: pathlib.Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def ping_mod():
    return _load("ping_test_under_test", PING_TEST)


def _connect_target(mod, monkeypatch, gw_url: str):
    """Run the real send path far enough to capture what connect() receives.

    edition="base" keeps the advanced-only auth.local encoding out of the way;
    the host/port computation is identical for both editions.
    """
    monkeypatch.setattr(mod.socket, "socket", _RecordingSocket)
    with pytest.raises(_Stop) as ei:
        mod._smtp_send_ping(gw_url, "ab" * 32, "agent@x.test", "mgr@y.test", "pid", "base")
    return ei.value.addr


# ── green: what the fixed code hands to connect() ──────────────────────────────

def test_connect_target_has_no_port(ping_mod, monkeypatch):
    """夹具形态 `http://127.0.0.1:34401` ⇒ HOST 必须是 127.0.0.1（不带端口）。"""
    assert _connect_target(ping_mod, monkeypatch, "http://127.0.0.1:34401") == ("127.0.0.1", 25)


def test_connect_host_is_resolvable(ping_mod, monkeypatch):
    """修后 HOST 能被真正解析（红态时 getaddrinfo 直接 gaierror）。"""
    host, port = _connect_target(ping_mod, monkeypatch, "http://127.0.0.1:34401")
    infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    assert infos, f"getaddrinfo({host!r}, {port}) 无结果"


@pytest.mark.parametrize("gw_url,host", [
    ("http://127.0.0.1:34401", "127.0.0.1"),
    ("https://127.0.0.1:34401", "127.0.0.1"),
    ("127.0.0.1:34401", "127.0.0.1"),          # 无 scheme（契约允许的形态）
    ("https://gw.example.test", "gw.example.test"),
    ("http://gw.example.test:8443/", "gw.example.test"),
])
def test_url_forms_yield_bare_host(ping_mod, monkeypatch, gw_url, host):
    assert _connect_target(ping_mod, monkeypatch, gw_url) == (host, 25)


# ── red anchor: the OLD expression is really broken (mutation, not decoration) ──

def test_red_anchor_old_parsing_yields_host_with_port(tmp_path):
    """旧写法 ⇒ connect() 收到 "127.0.0.1:34401" ⇒ 正是实测的 gaierror 输入。

    这不是复述：它读当前文件、把 host 解析回退成旧表达式、真的跑同一条路径。
    """
    src = PING_TEST.read_text(encoding="utf-8")
    assert _FIXED in src, "红锚失效：当前文件不再含被替换的修法片段（先对齐本测试）"
    mutant = tmp_path / "ping_test_mutant.py"
    mutant.write_text(src.replace(_FIXED, _OLD), encoding="utf-8")
    mod = _load("ping_test_mutant", mutant)

    class _M(_RecordingSocket):
        pass

    # monkeypatch 不在这里（非 fixture 测试）⇒ 手工换回，避免污染全局 socket 模块。
    real = socket.socket
    socket.socket = _M
    try:
        with pytest.raises(_Stop) as ei:
            mod._smtp_send_ping("http://127.0.0.1:34401", "ab" * 32,
                                "agent@x.test", "mgr@y.test", "pid", "base")
    finally:
        socket.socket = real
    assert ei.value.addr == ("127.0.0.1:34401", 25), (
        "旧写法应当把带端口的串当 HOST —— 若这条断言不再成立，说明红锚已失效")
