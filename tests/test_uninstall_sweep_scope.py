"""N2-A: uninstall whitelist sweep must only ever touch THIS system's rows.

Repro evidence: /tmp/d2-repro.log (data-layer replay of gateway SQL) showed the
old sweep keyed by the bare `cfg_domain` resolves to the DOMAIN OWNER on shared
domains (system_domains.domain_addr UNIQUE), so a shared tenant's uninstall
deleted the platform's rows while leaking its own. Owner ruling 2026-10-02:
A fix first (CLI-side query keys = system-owned rows + system_id filter),
B (SDK 收口) deferred to the next SDK batch.
"""
import importlib.util
import sys
from pathlib import Path

_CLI = Path(__file__).resolve().parent.parent / "cli" / "aimail"
assert _CLI.is_file()
if str(_CLI.parent) not in sys.path:
    sys.path.insert(0, str(_CLI.parent))
if str(_CLI.parent.parent / "pysdk") not in sys.path:
    sys.path.insert(0, str(_CLI.parent.parent / "pysdk"))

import aimail_base  # noqa: E402 — N2-B: the sweep lives in the SDK now
from importlib.machinery import SourceFileLoader


def _load(path, name):
    """cli/*.py 无扩展名者(cli/aimail)按文件路径加载(照 test_install_plugin_ensure)。"""
    loader = SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


cli = _load(_CLI, "cli_n2_sweep")


class FakeClient:
    """回解析语义: 地址键 → 本系统行; 裸域键(共享) → 域主行 —— 模拟网关工厂。"""
    def __init__(self, key_rows):
        self.key_rows = key_rows          # {key: [row, ...]}
        self.queried = []
        self.deleted = []

    def list_whitelists_by_domain(self, key):
        self.queried.append(key)
        if key not in self.key_rows:
            raise RuntimeError(f"404 domain not found: {key}")
        return list(self.key_rows[key])

    def delete_whitelist_entry_by_id(self, rid):
        self.deleted.append(int(rid))


def test_shared_tenant_never_queries_bare_domain_and_deletes_only_own_rows():
    sid = "tenant1"
    own = [{"id": 1, "system_id": sid, "domain_addr": "agent.t1@shared.tm"},
           {"id": 2, "system_id": sid, "domain_addr": "mgr.t1@shared.tm"}]
    foreign = {"id": 9, "system_id": "platform-sys", "domain_addr": "shared.tm"}
    # 地址键回解析 = 本系统行; 额外混入一条域主行, 模拟解析异常仍须被 system_id 滤掉
    c = FakeClient({"agent.t1@shared.tm": own + [foreign],
                    "mgr.t1@shared.tm": own + [foreign],
                    # 若有人把裸域当键, 网关会返回域主的行 —— 键集里根本不该出现它
                    "shared.tm": [foreign]})
    res = aimail_base.cleanup_system_whitelists(c, sid, addresses=["agent.t1@shared.tm"],
                                                domains=[], deregistered=set())
    assert "shared.tm" not in c.queried, f"bare domain queried: {c.queried}"
    assert sorted(c.deleted) == [1, 2], f"deleted={c.deleted} (foreign row touched or own leaked)"
    assert res["removed"] == 2


def test_non_shared_own_domain_still_sweeps():
    sid = "solo"
    rows = [{"id": 3, "system_id": sid, "domain_addr": "own-a.test"},
            {"id": 4, "system_id": sid, "domain_addr": "a@own-a.test"}]
    c = FakeClient({"own-a.test": rows, "a@own-a.test": rows})
    res = aimail_base.cleanup_system_whitelists(c, sid, addresses=["a@own-a.test"],
                                                domains=["own-a.test"], deregistered=set())
    assert sorted(c.deleted) == [3, 4]
    assert res["removed"] == 2


def test_deregistered_address_key_is_skipped():
    sid = "solo"
    rows = [{"id": 5, "system_id": sid, "domain_addr": "own-a.test"}]
    c = FakeClient({"own-a.test": rows, "orphan@own-a.test": rows})
    aimail_base.cleanup_system_whitelists(c, sid, addresses=["orphan@own-a.test"],
                                          domains=["own-a.test"],
                                          deregistered={"orphan@own-a.test"})
    assert "orphan@own-a.test" not in c.queried


def test_no_system_owned_rows_means_no_sweep_at_all():
    # 判不出归属宁可不扫 —— 旧实现此时会拿 cfg_domain 裸键兜底(共享域病灶)
    c = FakeClient({"shared.tm": [{"id": 9, "system_id": "platform-sys",
                                   "domain_addr": "shared.tm"}]})
    res = aimail_base.cleanup_system_whitelists(c, "tenant1", addresses=[], domains=[],
                                                deregistered=set())
    assert res["removed"] == 0 and c.queried == [] and c.deleted == []


def test_duplicate_rows_across_keys_deleted_once():
    sid = "solo"
    rows = [{"id": 6, "system_id": sid, "domain_addr": "x@own-a.test"}]
    c = FakeClient({"own-a.test": rows, "x@own-a.test": rows})
    res = aimail_base.cleanup_system_whitelists(c, sid, addresses=["x@own-a.test"],
                                                domains=["own-a.test"], deregistered=set())
    assert c.deleted == [6] and res["removed"] == 1

def test_cli_uninstall_is_a_thin_caller_no_local_sweep_logic():
    """N2-B wiring: the CLI triggers the SDK entry and only reports; the sweep
    logic (keys/filter/delete) must NOT exist in the CLI module anymore."""
    src = _CLI.read_text(encoding="utf-8")
    assert "cleanup_system_whitelists(" in src, "CLI does not call the SDK entry"
    assert "_gateway_sweep_whitelists" not in src, "local sweep helper resurrected"
