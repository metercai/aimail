"""CLI 奇偶校验骨架（plan v3 S0；rust 化验收面 (b)(c) 的本地形态）。

做法：同一个命令分别跑 **Python CLI** 与 **Rust CLI**，对 stdout/stderr/rc 逐行
diff，只允许白名单差异（时间戳 / PID / 夹具根路径前缀）—— 不允许"大概一样"。

S0 阶段 Rust 侧只有 `version` 已实现，所以对 Rust 的断言**只覆盖已实现面**
（`version` 逐字 + 子命令集合相等 + rc 2 家族），其余命令随 S3–S9 落地后加入
`RUST_PARITY_COMMANDS`。**不预先声明未实现命令的等价**（那是假绿）。

夹具：四种程序根组合（空 / 单系统 / 多系统 / 断链系统），全部在 tmp_path 下，
`HOME` 与 `AIMAIL_HOME` 都重定向进夹具 —— 不碰真机 ~/.aimail 与平台目录。
"""

from __future__ import annotations

import base64
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
PY_CLI = [sys.executable, str(REPO / "cli" / "aimail")]

# 契约字面量**取自单真源**（pysdk 常量模块），测试里不复制字面量 ——
# 这也是 tests/contract/check-contract-single-source.py 的字面量棘轮要求的形态
# （新增位置必须改用常量模块）。
sys.path.insert(0, str(REPO / "pysdk"))
from aimail_contract import BINDING_FILE  # noqa: E402

#: 命令面冻结清单（边界定稿 §1：15 顶层 + prompt 5 嵌套）。Python 与 Rust 两侧都必须相等。
FROZEN_TOP_LEVEL = (
    "install", "uninstall", "reset", "stats", "renew", "version", "check", "repair", "ping",
    "welcome", "persona", "domain", "address", "prompt", "bridge",
)
FROZEN_PROMPT_SUBCOMMANDS = ("add", "list", "rm", "test", "create-file")

#: Rust 侧已实现、可做逐字等价比对的**调用**（含参数；随 S3–S9 增长）。
RUST_PARITY_INVOCATIONS = (
    ("version",),
    ("stats",),
    ("stats", "-a"),
    ("persona",),
)

#: Python 自比命令集（只读、无网络、夹具内确定性）。
PY_SELF_PARITY_COMMANDS = ("version", "stats", "--help")

COMBOS = ("empty", "single", "multi", "broken")


def rust_bin() -> Path | None:
    """Rust 二进制：`AIMAIL_RUST_BIN` 优先，其次 cli/target/{debug,release}/aimail。"""
    env = os.environ.get("AIMAIL_RUST_BIN", "").strip()
    if env:
        p = Path(env).expanduser()
        return p if p.is_file() else None
    for profile in ("debug", "release"):
        p = REPO / "cli" / "target" / profile / "aimail"
        if p.is_file():
            return p
    return None


# ── 夹具 ───────────────────────────────────────────────────────────────────
def _b64key() -> str:
    return base64.b64encode(b"0" * 32).decode()


def _write_agent(sid_dir: Path, email: str, sid: str) -> None:
    cleaned = re.sub(r"[^\w.-]", "_", email)
    addr_dir = sid_dir / cleaned
    addr_dir.mkdir(parents=True, exist_ok=True)
    (addr_dir / BINDING_FILE).write_text(json.dumps({
        "email": email, "gateway_url": "http://127.0.0.1:38999", "domain": "example.test",
        "system_id": sid, "system_name": sid, "manager_address": "manager@example.test",
        "api_key": f"{sid}.system.key", "webhook_url": "", "webhook_secret": "s" * 43,
    }, indent=1), encoding="utf-8")
    month = addr_dir / "mail" / "202609"
    month.mkdir(parents=True, exist_ok=True)
    (month / "in-1.json").write_text(json.dumps({"subject": "x", "body": "y"}), encoding="utf-8")


def _write_system(aimail_home: Path, sid: str, *, system_home: Path) -> None:
    sd = aimail_home / "systems" / sid
    sd.mkdir(parents=True, exist_ok=True)
    (sd / "aimail_gateway.json").write_text(json.dumps({
        "gateway_url": "http://127.0.0.1:38999", "admin_key": _b64key(), "system_id": sid,
        "system_name": sid, "system_home": str(system_home), "save_raw_snapshots": True,
    }, indent=1), encoding="utf-8")
    _write_agent(sd, f"agent@{sid}.example.test", sid)


