"""文档 ↔ 实现一致性门禁的 pytest 包装(L0 里另有一处直调)。

门禁本体 = ``tests/contract/check-docs-consistency.py``(退出码 0=通过 / 1=违约 /
2=判不了)。这里把它跑成 RED-GREEN 三态, 免得"文档承诺了、实现没兜住"再漂回去:

* 默认两份自举文档 ⇒ rc=0;
* 故意写错一个反引号符号名 ⇒ rc=1 且带 ``file:line``;
* 把契约值写错一个字符 ⇒ rc=1(逐字命中单一真源那套);
* 文档读不到 ⇒ rc=2(判不了, 绝不假装通过)。
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "tests" / "contract" / "check-docs-consistency.py"
MANIFEST = ROOT / "contract" / "aimail-contract.json"
DOC_EN = ROOT / "docs" / "agent-self-setup.md"
DOC_ZH = ROOT / "docs" / "agent-self-setup_zh.md"


def _run(*args: str) -> tuple:
    p = subprocess.run([sys.executable, str(SCRIPT), *args],
                       cwd=str(ROOT), capture_output=True, text=True)
    return p.returncode, p.stdout + p.stderr


def test_docs_are_consistent():
    rc, out = _run()
    assert rc == 0, out
    assert "PASS" in out, out


def test_bad_symbol_name_is_red_with_file_line(tmp_path):
    text = DOC_EN.read_text(encoding="utf-8")
    assert "`start_polling`" in text, "文档里应有 start_polling 的反引号引用"
    bad = tmp_path / "bad-doc.md"
    bad.write_text(text.replace("`start_polling`", "`start_polling_typo`"),
                   encoding="utf-8")
    rc, out = _run("--en", str(bad))
    assert rc == 1, out
    assert "start_polling_typo" in out, out
    assert "bad-doc.md:" in out, out  # file:line 必须给


def test_wrong_contract_value_is_red(tmp_path):
    man = json.loads(MANIFEST.read_text(encoding="utf-8"))
    text = DOC_EN.read_text(encoding="utf-8")
    assert man["inbound_path"] in text, "前置: 文档里本就引用了真源路径"
    # 把路径改坏到"真源值再也出现不了"(前缀式子串也算命中, 所以要整体换掉)
    bad_text = text.replace(man["inbound_path"], man["inbound_path"].replace("/", "_"))
    assert man["inbound_path"] not in bad_text, "前置: 坏文档里确已无真源值"
    bad = tmp_path / "bad-doc.md"
    bad.write_text(bad_text, encoding="utf-8")
    rc, out = _run("--en", str(bad))
    assert rc == 1, out
    assert man["inbound_path"] in out, out  # 报告里点名缺哪个真源值


def test_missing_doc_cannot_judge():
    rc, out = _run("--en", str(ROOT / "docs" / "no-such-self-setup-doc.md"))
    assert rc == 2, out
    assert "CANNOT JUDGE" in out, out


def test_zh_doc_exists_and_is_covered():
    assert DOC_ZH.is_file(), "中文对必须存在(双语成对维护)"
    text = DOC_ZH.read_text(encoding="utf-8")
    assert text.strip(), "中文对不能是空文件"
