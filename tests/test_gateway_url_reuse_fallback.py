"""复用路径的网关地址回落 + uninstall 的注销目标旋钮 —— 产品候选 #15（2026-09-29）。

缺陷原文（CLI 门禁 L2 journey J5-10d 活体复现，openclaw/hermes 容器）：

    复用重跑 `aimail install -H <ph> --platform <p> -s <sid> -k <key>`（**不带 -g**）
      ⇒ systems/<sid>/aimail_gateway.json 的 gateway_url 被从本轮夹具网关改写成
        生产默认 https://aimail.token.tm

根因：`cmd_install` / `cmd_ensure_system` 都用
`_env_val("AIMAIL_URL", <生产默认>)` 兜底，**从不读被复用系统自己的 cfg**；
同族 `reset` 一直是 `... or prev.get("gateway_url", "")` 口径（cli/aimail:1122）。
下游：`uninstall` 只能读 cfg（无 -g）→ 注销打到错误的网关 → 夹具地址/白名单残留
（硬门禁红）+ “地址集被清空”假红。

本测试锁三件事，每件都可证伪：

  1. 网关地址只有一条优先级（全命令共用 `_resolve_gateway_url`）：
     显式 -g > AIMAIL_URL > 本系统 cfg.gateway_url > 默认；
  2. 该口径在**两个复用入口**（install / install --system-only）都在用
     （调用点计数：改一处漏一处是这一族的复发形态）；
  3. `uninstall` 有 -g，且 cfg 缺 gateway_url 时**显式播报**、绝不落到生产默认。

红锚：把已知 local 的 cfg 摆好、把 prev 分支从 helper 里删掉（变异副本真的跑起来），
同一条断言即回落到生产默认 —— 证明第 1/2 条不是装饰。
"""
from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

from aimail_contract import POINTER_FILE

REPO = Path(__file__).resolve().parents[1]
CLI_PATH = REPO / "cli" / "aimail"
SID = "shared-test-abc"
#: 本轮夹具网关形态（带端口；与 L2 夹具 http://127.0.0.1:34401 同形）
FIXTURE_GW = "http://127.0.0.1:34401"
PROD_DEFAULT = "https://aimail.token.tm"

#: 修法片段（红锚的替换目标）—— prev 分支
_PREV_BRANCH = ('    v = _prev_gateway_url(sid)\n'
                '    if v:\n'
                '        return v, "prev"\n')


def _load(name: str, path: Path):
    # cli/aimail 无扩展名 ⇒ spec_from_file_location 会返回 None，必须显式给 loader
    # （仓内既有姿势，见 tests/test_bridge_route_target.py / test_ping_smtp_host_parse.py）
    loader = SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    sys.modules[name] = mod
    loader.exec_module(mod)
    return mod


def _write_system_cfg(home: Path, cfg: dict) -> Path:
    d = home / "systems" / SID
    d.mkdir(parents=True, exist_ok=True)
    p = d / "aimail_gateway.json"
    p.write_text(json.dumps(cfg), encoding="utf-8")
    return p


# ── 1. 一条优先级（显式 > env > prev > 默认）─────────────────────────────────────

def _cli(home: Path, name: str):
    """在给定 AIMail 主根下加载真身 CLI（沿用仓内 SourceFileLoader 姿势）。"""
    os.environ["AIMAIL_HOME"] = str(home)
    os.environ.pop("AIMAIL_URL", None)
    return _load(name, CLI_PATH)


def test_reuse_path_inherits_local_gateway_url(tmp_path):
    """复用重跑不带 -g / 无 AIMAIL_URL ⇒ 用本系统 cfg 的 gateway_url（不是生产默认）。"""
    home = tmp_path / "aimail-home"
    _write_system_cfg(home, {"gateway_url": FIXTURE_GW, "admin_key": "AK", "system_id": SID})
    cli = _cli(home, "cli_under_test_prev")

    url, src = cli._resolve_gateway_url("", SID)
    assert (url, src) == (FIXTURE_GW, "prev"), (url, src)
    assert url != PROD_DEFAULT, "复用路径又落回生产默认了（#15 复发）"


def test_explicit_flag_and_env_still_win(tmp_path):
    """显式 -g 与 AIMAIL_URL 的优先级不变（修 prev 兜底不能掀掉上面两级）。"""
    home = tmp_path / "aimail-home"
    _write_system_cfg(home, {"gateway_url": FIXTURE_GW, "admin_key": "AK", "system_id": SID})
    cli = _cli(home, "cli_under_test_prec")

    assert cli._resolve_gateway_url("https://flag.test", SID) == ("https://flag.test", "flag")
    os.environ["AIMAIL_URL"] = "https://env.test"
    try:
        assert cli._resolve_gateway_url("", SID) == ("https://env.test", "env")
        assert cli._resolve_gateway_url("https://flag.test", SID) == ("https://flag.test", "flag")
    finally:
        os.environ.pop("AIMAIL_URL", None)


