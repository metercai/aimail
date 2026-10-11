"""SDK 语言边界契约(owner 裁决 2026-10-11): pysdk 与 tssdk 相互独立 ——
``pysdk/`` 里不得有 nodejs 代码, ``tssdk/`` 里不得有 python 代码。

为什么码层固化: "独立"靠人记会漂。两边各自被自己的语言工具链消费(pip wheel / npm
tarball), 掺入对侧语言会引出对侧工具链依赖(装 node 才能跑 pysdk?装 python 才能
发 tssdk?), 跨机复现时先坏在这里。仓根 ``scripts/`` 是语言中立的仓级工具区, 不受此限。

本测试只做文件树断言(不做产物断言: 产物层指纹在 L2 check-tarball / npm pack 各自守)。
"""
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]

# 允许的例外(生成物/第三方): 只跳过 node_modules 与编译产物目录。
SKIP_DIRS = {"node_modules", "__pycache__", ".git", "lib", "dist", "target"}


def _files_under(root: Path, suffixes: tuple[str, ...] | set[str]) -> list[str]:
    out = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts[:-1]):
            continue
        if p.suffix in suffixes:
            out.append(str(p.relative_to(REPO)))
    return sorted(out)


def test_no_node_code_in_pysdk():
    node_ext = {".js", ".mjs", ".cjs", ".ts", ".tsx"}
    hits = _files_under(REPO / "pysdk", node_ext)
    hits += [str(p.relative_to(REPO)) for p in (REPO / "pysdk").rglob("package.json")
             if not any(part in SKIP_DIRS for part in p.relative_to(REPO / "pysdk").parts[:-1])]
    assert not hits, (
        "pysdk/ 里出现 nodejs 代码(应移出到 tssdk/ 或仓根 scripts/):\n  "
        + "\n  ".join(sorted(hits)))


def test_no_python_code_in_tssdk():
    hits = _files_under(REPO / "tssdk", {".py"})
    assert not hits, (
        "tssdk/ 里出现 python 代码(应移出到 pysdk/ 或仓根 scripts/):\n  "
        + "\n  ".join(hits))
