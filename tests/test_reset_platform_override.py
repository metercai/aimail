"""`--platform` 显式覆盖:取值用注册表校验,非法值报错退出不静默回落(2026-09-29)。

取证背景(本用例的前提,结论 = 方案 A):
  想给 `resolve_platform` 加"从系统配置读 platform"的回退,但 `$AIMAIL_HOME/systems/
  <sid>/**/<BINDING_FILE>` **没有** platform 字段可读 —— 写入点
  pysdk/aimail_tools.py:501(register cfg 字面量)、pysdk/hermes/aimail_hermes.py:572
  (inject_cfg)、tssdk mail-core saveBinding(opts.extra 只有 agent_id/session_id/preset…)
  均无此键;系统配置 aimail_gateway.json 同样没有。故改为方案 A:
  `reset` 补可选 `--platform`,取值用 cli/platforms.json 校验(静态门禁
  「CLI free of platform literals」不许 CLI 源码出现平台名字面量)。

覆盖的 resolve_platform 入口(不只 reset):install(原有)、reset、uninstall、
bridge(refresh routes)—— 四者共用 `_platform_override()` 一个校验入口。

判决项:
  ① 合法 `--platform` ⇒ 平台解析成功(home 特征与指针都为空也能过关)
  ② 非法 `--platform` ⇒ rc=1 报错并列出合法值,**不静默回落**到探测
  ③ 不给 `--platform` ⇒ 原有失败路径逐字不变(`无法确定平台`)
  ④ 覆盖优先级高于 home 特征探测;四个入口的 --help 都暴露该参数
"""
import argparse
import importlib.util
import json
import os
import pathlib
import subprocess
import sys
from importlib.machinery import SourceFileLoader

import pytest

from aimail_contract import POINTER_FILE

REPO = pathlib.Path(__file__).resolve().parents[1]
CLI = REPO / "cli" / "aimail"
CLI_DIR = REPO / "cli"
REG = json.loads((CLI_DIR / "platforms.json").read_text())
VALID = sorted(REG["platforms"])[0]          # 注册表里的一个合法平台名(不写字面量)
BOGUS = "no-such-platform"
ENTRY_POINTS = ("install", "reset", "uninstall", "bridge")   # 全部走 resolve_platform

if str(CLI_DIR) not in sys.path:
    sys.path.insert(0, str(CLI_DIR))
if str(REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(REPO / "pysdk"))


def _load():
    """cli/aimail 无扩展名 —— 按文件路径加载为模块(与既有测试同款)。"""
    name = "aimail_cli_platform_override"
    loader = SourceFileLoader(name, str(CLI))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def _isolated(tmp_path: pathlib.Path):
    """隔离 HOME / AIMAIL_HOME:本机真实指针与系统配置一概不参与判定。"""
    home = tmp_path / "host-home"
    home.mkdir(exist_ok=True)
    aimail_home = tmp_path / "aimail-home"
    aimail_home.mkdir(exist_ok=True)
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["AIMAIL_HOME"] = str(aimail_home)
    return env, home, aimail_home


def _run(argv, env):
    return subprocess.run([sys.executable, str(CLI), *argv],
                          capture_output=True, text=True, env=env, timeout=90)


# ── ① 合法取值:平台解析成功 ───────────────────────────────────────

def test_valid_platform_flag_resolves_platform(tmp_path):
    """detect/指针都为空时,`--platform <合法>` 让 reset 越过平台关。"""
    env, home, _ = _isolated(tmp_path)
    r = _run(["reset", "-H", str(home), "--platform", VALID,
              "--system-id", "sys-override"], env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "无法确定平台" not in out, f"平台已由 --platform 解析,不应卡在平台关: {out}"
    # 平台与 sid 都解析成功 ⇒ 走到下一步(隔离 HOME 下本机无密钥)才停
    assert "无 admin-key" in out, out


def test_platform_override_helper_contract(capsys):
    """校验入口本身:合法值原样返回、未给返回 ''、非法值 exit(1) 且列出合法值。"""
    cli = _load()
    assert cli._platform_override(argparse.Namespace(platform=VALID)) == VALID
    assert cli._platform_override(argparse.Namespace(platform="")) == ""
    assert cli._platform_override(argparse.Namespace()) == ""     # 无该属性也不炸
    with pytest.raises(SystemExit) as exc:
        cli._platform_override(argparse.Namespace(platform=BOGUS))
    assert exc.value.code == 1
    out = capsys.readouterr().out
    assert f"--platform '{BOGUS}' unknown; valid:" in out
    assert VALID in out                                            # 合法值清单来自注册表


# ── ② 非法取值:报错退出,不静默回落 ────────────────────────────────

def test_unknown_platform_flag_fails_loudly(tmp_path):
    """打错字的 --platform 必须红,不能被 home 探测悄悄顶掉继续跑。"""
    env, home, _ = _isolated(tmp_path)
    r = _run(["reset", "-H", str(home), "--platform", BOGUS], env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert f"--platform '{BOGUS}' unknown; valid:" in out, out
    assert VALID in out
    assert "无法确定平台" not in out, "非法值不得落到探测分支(静默回落)"
    assert "无 admin-key" not in out, "非法值不得继续往下执行"


# ── ③ 原有失败路径保持不变 ─────────────────────────────────────────

def test_without_flag_original_failure_is_unchanged(tmp_path):
    env, home, _ = _isolated(tmp_path)
    r = _run(["reset", "-H", str(home)], env)
    out = r.stdout + r.stderr
    assert r.returncode == 1, out
    assert "无法确定平台" in out, out
    assert "unknown; valid:" not in out, "没给 --platform 就不该出现覆盖相关报错"


# ── ④ 覆盖优先于 home 特征探测 ─────────────────────────────────────

def test_platform_flag_beats_home_detection(tmp_path):
    """home 有 dsh 特征 + dsh 指针 ⇒ 不带 --platform 走探测;带了则以覆盖为准。"""
    env, home, aimail_home = _isolated(tmp_path)
    dsh_home = home / ".dsh"                       # 注册表 detect: dir_name=.dsh + 标记目录
    (dsh_home / "profiles").mkdir(parents=True)
    (dsh_home / "storages").mkdir()
    (dsh_home / POINTER_FILE).write_text(json.dumps({"system_id": "sys-dsh"}))

    # 控制组:不带 --platform ⇒ 特征探测得到 dsh,并由指针反查到 sid
    ctrl = _run(["reset", "-H", str(dsh_home)], env)
    assert "无法确定平台" not in (ctrl.stdout + ctrl.stderr), ctrl.stdout
    assert "无 admin-key" in (ctrl.stdout + ctrl.stderr), ctrl.stdout

    # 实验组:带 --platform ⇒ 以覆盖值为准(该平台在本机无指针 ⇒ 停在 system_id 关,
    # 而不是沿用探测出来的 dsh 继续走)
    over = _run(["reset", "-H", str(dsh_home), "--platform", VALID], env)
    out = over.stdout + over.stderr
    assert over.returncode == 1, out
    assert "无法确定 system_id" in out, out
    assert "无 admin-key" not in out, "--platform 未生效,仍按探测到的 dsh 走了"


# ── ⑤ 所有 resolve_platform 入口都暴露 --platform ──────────────────

@pytest.mark.parametrize("sub", ENTRY_POINTS)
def test_every_resolve_platform_entry_exposes_the_flag(sub, tmp_path):
    env, _, _ = _isolated(tmp_path)
    r = _run([sub, "--help"], env)
    assert r.returncode == 0, r.stderr
    assert "--platform" in r.stdout, f"{sub} 缺 --platform(resolve_platform 入口未对齐)"
