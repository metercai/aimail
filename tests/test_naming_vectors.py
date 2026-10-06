"""定名规则 conformance：py 侧执行共享向量表 tests/naming-vectors.json。

该表是**单一真源**：py（aimail_base.email_for_agent）与 node（mail-core emailForAgent）
两侧各自执行同一张表；任一向量在任一侧失败 ⇒ 该侧规则实现不正确（判红）。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "pysdk"))
import aimail_base  # noqa: E402

_VEC = json.loads(
    (Path(__file__).resolve().parent / "naming-vectors.json").read_text(encoding="utf-8")
)["vectors"]


def test_naming_vectors_py():
    bad = []
    for v in _VEC:
        i = v["in"]
        got = aimail_base.email_for_agent(i["agent_id"], i["domain"], i.get("system_name", ""))
        if got != v["out"]:
            bad.append({"in": i, "want": v["out"], "got": got, "why": v.get("why", "")})
    assert not bad, f"定名向量不合规: {bad}"