def make_fixture(root: Path, combo: str) -> dict:
    """建一个 hermetic 程序根组合，返回 {home, aimail_home, platform_root}。"""
    home = root / "home"
    aimail_home = root / "aimail-home"
    platform_root = home / ".hermes"
    (home / ".aimail").mkdir(parents=True, exist_ok=True)
    (home / ".aimail" / ".env").write_text("", encoding="utf-8")   # 无机器级 env 泄漏
    aimail_home.mkdir(parents=True, exist_ok=True)
    if combo == "empty":
        pass
    elif combo == "single":
        (platform_root / "hermes-agent").mkdir(parents=True, exist_ok=True)
        _write_system(aimail_home, "single1", system_home=platform_root)
    elif combo == "multi":
        (platform_root / "hermes-agent").mkdir(parents=True, exist_ok=True)
        _write_system(aimail_home, "multi1", system_home=platform_root)
        _write_system(aimail_home, "multi2", system_home=platform_root / "missing-home")
    elif combo == "broken":
        # 断链：配置在、platform 根不存在；另有一个无 gateway 配置的目录
        _write_system(aimail_home, "broken1", system_home=platform_root / "gone")
        (aimail_home / "systems" / "orphan-dir").mkdir(parents=True, exist_ok=True)
    else:  # pragma: no cover - 参数化已穷举
        raise AssertionError(combo)
    return {"home": home, "aimail_home": aimail_home, "platform_root": platform_root}


def _env(fx: dict) -> dict:
    env = {k: v for k, v in os.environ.items()
           if not k.startswith(("AIMAIL_", "INTEGRATE_"))}
    env["HOME"] = str(fx["home"])
    env["AIMAIL_HOME"] = str(fx["aimail_home"])
    return env


# ── 跑与比对 ───────────────────────────────────────────────────────────────
def _run(cmd: list, args: tuple, env: dict) -> tuple:
    p = subprocess.run([*cmd, *args], capture_output=True, text=True, timeout=120, env=env)
    return p.returncode, p.stdout, p.stderr


