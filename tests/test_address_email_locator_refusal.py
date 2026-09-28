"""`aimail address -e <地址>` 单独出现时必须解析目标，不得静默落成 list（2026-09-29）。

实测（CLI L2 / J5-6c）：`aimail address -s SID -e no-such@x` ⇒ **rc=0 + 打印地址列表 + 零报错**。
代码链：`cli/aimail` 的 op 由 flag 推导，只有 `-e` 时落成 `"list"` ⇒ list 分支先 `return 0`，
于是「本机未找到地址 …」的拒绝分支**不可达** = F6 类静默降级。

修法：`list` 收窄为「没有任何定位参数」；`-e` 单独出现（无 -m/-n/-d）按 `show` 语义
**先解析目标**，解析失败 `_fail`（rc=1，且不打印列表）。

范围边界（有意不改，登记在案）：`-a <名>` 单独出现仍是 list —— `tests/cli/docker/cli-in-host.sh:169`
对该形态有 `exits 0` 的 expect，且 `tests/cli/README.md` 的 FINDING F6 把「`-a` 打印列表 rc=0」
登记为已登记 GAP（无 register_default 的平台靠它走 CLI 侧注册提示）。
本文件用 `test_agent_only_scan_stays_a_list` 把这条边界钉住，防止无声越权。

可证伪（红锚）：把 op 推导回退掉（变异 CLI 拷到 tmp），
`test_red_anchor_mutant_falls_back_to_list` 里 `-e <不存在的地址>` 重新变回 rc=0 + 列表。
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest
from aimail_contract import BINDING_FILE

REPO = pathlib.Path(__file__).resolve().parents[1]
CLI = REPO / "cli" / "aimail"
SID = "sys-j5-locator"
ADDR = "agent.one@gw.test"
ADDR2 = "agent.two@gw.test"
MISSING = "no-such-agent@gw.test"
LIST_MARKER = "默认主 agent 名"          # the list branch's own header (as in the F6-family tests)
#: 单一真源 = pysdk/aimail_base.py:262（`_aimail_system_dir(sid) / "aimail_gateway.json"`）
GW_CFG_NAME = "aimail_gateway.json"

_FIXED = '          else "show" if args.email\n          else "list")\n'
_OLD = '          else "list")\n'


def _home(tmp_path: pathlib.Path) -> pathlib.Path:
    home = tmp_path / "aimail-home"
    d = home / "systems" / SID
    d.mkdir(parents=True, exist_ok=True)
    (d / GW_CFG_NAME).write_text(json.dumps({
        "gateway_url": "https://gw.test", "admin_key": "AK", "system_id": SID,
        "domain": "gw.test", "default_agent_name": "agent",
    }), encoding="utf-8")
    return home


def _binding(home: pathlib.Path, email: str, manager: str = "mgr@ext.test") -> None:
    d = home / "systems" / SID / email.replace("@", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / BINDING_FILE).write_text(json.dumps({
        "email": email, "system_id": SID, "domain": "gw.test", "api_key": "k" * 64,
        "manager_address": manager, "webhook_url": "http://127.0.0.1:8645/hook",
        "webhook_secret": "s" * 64,
    }), encoding="utf-8")


def _run(home: pathlib.Path, *argv, cli: pathlib.Path | None = None):
    env = dict(os.environ, HOME=str(home), AIMAIL_HOME=str(home))
    # 变异副本不在仓内 ⇒ 显式给 PYTHONPATH(cli/ 的 runtime_core/_common 等),
    # 否则变体只能证明「导入失败」而不是「修前行为」。
    env["PYTHONPATH"] = os.pathsep.join([str(REPO / "cli"), str(REPO / "pysdk")])
    return subprocess.run([sys.executable, str(cli or CLI), *argv],
                          env=env, capture_output=True, text=True, timeout=120)


def _mutant(tmp_path: pathlib.Path) -> pathlib.Path:
    """op 推导回退版 CLI（红锚：修法失效时的原行为）。

    复制品放独立目录并**符号链接**其余 cli/ 资源（platforms.json 等）——
    否则变体只会证明「找不到 registry」，证明不了修前行为。
    """
    src = CLI.read_text(encoding="utf-8")
    assert _FIXED in src, "红锚失效：当前 CLI 不再含被替换的 op 推导片段（先对齐本测试）"
    root = tmp_path / "mutant-cli"
    root.mkdir()
    for p in (REPO / "cli").iterdir():
        if p.name != "aimail":
            os.symlink(p, root / p.name)
    p = root / "aimail"
    p.write_text(src.replace(_FIXED, _OLD), encoding="utf-8")
    return p


# ── green: `-e` 单独出现 = 只读定位，目标不存在 ⇒ 拒绝，且绝不打印列表 ───────────

def test_missing_email_locator_is_refused(tmp_path):
    home = _home(tmp_path)
    _binding(home, ADDR)
    r = _run(home, "address", "-s", SID, "-e", MISSING)
    assert r.returncode != 0, f"目标不存在却 rc=0（F6 类静默降级）:\n{r.stdout}"
    assert LIST_MARKER not in r.stdout, f"拒绝时仍打印了列表:\n{r.stdout}"
    assert "已注册" not in r.stdout
    assert MISSING in r.stdout, f"拒绝理由必须点名地址:\n{r.stdout}"


def test_existing_email_locator_shows_that_row_only(tmp_path):
    home = _home(tmp_path)
    _binding(home, ADDR)
    _binding(home, ADDR2)
    r = _run(home, "address", "-s", SID, "-e", ADDR)
    assert r.returncode == 0, r.stdout
    assert ADDR in r.stdout and "已注册" in r.stdout
    assert ADDR2 not in r.stdout, f"show 不得退化成全量列表:\n{r.stdout}"


def test_no_locator_still_lists(tmp_path):
    home = _home(tmp_path)
    _binding(home, ADDR)
    _binding(home, ADDR2)
    r = _run(home, "address", "-s", SID)
    assert r.returncode == 0 and LIST_MARKER in r.stdout
    assert ADDR in r.stdout and ADDR2 in r.stdout, f"无定位参数仍应是全量视图:\n{r.stdout}"


def test_agent_only_scan_stays_a_list(tmp_path):
    """边界：`-a` 单独出现仍是 list（cli-in-host.sh:169 expect + README FINDING F6）。

    这条**不是**认可静默降级，而是把「本轮只收窄 -e」写成可读的边界，
    改动越界时它会红。
    """
    home = _home(tmp_path)
    _binding(home, ADDR)
    r = _run(home, "address", "-s", SID, "-a", "agent")
    assert r.returncode == 0 and LIST_MARKER in r.stdout, (
        "`-a` 单独出现的行为被改动了 —— 那超出本轮授权（见 cli-in-host.sh:169 / README F6）")


def test_action_flags_still_win_over_show(tmp_path):
    """`-e … -m …` 仍是 set-manager（show 分支不得抢走上报路径）。

    注意（2026-09-29 实测，另案）：这条走的是 set-manager 的**自身播报**，
    而它不看 `_GatewayClient._request` 返回的错误字典 —— 网关不可达时
    原文是 `{'status': 0, 'error': '<urlopen error [Errno -2] Name or service not known>'}`
    却仍打印「云端+本地已同步」。该「播报与事实不一致」是 F6 同族缺陷，
    已登记待处理，不在本修范围（本测试只钉 op 分流）。
    """
    home = _home(tmp_path)
    _binding(home, ADDR)
    r = _run(home, "address", "-s", SID, "-e", ADDR, "-m", "mgr@ext.test")
    assert "云端+本地已同步" in r.stdout, f"set-manager 路径未被走到:\n{r.stdout}"
    assert LIST_MARKER not in r.stdout, "set-manager 不得落回列表/show 视图"


# ── red anchor: the pre-fix behaviour really is the silent degradation ─────────

def test_red_anchor_mutant_falls_back_to_list(tmp_path):
    home = _home(tmp_path)
    _binding(home, ADDR)
    r = _run(home, "address", "-s", SID, "-e", MISSING, cli=_mutant(tmp_path))
    assert r.returncode == 0, f"变异（修前）行为应当 rc=0:\n{r.stdout}"
    assert LIST_MARKER in r.stdout, f"变异（修前）行为应当打印列表:\n{r.stdout}"
    assert "未找到地址" not in r.stdout, (
        "修前形态不解析目标（无拒绝理由）—— 这正是被修掉的静默降级")
