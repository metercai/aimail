"""桥路由目标规范化回归（2026-09-21 生产实测缺陷）。

缺陷: `aimail bridge --system-id`(repair 计划里的"路由重刷"步)把**路由表里已渲染的**
`host:port` 当作 admin API 的 `host` 回传、`port` 硬填 80 ⇒ 桥走
`ProfileRoute::new`(非 from_url), 套用默认路径 `/webhooks/aimail-inbound` 并把 host 当
字面量 ⇒ 生成 `http://127.0.0.1:9101:80/webhooks/aimail-inbound`(非法 URL + 错路径),
文件里落成 `"127.0.0.1:9101:80"`, **投递静默断**(而 CLI 仍打印 "unchanged")。
修: 优先 agentmail.json 的 webhook_url(全 URL 权威源) + `_target_to_route_fields`
保证 host 传完整 URL(桥走 from_url, 自定义路径 /aimail/inbound 不丢)。
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
    loader = SourceFileLoader("aimail_cli_route_under_test", str(_CLI))
    mod = importlib.util.module_from_spec(
        importlib.util.spec_from_loader("aimail_cli_route_under_test", loader))
    loader.exec_module(mod)
    return mod


CLI = _load_cli()


def test_full_url_is_passed_verbatim_with_placeholder_port():
    # 平台真实路径必须原样进 host(桥按 from_url 保真解析), port 仅占位
    assert CLI._target_to_route_fields("http://127.0.0.1:9101/aimail/inbound") == (
        "http://127.0.0.1:9101/aimail/inbound", 80)
    assert CLI._target_to_route_fields("https://agent.example.com/hook") == (
        "https://agent.example.com/hook", 80)


def test_bare_host_port_is_split_into_two_fields():
    # 兜底路径: 裸 host:port 拆成两字段, 由桥按默认路径拼接(不再叠成三段)
    assert CLI._target_to_route_fields("127.0.0.1:9101") == ("127.0.0.1", 9101)
    assert CLI._target_to_route_fields("10.0.0.5:8646") == ("10.0.0.5", 8646)


def test_garbage_target_does_not_crash():
    assert CLI._target_to_route_fields("") == ("", 80)
    assert CLI._target_to_route_fields("host-without-port") == ("host-without-port", 80)


def test_system_agents_prefers_agentmail_webhook_url(tmp_path, monkeypatch):
    """接收端 URL 以 agentmail.json 的 webhook_url 为准(全 URL 唯一信任源)。"""
    sid = "sys-route-test"
    sysdir = tmp_path / "systems" / sid
    addr = "pi@example.com".replace("@", "_")  # _addr_clean: 非 [\w.-] → _
    (sysdir / addr).mkdir(parents=True)
    (sysdir / addr / "agentmail.json").write_text(
        '{"email": "pi@example.com", "system_id": "%s",'
        ' "webhook_url": "http://127.0.0.1:9101/aimail/inbound"}' % sid)
    monkeypatch.setattr(CLI, "SYSTEMS_DIR", tmp_path / "systems")
    monkeypatch.setattr(CLI, "_read_routes", lambda: {"pi@example.com": "127.0.0.1:9101:80"})
    agents = dict(CLI._system_agents(sid))
    assert agents["pi@example.com"] == "http://127.0.0.1:9101/aimail/inbound"
