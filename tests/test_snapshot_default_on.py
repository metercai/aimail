"""快照开关的默认值必须是**开**(2026-10-02 D1, J4f 根因的第二半)。

背景(实测): J4e 已绿(deerflow 真回信, outbound 行在 agentmail.log 里)但 J4f 仍红
`persona='' signature=''` —— 因为 `aimail welcome` 第 3 段的草案解析只读
`mail/<yyyymm>/out-*.json`(`cli/send_welcome.py:344-352`), 而快照根本没写:
`pysdk/aimail_tools.py` 曾用 `config.get("save_raw_snapshots")`(**缺键即关**),
这一步的 config 是 `set_agent_context` 注入的 **agentmail.json 绑定**, 而绑定从来没有这个键。

三方对照(必须同口径):
  * TS        `mail-core/src/tools.ts:329`  `cfg.save_raw_snapshots !== false` ⇒ 缺键=开
  * hermes    `pysdk/hermes/aimail_hermes.py:579` `config.get("save_raw_snapshots", True)`
  * 文档      `cli/README.md:128` "keep a raw snapshot of every mail (default true)"
  * Python    曾是唯一"缺键即关"的实现 ⇒ 本文件钉死:缺键=开, 显式 false 才关。

覆盖两条写盘路径(出站/入站)与开关的两个方向。
"""
import glob
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
import aimail_tools as tools  # noqa: E402
from aimail_contract import BINDING_FILE  # noqa: E402

SID = "sys-snap"
EMAIL = "agent@snap.test"
ADDR = "agent_snap.test"   # = _clean_agent_dir_name(EMAIL) — 目录名与地址派生同源


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """AIMAIL_HOME / 身份 env 指进 tmp, 且模块级上下文在用完后复位。"""
    home = tmp_path / "aimail-home"
    bind_dir = home / "systems" / SID / ADDR
    bind_dir.mkdir(parents=True)
    monkeypatch.setenv("AIMAIL_HOME", str(home))
    monkeypatch.setenv("AIMAIL_AGENT_EMAIL", EMAIL)
    monkeypatch.setenv("AIMAIL_SYSTEM_ID", SID)
    saved_cfg = base._ACTIVE_AGENT_CONFIG
    saved_loader = base._CONFIG_LOADER
    yield home
    base._ACTIVE_AGENT_CONFIG = saved_cfg
    base._CONFIG_LOADER = saved_loader
    for k in [k for k in list(__import__("os").environ) if k.startswith("AIMAIL_")]:
        __import__("os").environ.pop(k, None)


@pytest.fixture
def home(_isolated_home):
    """同一份隔离 home(由 autouse fixture 建好并负责复位)。"""
    return _isolated_home


def _binding(home: Path, **extra) -> dict:
    cfg = {"email": EMAIL, "system_id": SID, "domain": "snap.test",
           "gateway_url": "http://127.0.0.1:34401", "api_key": "k" * 64,
           "webhook_url": "http://127.0.0.1:8001/aimail/inbound",
           "webhook_secret": "s" * 64, "agent_id": "default"}
    cfg.update(extra)
    (home / "systems" / SID / ADDR / BINDING_FILE).write_text(json.dumps(cfg))
    return cfg


def _snapshots(home: Path, prefix: str):
    return sorted(glob.glob(str(home / "systems" / SID / ADDR / "mail" / "*" / f"{prefix}-*.json")))


def _use(cfg: dict, monkeypatch):
    """模拟 set_agent_context 注入的绑定型 config(没有 save_raw_snapshots 键)。"""
    monkeypatch.setattr(tools, "_load_profile_config", lambda: dict(cfg))


def _fake_gateway(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def send_mail(self, **kw):
            return {"status": 200, "email_id": "<m1@gw.test>"}

    monkeypatch.setattr(tools, "_GatewayClient", _Client)


# ── 出站: 缺键 ⇒ 仍写 out-*.json(J4f 草案解析的唯一输入) ──────────────────
def test_outbound_snapshot_written_when_the_key_is_absent(home, monkeypatch):
    cfg = _binding(home)
    _use(cfg, monkeypatch)
    _fake_gateway(monkeypatch)

    out = tools.send_mail(to="m@gw.test", subject="Re: Welcome to AIMail World, agent",
                          body="persona: p\nsignature: s\ncurrent_time: now")
    assert out.get("success") is True, out

    snaps = _snapshots(home, "out")
    assert snaps, ("outbound snapshot missing — this is exactly what starves "
                   "send_welcome._parse_draft_from_reply (J4f red)")
    snap = json.loads(Path(snaps[-1]).read_text())
    assert snap.get("direction") == "outbound"
    assert "persona: p" in (snap.get("body") or "")


# ── 出站: 显式 false ⇒ 仍然不写(关的能力不许被 D1 削弱) ────────────────────
def test_outbound_snapshot_still_honours_explicit_false(home, monkeypatch):
    cfg = _binding(home, save_raw_snapshots=False)
    _use(cfg, monkeypatch)
    _fake_gateway(monkeypatch)

    tools.send_mail(to="m@gw.test", subject="s", body="b")

    assert _snapshots(home, "out") == [], "explicit false must still disable snapshots"


# ── 入站: 缺键 ⇒ 仍写 in-*.json(与出站同一口径) ────────────────────────────
def test_inbound_snapshot_written_when_the_key_is_absent(home, monkeypatch):
    cfg = _binding(home)
    _use(cfg, monkeypatch)

    path = tools.store_inbound_message(
        "mid-in-1", [], EMAIL,
        preprocessed_payload={"subject": "Welcome", "body": "b", "sender": "m@gw.test"})
    assert path, "inbound snapshot missing under the default-on rule"
    assert Path(path).is_file() and Path(path).name.startswith("in-")


# ── 入站: 显式 false ⇒ 不写 ────────────────────────────────────────────────
def test_inbound_snapshot_still_honours_explicit_false(home, monkeypatch):
    cfg = _binding(home, save_raw_snapshots=False)
    _use(cfg, monkeypatch)

    path = tools.store_inbound_message(
        "mid-in-2", [], EMAIL,
        preprocessed_payload={"subject": "Welcome", "body": "b", "sender": "m@gw.test"})
    assert path is None
    assert _snapshots(home, "in") == []
