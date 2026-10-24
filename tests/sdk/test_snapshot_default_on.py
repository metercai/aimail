"""The default value of the snapshot switch must be **on**(2026-10-02 D1, second half of J4f root cause).

Background(verified): J4e is green(deerflow really replied, outbound line in agentmail.log) but J4f is still red
`persona='' signature=''` —— because `aimail welcome` section 3 draft parsing only reads
`mail/<yyyymm>/out-*.json`(`cli/send_welcome.py:344-352`), and snapshot never wrote:
`pysdk/aimail_tools.py` used to use `config.get("save_raw_snapshots")`(**missing key means off**),
 The config at this step is the **binding file (contract BINDING_FILE)** injected by `set_agent_context`, and the binding has never had this key.

Three-way comparison(must be same standard):
  * TS        `mail-core/src/tools.ts:329`  `cfg.save_raw_snapshots !== false` ⇒ missing key=on
  * hermes    `pysdk/hermes/aimail_hermes.py:579` `config.get("save_raw_snapshots", True)`
  * Docs      `cli/README.md:128` "keep a raw snapshot of every mail (default true)"
  * Python    was the only "missing key means off" implementation ⇒ this file pins: missing key=on, explicit false turns off.

Covers two disk-writing paths (outbound/inbound) and two directions of the switch.
"""
import glob
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, "pysdk")  # ensure repo pysdk wins over any cli/ shadow

import aimail_base as base  # noqa: E402
import aimail_tools as tools  # noqa: E402
from aimail_contract import BINDING_FILE, INBOUND_PATH  # noqa: E402 — contract single source: no literals

SID = "sys-snap"
EMAIL = "agent@snap.test"
ADDR = "agent_snap.test"   # = _clean_agent_dir_name(EMAIL) — directory name and address derive from the same source


@pytest.fixture(autouse=True)
def _isolated_home(tmp_path, monkeypatch):
    """AIMAIL_HOME / identity env points to tmp, and module-level context is reset after use."""
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
    """Same isolated home (created by autouse fixture and responsible for reset)."""
    return _isolated_home


def _binding(home: Path, **extra) -> dict:
    cfg = {"email": EMAIL, "system_id": SID, "domain": "snap.test",
           "gateway_url": "http://127.0.0.1:34401", "api_key": "k" * 64,
           "webhook_url": f"http://127.0.0.1:8001{INBOUND_PATH}",
           "webhook_secret": "s" * 64, "agent_id": "default"}
    cfg.update(extra)
    (home / "systems" / SID / ADDR / BINDING_FILE).write_text(json.dumps(cfg))
    return cfg


def _snapshots(home: Path, prefix: str):
    return sorted(glob.glob(str(home / "systems" / SID / ADDR / "mail" / "*" / f"{prefix}-*.json")))


def _use(cfg: dict, monkeypatch):
    """Simulate the binding-type config injected by set_agent_context (no save_raw_snapshots key)."""
    monkeypatch.setattr(tools, "_load_profile_config", lambda: dict(cfg))


def _fake_gateway(monkeypatch):
    class _Client:
        def __init__(self, *a, **k):
            pass

        def send_mail(self, **kw):
            return {"status": 200, "email_id": "<m1@gw.test>"}

    monkeypatch.setattr(tools, "_GatewayClient", _Client)


# ── Outbound: missing key ⇒ still write out-*.json (the sole input for J4f draft parsing) ──────────────────
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


# ── Outbound: explicit false ⇒ still do not write (the capability to turn off must not be weakened by D1) ────────────────────
def test_outbound_snapshot_still_honours_explicit_false(home, monkeypatch):
    cfg = _binding(home, save_raw_snapshots=False)
    _use(cfg, monkeypatch)
    _fake_gateway(monkeypatch)

    tools.send_mail(to="m@gw.test", subject="s", body="b")

    assert _snapshots(home, "out") == [], "explicit false must still disable snapshots"


# ── Inbound: missing key ⇒ still write in-*.json (same convention as outbound) ────────────────────────────
def test_inbound_snapshot_written_when_the_key_is_absent(home, monkeypatch):
    cfg = _binding(home)
    _use(cfg, monkeypatch)

    path = tools.store_inbound_message(
        "mid-in-1", [], EMAIL,
        preprocessed_payload={"subject": "Welcome", "body": "b", "sender": "m@gw.test"})
    assert path, "inbound snapshot missing under the default-on rule"
    assert Path(path).is_file() and Path(path).name.startswith("in-")


# ── Inbound: explicit false ⇒ do not write ────────────────────────────────────────────────
def test_inbound_snapshot_still_honours_explicit_false(home, monkeypatch):
    cfg = _binding(home, save_raw_snapshots=False)
    _use(cfg, monkeypatch)

    path = tools.store_inbound_message(
        "mid-in-2", [], EMAIL,
        preprocessed_payload={"subject": "Welcome", "body": "b", "sender": "m@gw.test"})
    assert path is None
    assert _snapshots(home, "in") == []
