"""deer-flow 装配写进 extensions_config 的 MCP env 必须**自足**(hermetic, 2026-10-02)。

背景(J4e 根因, 实测取证): deer-flow 起 stdio MCP server 时**只带** `mcpServers.<n>.env`
(第三方只读参考 `backend/packages/harness/deerflow/mcp/client.py:32-34`:
`params["env"] = config.env`), 子进程拿不到安装侧的环境。install-mcp.sh 原先只写
AIMAIL_AGENT_ID / AIMAIL_AGENT_IDENTITY ⇒ 子进程里
`aimail_base.aimail_home()`(pysdk/aimail_base.py:242-243) 回落 `~/.aimail` ⇒ 扫不到本机
绑定 ⇒ 每次工具调用抛 `agent '<id>' not registered`(aimail_base.py:573-575) ⇒
agent 永远发不出信(deerflow J4e 历史 13 红 0 绿)。反事实实测: 只补 AIMAIL_HOME 即 OK。

本文件把该契约钉成判决项:
  1. 写出的 env 自带 AIMAIL_HOME(值 == 安装侧 home, 与 aimail_home() 同语义);
  2. 装配步给了 AIMAIL_SYSTEM_ID(pysdk/install.py::_assembly_env 会 setdefault)就写进去,
     没给就不写键(server 侧回退全扫描 = 旧行为, 不写空串冒充"钉住");
  3. 幂等: 同输入重跑, 文件字节不变;
  4. 原有键(aimail server 块 / args 指向载荷)不回退。

调用面按产品真实形状: 真跑 `pysdk/deer-flow/install-mcp.sh`, 用 PATH 上的 stub `aimail`
兑现 `aimail install --payload …`(脚本按 P3 裁决只走 CLI 公开命令面, 不兜底仓内路径)。
"""
import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "pysdk" / "deer-flow" / "install-mcp.sh"


def _stub_cli(bin_dir: Path, mcp_dir: Path) -> Path:
    """PATH 上的 aimail stub: 兑现脚本用到的两条 payload 子命令。

    `--payload dir mcp` 按产品语义返回 **mcp 载荷目录**(脚本据此拼
    `<dir>/aimail_mcp_server.py` 并断言文件在位)。
    """
    bin_dir.mkdir(parents=True, exist_ok=True)
    stub = bin_dir / "aimail"
    stub.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  *"install --payload dir mcp"*) echo "%s" ;;\n'
        '  *"install --payload install mcp"*) exit 0 ;;\n'
        '  *) echo "stub aimail: unexpected args: $*" >&2; exit 1 ;;\n'
        "esac\n" % mcp_dir
    )
    stub.chmod(stub.stat().st_mode | stat.S_IXUSR)
    return bin_dir


def _run(tmp_path: Path, *, aimail_home: str | None, system_id: str | None) -> Path:
    """真跑安装脚本, 返回 extensions_config.json 路径。"""
    payload = tmp_path / "payload"
    mcp_dir = payload / "mcp"
    mcp_dir.mkdir(parents=True, exist_ok=True)
    (mcp_dir / "aimail_mcp_server.py").write_text("# stub payload\n")
    bin_dir = _stub_cli(payload / "bin", mcp_dir)

    home_root = tmp_path / "home"            # deer-flow 检出根(装版本号用)
    (home_root / "backend").mkdir(parents=True, exist_ok=True)
    (home_root / "backend" / "pyproject.toml").write_text('version = "9.9.9"\n')
    cfg = tmp_path / "df" / "extensions_config.json"
    cfg.parent.mkdir(parents=True, exist_ok=True)

    env = dict(os.environ)
    env.update({
        "PATH": f"{bin_dir}{os.pathsep}{env.get('PATH', '')}",
        "HOME": str(tmp_path / "userhome"),
        "DEER_FLOW_HOME": str(home_root),
        "DEER_FLOW_PROJECT_ROOT": str(tmp_path / "dfproj"),
        "DEER_FLOW_EXT_CFG": str(cfg),
    })
    env.pop("AIMAIL_HOME", None)
    env.pop("AIMAIL_SYSTEM_ID", None)
    if aimail_home is not None:
        env["AIMAIL_HOME"] = aimail_home
    if system_id is not None:
        env["AIMAIL_SYSTEM_ID"] = system_id

    r = subprocess.run(["bash", str(SCRIPT)], env=env,
                       capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, f"install-mcp.sh rc={r.returncode}\n{r.stdout}\n{r.stderr}"
    assert cfg.is_file(), f"config not written: {cfg}\n{r.stdout}"
    return cfg


def test_env_carries_aimail_home_and_pinned_system(tmp_path):
    """装了 home + system 的安装侧: env 必须自带这两项(= server 唯一能读到的地方)。"""
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id="sys-j4e")
    env = json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]

    assert env["AIMAIL_HOME"] == str(home), env
    assert env["AIMAIL_SYSTEM_ID"] == "sys-j4e", env
    assert env["AIMAIL_AGENT_ID"] == "default", env
    assert env["AIMAIL_AGENT_IDENTITY"].startswith("deerflow/"), env
    # 服务块原有形状不回退(args 仍指向自包含载荷)
    block = json.loads(cfg.read_text())["mcpServers"]["aimail"]
    assert block["args"][0].endswith("/mcp/aimail_mcp_server.py"), block


def test_env_without_system_id_omits_the_key(tmp_path):
    """没给 system_id ⇒ 不写空键(空串会被 server 当成钉住一个不存在的 system)。"""
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id=None)
    env = json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]

    assert "AIMAIL_SYSTEM_ID" not in env, env
    assert env["AIMAIL_HOME"] == str(home), env


def test_env_home_falls_back_like_aimail_home(tmp_path):
    """安装侧没设 AIMAIL_HOME ⇒ 写出的值 == $HOME/.aimail(与 aimail_base.aimail_home()
    的回落逐字同语义; MCP 子进程按同一 AIMAIL_HOME 解析 ⇒ 两边落在同一个根)。"""
    cfg = _run(tmp_path, aimail_home=None, system_id=None)
    env = json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]

    assert env["AIMAIL_HOME"] == str(tmp_path / "userhome" / ".aimail"), env


def test_write_is_idempotent(tmp_path):
    """同输入重跑: 文件字节不变(脚本自称的幂等, 由判决项守住)。"""
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id="sys-j4e")
    first = cfg.read_bytes()
    cfg2 = _run(tmp_path, aimail_home=str(home), system_id="sys-j4e")
    assert cfg2 == cfg
    assert cfg2.read_bytes() == first


def test_system_pin_is_kept_across_a_later_install(tmp_path):
    """set-if-absent: 首次装配钉下的 system 是主身份, 之后为**别的 system** 重装不许改写。

    2026-10-02 j4e4 实测(反例): last-writer 语义下旅程 J2 的 `install -c`(共享域买家系统)
    把 AIMAIL_SYSTEM_ID 覆写成共享 system ⇒ MCP 子进程解析到共享地址 ⇒ 欢迎信回信从
    `agent.j*@shared-e2e.local` 发出 ⇒ 本轮绑定日志 0 行 ⇒ J4e 仍红。
    """
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id="sys-first")
    assert json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]["AIMAIL_SYSTEM_ID"] == "sys-first"

    cfg2 = _run(tmp_path, aimail_home=str(home), system_id="sys-shared-buyer")
    env = json.loads(cfg2.read_text())["mcpServers"]["aimail"]["env"]
    assert env["AIMAIL_SYSTEM_ID"] == "sys-first", env
    assert env["AIMAIL_HOME"] == str(home), env
