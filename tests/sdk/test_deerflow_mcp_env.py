"""deer-flow assembly written into extensions_config's MCP env must be **self-contained** (hermetic, 2026-10-02).

Background (J4e root cause, empirical evidence): when deer-flow starts stdio MCP server it **only carries** `mcpServers.<n>.env`
(third-party read-only reference `backend/packages/harness/deerflow/mcp/client.py:32-34`:
`params["env"] = config.env`), child process cannot get the installation side environment. install-mcp.sh originally only wrote
AIMAIL_AGENT_ID / AIMAIL_AGENT_IDENTITY ⇒ in child process
`aimail_base.aimail_home()`(pysdk/aimail_base.py:242-243) falls back to `~/.aimail` ⇒ cannot scan local
binding ⇒ every tool call throws `agent '<id>' not registered`(aimail_base.py:573-575) ⇒
agent can never send mail (deerflow J4e history 13 red 0 green). Counterfactual test: only adding AIMAIL_HOME is OK.

This file pins the contract as a verdict item:
  1. The written env carries AIMAIL_HOME (value == install-side home, same semantics as aimail_home());
  2. If the assembly step provides AIMAIL_SYSTEM_ID (pysdk/install.py::_assembly_env will setdefault), it is written;
     if not provided, the key is not written (server-side falls back to full scan = old behavior, does not write empty string to fake "pinned");
  3. Idempotent: re-running with the same input, file bytes remain unchanged;
  4. Existing keys (aimail server block / args pointing to payload) are not rolled back.

 The call surface follows the real product shape: actually runs `pysdk/deer-flow/install-mcp.sh` with a stub `aimail` on PATH
to fulfill `aimail install --payload …` (script follows P3 ruling to only use CLI public command surface, no fallback to in-repo paths).
"""
import json
import os
import stat
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "pysdk" / "deer-flow" / "install-mcp.sh"


def _stub_cli(bin_dir: Path, mcp_dir: Path) -> Path:
    """The aimail stub on PATH: fulfills the two payload subcommands the script uses.

    `--payload dir mcp` returns the **mcp payload directory** per product semantics (script uses this to construct
    `<dir>/aimail_mcp_server.py` and asserts the file is present).
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
    """Actually runs the install script, returns the path to extensions_config.json."""
    payload = tmp_path / "payload"
    # owner 2026-10-06 target state: MCP service taken from **installed package** (zero-copy ✗ no more <program root>/mcp/ copy) ⇒
    # Test provides the minimal form of an "installed package": an aimail package directory (containing aimail_mcp_server.py) + PYTHONPATH pointing to it.
    pkg = tmp_path / "python" / "aimail"
    pkg.mkdir(parents=True, exist_ok=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "aimail_mcp_server.py").write_text("# stub installed-package payload\n")
    bin_dir = _stub_cli(payload / "bin", pkg)

    home_root = tmp_path / "home"            # deer-flow checkout root (for installing version number)
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
        "PYTHONPATH": str(tmp_path / "python"),
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
    """Install side with home + system installed: env must carry these two items (= the only place server can read them)."""
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id="sys-j4e")
    env = json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]

    assert env["AIMAIL_HOME"] == str(home), env
    assert env["AIMAIL_SYSTEM_ID"] == "sys-j4e", env
    assert env["AIMAIL_AGENT_ID"] == "default", env
    assert env["AIMAIL_AGENT_IDENTITY"].startswith("deerflow/"), env
    # Service block shape (args[0] = MCP service inside installed package; owner 2026-10-06: zero-copy ✗)
    block = json.loads(cfg.read_text())["mcpServers"]["aimail"]
    # owner 2026-10-06 target state: MCP service is taken from the **installed package** (zero-copy ✗ —— no more copy of <program root>/mcp/).
    # Assertion binds only to **semantics** (not a copy under program root, and script will fail loudly if empty), not to whether package is installed locally (environment-agnostic ✓).
    _srv = block["args"][0]
    assert _srv.endswith("/aimail_mcp_server.py"), block
    assert "/mcp/" not in _srv, block


def test_env_without_system_id_omits_the_key(tmp_path):
    """No system_id provided ⇒ do not write empty key (empty string would be treated by server as pinning a non-existent system)."""
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id=None)
    env = json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]

    assert "AIMAIL_SYSTEM_ID" not in env, env
    assert env["AIMAIL_HOME"] == str(home), env


def test_env_home_falls_back_like_aimail_home(tmp_path):
    """Install side did not set AIMAIL_HOME ⇒ written value == $HOME/.aimail (same semantics as aimail_base.aimail_home()
    fallback verbatim; MCP subprocess resolves with same AIMAIL_HOME ⇒ both sides land on the same root)."""
    cfg = _run(tmp_path, aimail_home=None, system_id=None)
    env = json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]

    assert env["AIMAIL_HOME"] == str(tmp_path / "userhome" / ".aimail"), env


def test_write_is_idempotent(tmp_path):
    """Re-run with same input: file bytes unchanged (idempotency claimed by script, guarded by verdict item)."""
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id="sys-j4e")
    first = cfg.read_bytes()
    cfg2 = _run(tmp_path, aimail_home=str(home), system_id="sys-j4e")
    assert cfg2 == cfg
    assert cfg2.read_bytes() == first


def test_system_pin_is_kept_across_a_later_install(tmp_path):
    """set-if-absent: system pinned by first assembly is primary identity, subsequent reinstall for **another system** must not overwrite.

    2026-10-02 j4e4 measured (counter-example): under last-writer semantics, journey J2's `install -c` (shared domain buyer system)
    overwrote AIMAIL_SYSTEM_ID with shared system ⇒ MCP subprocess resolved to shared address ⇒ welcome email reply sent from
    `agent.j*@shared-e2e.local` ⇒ 0 lines in binding log for this round ⇒ J4e still red.
    """
    home = tmp_path / "aimail-home"
    cfg = _run(tmp_path, aimail_home=str(home), system_id="sys-first")
    assert json.loads(cfg.read_text())["mcpServers"]["aimail"]["env"]["AIMAIL_SYSTEM_ID"] == "sys-first"

    cfg2 = _run(tmp_path, aimail_home=str(home), system_id="sys-shared-buyer")
    env = json.loads(cfg2.read_text())["mcpServers"]["aimail"]["env"]
    assert env["AIMAIL_SYSTEM_ID"] == "sys-first", env
    assert env["AIMAIL_HOME"] == str(home), env