_TS = re.compile(r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?")
_PID = re.compile(r"\b(pid|PID)[ =:]*\d+\b")


def _norm(text: str, roots: list) -> str:
    out = _TS.sub("<TS>", text)
    out = _PID.sub(r"\1<PID>", out)
    for r in sorted({str(x) for x in roots}, key=len, reverse=True):
        out = out.replace(r, "<ROOT>")
    # 尾部空白/结尾换行不参与判读（不同实现的收尾换行差异不该算差异）
    return "\n".join(line.rstrip() for line in out.strip().splitlines())


def _diff(a: str, b: str) -> list:
    import difflib
    return list(difflib.unified_diff(a.splitlines(), b.splitlines(),
                                     "left", "right", lineterm="", n=1))


# ── 测试 ───────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("combo", COMBOS)
def test_python_self_parity_harness(tmp_path, combo):
    """S0 验收：骨架能在 Python 版自比（两次运行逐行 diff 为空）。"""
    fx = make_fixture(tmp_path, combo)
    env = _env(fx)
    roots = [fx["home"], fx["aimail_home"]]
    for args in PY_SELF_PARITY_COMMANDS:
        rc1, so1, se1 = _run(PY_CLI, (args,), env)
        rc2, so2, se2 = _run(PY_CLI, (args,), env)
        assert rc1 == rc2, f"{combo}/{args}: rc {rc1} != {rc2}"
        assert _diff(_norm(so1, roots), _norm(so2, roots)) == [], f"{combo}/{args}: stdout 漂移"
        assert _diff(_norm(se1, roots), _norm(se2, roots)) == [], f"{combo}/{args}: stderr 漂移"
        # 非空断言：防"两边都是空输出"这种空洞通过
        assert (so1.strip() or se1.strip()), f"{combo}/{args}: 命令没有任何输出，判定无效"


def test_python_frozen_top_level_surface(tmp_path):
    """黑盒取 Python 侧冻结命令面（argparse invalid choice 会枚举全部 15 个）。"""
    fx = make_fixture(tmp_path, "empty")
    rc, _, err = _run(PY_CLI, ("__no_such_command__",), _env(fx))
    assert rc == 2, err
    m = re.search(r"invalid choice:.*?\(choose from ([^)]*)\)", err, re.S)
    assert m, f"无法从 argparse 报错里取命令面: {err!r}"
    got = tuple(sorted(x.strip().strip("'") for x in m.group(1).split(",") if x.strip()))
    assert got == tuple(sorted(FROZEN_TOP_LEVEL)), f"命令面漂移: {got}"


def test_python_frozen_prompt_subcommand_surface(tmp_path):
    fx = make_fixture(tmp_path, "empty")
    rc, _, err = _run(PY_CLI, ("prompt", "__nope__"), _env(fx))
    assert rc == 2, err
    m = re.search(r"invalid choice:.*?\(choose from ([^)]*)\)", err, re.S)
    assert m, f"prompt 报错里没有 choices: {err!r}"
    got = tuple(sorted(x.strip().strip("'") for x in m.group(1).split(",") if x.strip()))
    assert got == tuple(sorted(FROZEN_PROMPT_SUBCOMMANDS)), f"prompt 嵌套面漂移: {got}"


def test_rust_version_subcommand_and_cargo_version_agree():
    """版本真源 = 仓根 pyproject.toml；Cargo.toml 与 pysdk/__init__.py 必须同值。"""
    pyproject = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    cargo = (REPO / "cli" / "Cargo.toml").read_text(encoding="utf-8")
    init = (REPO / "pysdk" / "__init__.py").read_text(encoding="utf-8")
    m_py = re.search(r'^version\s*=\s*"([^"]+)"', pyproject, re.M)
    m_cargo = re.search(r'^version\s*=\s*"([^"]+)"', cargo, re.M)
    m_init = re.search(r'__version__\s*=\s*"([^"]+)"', init)
    assert m_py and m_cargo and m_init, "版本字段没找到（pyproject / cli/Cargo.toml / pysdk/__init__.py）"
    want, got_cargo, got_init = m_py.group(1), m_cargo.group(1), m_init.group(1)
    assert got_cargo == want, f"cli/Cargo.toml version {got_cargo} != pyproject {want}"
    assert got_init == want, f"pysdk/__init__.py version {got_init} != pyproject {want}"


def test_rust_parity_with_python(tmp_path):
    """Rust 已实现面的逐字等价 + 命令面集合相等 + rc 2 家族。

    已实现命令在**四种夹具组合**上逐字节比对（不是只挑一个顺的夹具）；
    夹具差异（空/单系统/多系统/断链）正是最容易暴露"分类判读不同"的地方。
    """
    rb = rust_bin()
    if rb is None:
        pytest.skip("rust CLI 未构建（cd cli && cargo build）— 本趟只跑 Python 自比")

    for combo in COMBOS:
        fx = make_fixture(tmp_path / combo, combo)
        env = _env(fx)
        roots = [fx["home"], fx["aimail_home"], tmp_path]
        for args in RUST_PARITY_INVOCATIONS:
            prc, pso, pse = _run(PY_CLI, args, env)
            rrc, rso, rse = _run([str(rb)], args, env)
            assert prc == rrc, f"{combo}/{args}: rc py={prc} rust={rrc}"
            assert (
                _diff(_norm(pso, roots), _norm(rso, roots)) == []
            ), f"{combo}/{args}: stdout 不等价\n{_norm(pso, roots)!r}\nvs\n{_norm(rso, roots)!r}"
            assert (
                _diff(_norm(pse, roots), _norm(rse, roots)) == []
            ), f"{combo}/{args}: stderr 不等价\n{_norm(pse, roots)!r}\nvs\n{_norm(rse, roots)!r}"

    fx = make_fixture(tmp_path / "surface", "single")
    env = _env(fx)

    # (2) 命令面集合相等（Python 走 invalid choice 枚举；Rust 读 --help 的 Commands 段）
    _, _, perr = _run(PY_CLI, ("__no_such_command__",), env)
    pm = re.search(r"invalid choice:.*?\(choose from ([^)]*)\)", perr, re.S)
    assert pm, f"取不到 Python 命令面: {perr!r}"
    py_surface = {x.strip().strip("'") for x in pm.group(1).split(",") if x.strip()}
    rrc, rso, _ = _run([str(rb)], ("--help",), env)
    assert rrc == 0
    # 只取 clap 的 `Commands:` 段（epilog 的场景分组名不是子命令）
    cmd_block = re.search(r"^Commands:\n((?:  .*\n)+)", rso, re.M)
    assert cmd_block, f"rust --help 里没有 Commands 段: {rso!r}"
    rust_surface = set(re.findall(r"^  ([a-z][a-z0-9-]*)", cmd_block.group(1), re.M))
    assert rust_surface == py_surface, f"命令面不等价: rust={sorted(rust_surface)} py={sorted(py_surface)}"

    # (3) rc 2 家族：缺子命令 / 未知子命令
    for args in ((), ("__no_such_command__",)):
        assert _run([str(rb)], args, env)[0] == 2, f"rust rc for {args} != 2"

    # (4) 机器面不得出现在人面 help（两侧同判据）
    for args in (("--help",), ("install", "--help")):
        _, rso, _ = _run([str(rb)], args, env)
        for lit in ("ensure-system", "payload", "system-only"):
            assert lit not in rso, f"rust {args}: 机器面字样泄漏 {lit!r}"
