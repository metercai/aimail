"""pysdk 侧的绑定写入口单测(owner 裁决 A, 2026-09-28)。

分层裁决: 系统级环境文件由 CLI 写, per-agent 绑定文件由 SDK 写; CLI 需要改绑定
内容时不许自持写调用 ⇒ 它只调 pysdk 的三个语义化薄函数
(``update_binding`` / ``backfill_binding`` / ``rename_binding``), 三者内部一律走既有
的 ``save_agent_config``(原子 tmp+rename + 0600 语义不变)。

本测试盯住的正是这三个薄函数的**语义**, 不是实现:
  1. ``update_binding`` 字段级合并 + 落盘(其它字段逐字保留; 文件 0600)
  2. ``backfill_binding`` 整份落盘(repair 回填/对齐路径)
  3. ``rename_binding`` = 「搬目录 + 写内容」同一件事(附件随目录一起走;
     目标目录已存在 ⇒ merged=True 且**不动**源目录)
  4. 三者落盘格式与 ``save_agent_config`` 同款(indent=2 + 结尾换行)

文件名走契约常量(``aimail_contract.BINDING_FILE``), 不写字面量 —— 契约字面量棘轮
按「文件 × 键」只许减不许增。
"""
import json
import stat
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO / "pysdk"))

import aimail_base as ab  # noqa: E402
from aimail_contract import BINDING_FILE  # noqa: E402

SID = "system-test0001"


@pytest.fixture()
def home(tmp_path, monkeypatch):
    """隔离的 aimail home(aimail_home() 每次读 env ⇒ 可重定向)。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path))
    return tmp_path


def _binding_path(home: Path, email: str) -> Path:
    return home / "systems" / SID / ab._clean_agent_dir_name(email) / BINDING_FILE


def _seed(home: Path, email: str, **extra) -> dict:
    cfg = {"email": email, "system_id": SID, "gateway_url": "http://gw:38080",
           "api_key": "k-secret", "agent_id": "agent"}
    cfg.update(extra)
    ab.save_agent_config(cfg["agent_id"], cfg, SID)
    return cfg


def _mode(p: Path) -> int:
    return stat.S_IMODE(p.stat().st_mode)


def test_update_binding_merges_fields_and_persists(home):
    email = "alice@example.test"
    _seed(home, email, manager_address="")
    p = _binding_path(home, email)
    before = json.loads(p.read_text())

    out = ab.update_binding(SID, before, {"manager_address": "mgr@example.test"})

    assert out == p, "落盘路径必须仍是该地址的绑定文件"
    after = json.loads(p.read_text())
    assert after["manager_address"] == "mgr@example.test"
    # 其余字段逐字保留 + agent_id 未丢
    for k, v in before.items():
        if k != "manager_address":
            assert after[k] == v, f"{k} 被 update_binding 改动了"
    assert after["agent_id"] == "agent"
    assert _mode(p) == 0o600
    assert p.read_text().endswith("\n") and '"manager_address"' in p.read_text()


def test_update_binding_does_not_mutate_caller_dict(home):
    email = "bob@example.test"
    cfg = _seed(home, email)
    snapshot = json.loads(json.dumps(cfg))

    ab.update_binding(SID, cfg, {"manager_address": "m2@example.test"})

    assert cfg == snapshot, "薄函数不得就地改调用方传入的 dict"


def test_backfill_binding_writes_whole_content(home):
    email = "carol@example.test"
    p = _binding_path(home, email)

    ab.backfill_binding({"email": email, "system_id": SID, "agent_id": "a2",
                         "gateway_url": "http://gw2:38080", "domain": "example.test"}, SID)

    d = json.loads(p.read_text())
    assert d == {"email": email, "system_id": SID, "agent_id": "a2",
                 "gateway_url": "http://gw2:38080", "domain": "example.test"}
    assert _mode(p) == 0o600


def test_rename_binding_moves_dir_and_rewrites_content(home):
    old, new = "dave@example.test", "dave2@example.test"
    cfg = _seed(home, old, webhook_url="http://127.0.0.1:8799/hook")
    old_dir = _binding_path(home, old).parent
    # 目录里的**兄弟文件**也必须跟着走(证明是搬目录, 不是只改 json)
    (old_dir / "role_prompt").mkdir()
    (old_dir / "role_prompt" / "10_x.md").write_text("# x\n", encoding="utf-8")

    res = ab.rename_binding(old_dir, new, {**cfg, "email": new}, SID)

    new_dir = _binding_path(home, new).parent
    assert res["moved"] is True and res["merged"] is False
    assert res["dir"] == new_dir and res["path"] == new_dir / BINDING_FILE
    assert not old_dir.exists(), "旧目录必须已被搬走"
    assert (new_dir / "role_prompt" / "10_x.md").is_file(), "兄弟文件未随目录搬迁"
    d = json.loads((new_dir / BINDING_FILE).read_text())
    assert d["email"] == new and d["webhook_url"] == "http://127.0.0.1:8799/hook"
    assert d["agent_id"] == "agent"
    assert _mode(new_dir / BINDING_FILE) == 0o600


def test_rename_binding_merges_when_target_exists(home):
    old, new = "erin@example.test", "erin2@example.test"
    cfg = _seed(home, old)
    old_dir = _binding_path(home, old).parent
    new_dir = _binding_path(home, new).parent
    new_dir.mkdir(parents=True)          # 目标目录先存在(旧代码行为: 保留两个目录)
    (new_dir / "keep.md").write_text("keep\n", encoding="utf-8")

    res = ab.rename_binding(old_dir, new, {**cfg, "email": new}, SID)

    assert res["moved"] is False and res["merged"] is True
    assert old_dir.is_dir(), "目标已存在时不得动源目录"
    assert (new_dir / "keep.md").is_file(), "合并不得清掉目标目录里的既有文件"
    assert json.loads((new_dir / BINDING_FILE).read_text())["email"] == new
