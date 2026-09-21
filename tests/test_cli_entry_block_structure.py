"""CLI 入口块结构护栏（2026-09-21 生产实测发现）。

`aimail ping` / `aimail welcome` 曾在运行时直接 NameError：
  cli/ping_test.py 与 cli/send_welcome.py 把模块级 `_detect_edition` **定义在**
  `if __name__ == "__main__": sys.exit(main())` **之后** —— 模块级代码顺序执行,
  入口块先跑 main(), 此时该函数尚未绑定 ⇒ 命令必崩。

本测试对 cli/*.py 做结构检查(比逐个命令跑一遍更省、也覆盖未来新增脚本):
  任何 `def`/`class` 都不允许出现在入口块之后(即入口块必须靠近文件末尾,
  且其前没有未执行的顶层定义)。

注意: 允许入口块之后存在**非定义**的其他语句(例如第二个 if/main 兜底), 但
出现 def/class 就说明"定义被入口块越过"。
"""
import re
from pathlib import Path

CLI_DIR = Path(__file__).resolve().parent.parent / "cli"
ENTRY_RE = re.compile(r"^if\s+__name__\s*==\s*['\"]__main__['\"]\s*:", re.M)
DEF_RE = re.compile(r"^(?:async\s+)?(?:def|class)\s+\w+", re.M)


def _cli_scripts():
    return sorted(p for p in CLI_DIR.glob("*.py"))


def test_all_defs_precede_main_entry_block():
    offenders = []
    for p in _cli_scripts():
        src = p.read_text(encoding="utf-8")
        m = ENTRY_RE.search(src)
        if not m:
            continue
        after = src[m.end():]
        tail_defs = DEF_RE.findall(after)
        if tail_defs:
            offenders.append(f"{p.name}: {tail_defs}")
    assert not offenders, (
        "入口块之后仍有定义(运行到 main() 时未绑定 ⇒ 必 NameError): " + "; ".join(offenders)
    )


def test_ping_and_welcome_edition_helper_is_defined_before_entry():
    for name in ("ping_test.py", "send_welcome.py"):
        src = (CLI_DIR / name).read_text(encoding="utf-8")
        m = ENTRY_RE.search(src)
        head = src[:m.start()] if m else src
        assert re.search(r"^def\s+_detect_edition\b", head, re.M), (
            f"{name}: _detect_edition 必须在入口块之前定义"
        )