def test_no_local_value_falls_to_default_and_is_declared(tmp_path):
    """既无本地 cfg 值、也无 -g / env ⇒ 兜底默认，但**来源是 default**（调用方据此播报）。"""
    home = tmp_path / "aimail-home"
    (home / "systems").mkdir(parents=True, exist_ok=True)
    cli = _cli(home, "cli_under_test_default")

    assert cli._resolve_gateway_url("", "no-such-system") == (PROD_DEFAULT, "default")


# ── 2. 两个复用入口共用同一口径（防“改一处漏一处”）──────────────────────────────

def test_both_reuse_entries_use_the_shared_resolver():
    src = CLI_PATH.read_text(encoding="utf-8")
    n = src.count("_resolve_gateway_url(args.gateway_url, sid)")
    assert n == 2, (f"复用入口应恰好 2 处共用同一 prev 口径(install + install --system-only)，"
                    f"实际 {n} —— 新入口漏接就会静默打错网关")
    assert "_resolve_gateway_url(args.gateway_url)" in src, \
        "新系统(激活码)路径的默认兜底口径被删了"


# ── 3. 红锚：删掉 prev 分支 ⇒ 同一条断言复现缺陷 ─────────────────────────────────

def test_red_anchor_without_prev_branch_reproduces_the_defect(tmp_path):
    src = CLI_PATH.read_text(encoding="utf-8")
    assert _PREV_BRANCH in src, "红锚失效：当前 cli/aimail 不再含被替换的修法片段（先对齐本测试）"

    home = tmp_path / "aimail-home"
    _write_system_cfg(home, {"gateway_url": FIXTURE_GW, "admin_key": "AK", "system_id": SID})

    mutant_root = tmp_path / "mutant"
    shutil.copytree(REPO / "cli", mutant_root / "cli")
    os.symlink(REPO / "pysdk", mutant_root / "pysdk")
    target = mutant_root / "cli" / "aimail"
    target.write_text(src.replace(_PREV_BRANCH, ""), encoding="utf-8")

    os.environ["AIMAIL_HOME"] = str(home)
    os.environ.pop("AIMAIL_URL", None)
    mutant = _load("cli_mutant_no_prev", target)
    # 旧行为：复用重跑（不带 -g）⇒ 生产默认 —— 正是 J5-10d 实测的改写值
    assert mutant._resolve_gateway_url("", SID) == (PROD_DEFAULT, "default")
    # 真身给出本地值：同一条断言两侧分叉，证明本测试真的在测这个分支
    assert _load("cli_green_probe", CLI_PATH)._resolve_gateway_url("", SID) == (FIXTURE_GW, "prev")

    shutil.rmtree(mutant_root, ignore_errors=True)


# ── 4. uninstall：-g 存在 + cfg 缺值时显式播报（绝不静默打生产默认）──────────────

def _uninstall_host(tmp_path, cfg: dict):
    """造一个能走到“网关注销”那一段的最小机器：平台根(hermes 特征) + 系统 cfg。"""
    ph = tmp_path / "ph"
    hermes = ph / ".hermes"
    (hermes / "hermes-agent").mkdir(parents=True)
    (hermes / "profiles").mkdir(parents=True)
    # 平台自己的指针也在（真机形态）——文件名走契约常量，不写字面量（棘轮）
    (hermes / POINTER_FILE).write_text(json.dumps({"system_id": SID}), encoding="utf-8")
    home = tmp_path / "aimail-home"
    full = {"system_id": SID, "system_home": str(hermes)}
    full.update(cfg)
    _write_system_cfg(home, full)
    return home, hermes


def _run_uninstall(home: Path, hermes: Path, *extra: str):
    env = dict(os.environ, AIMAIL_HOME=str(home), HOME=str(home))
    env.pop("AIMAIL_URL", None)
    return subprocess.run(
        [sys.executable, str(CLI_PATH), "uninstall", "-s", SID, "-H", str(hermes), "-y", *extra],
        env=env, capture_output=True, text=True, timeout=180)


def test_uninstall_exposes_the_gateway_flag():
    p = subprocess.run([sys.executable, str(CLI_PATH), "uninstall", "--help"],
                       capture_output=True, text=True, timeout=60)
    assert p.returncode == 0, p.stderr
    assert "--gateway-url" in p.stdout and "-g" in p.stdout, p.stdout


def test_uninstall_without_gateway_url_says_so_and_never_uses_a_default(tmp_path):
    home, hermes = _uninstall_host(tmp_path, {})          # cfg 里没有 gateway_url
    p = _run_uninstall(home, hermes)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert "缺 gateway_url" in out and "未给 -g" in out, out
    assert PROD_DEFAULT not in out, "cfg 缺值时静默打了生产默认"


def test_uninstall_g_flag_overrides_the_cfg_and_is_announced(tmp_path):
    home, hermes = _uninstall_host(tmp_path, {"gateway_url": PROD_DEFAULT, "admin_key": "AK"})
    target = "http://127.0.0.1:1"                          # 不可达：不会真的注销到别处
    p = _run_uninstall(home, hermes, "-g", target)
    out = p.stdout + p.stderr
    assert p.returncode == 0, out
    assert f"gateway_url: {target}" in out, out
    assert f"覆盖 cfg.gateway_url={PROD_DEFAULT}" in out, out
