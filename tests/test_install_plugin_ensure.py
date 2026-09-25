"""平台插件确保步(skip_if)+ `--home` 绝对化(2026-09-25)。

背景:
  ① README 承诺「aimail 命令行安装**或** Agent 插件安装,二选一即可完成对接」,但
     registry 里 openclaw/pi 只 print 人工步骤(R dsh 早已自动 `dsh plugin add`)⇒
     `aimail install -H <platform home>` 对这两个平台不闭环。修 = spawn 支持
     `skip_if` 探测: 已在位则跳过、缺失则安装(幂等,且容器回归不重复打 registry)。
  ② G2: `--home` 传相对路径(如 `.openclaw`)会被原样写入 cfg.system_home ⇒ 之后
     从别的 cwd 跑命令时平台 home 解析漂移。修 = 归一/落盘前一律绝对化。
"""
import importlib.util
import json
import os
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
_CLI_DIR = _REPO / "cli"
_REG = json.loads((_CLI_DIR / "platforms.json").read_text())
if str(_CLI_DIR) not in sys.path:
    sys.path.insert(0, str(_CLI_DIR))
if str(_REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(_REPO / "pysdk"))


def _load(path: Path, name: str):
    """cli/*.py 无扩展名者(cli/aimail)与普通模块统一按文件路径加载。"""
    loader = SourceFileLoader(name, str(path))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


def _run_steps(monkeypatch_steps, ctx):
    cli = _load(_CLI, "aimail_cli_plugin_ensure")
    cli._platform_def = lambda _p: {"install_steps": monkeypatch_steps}
    cli._run_install_steps("fake", ctx)


# ── ① skip_if 两态 ────────────────────────────────────────────────

def test_skip_if_probe_hit_skips_install(tmp_path):
    """探测命中(已在位)⇒ 安装 argv **不得执行**。"""
    marker = tmp_path / "install-ran"
    steps = [{
        "kind": "spawn",
        "argv": ["/bin/sh", "-c", f"touch {marker}"],
        "skip_if": {"argv": ["/bin/sh", "-c", "echo openclaw-aimail 1.2.3"], "match": "aimail"},
        "skipped_text": "已在位(跳过安装)",
    }]
    _run_steps(steps, {"home": str(tmp_path), "sid": "s"})
    assert not marker.exists(), "探测命中时不应执行安装"


def test_skip_if_probe_miss_runs_install(tmp_path):
    """探测未命中(缺失)⇒ 安装 argv 必须执行。"""
    marker = tmp_path / "install-ran"
    steps = [{
        "kind": "spawn",
        "argv": ["/bin/sh", "-c", f"touch {marker}"],
        "skip_if": {"argv": ["/bin/sh", "-c", "no packages installed"], "match": "aimail"},
        "skipped_text": "已在位(跳过安装)",
    }]
    _run_steps(steps, {"home": str(tmp_path), "sid": "s"})
    assert marker.exists(), "探测未命中时必须执行安装"


def test_probe_command_missing_is_not_silent_pass(tmp_path):
    """探测命令不可执行 ⇒ 视为未知 ⇒ 走安装(不得静默当成"已在位")。"""
    marker = tmp_path / "install-ran"
    steps = [{
        "kind": "spawn",
        "argv": ["/bin/sh", "-c", f"touch {marker}"],
        "skip_if": {"argv": ["definitely-not-a-real-cmd-xyz", "list"], "match": "aimail"},
        "on_missing": "warn", "warn_hint": "宿主 CLI 缺失",
    }]
    _run_steps(steps, {"home": str(tmp_path), "sid": "s"})
    assert marker.exists(), "探测无法执行时必须继续走安装动作"


# ── ② 注册表守卫(README 承诺钉死) ─────────────────────────────────

def test_openclaw_and_pi_have_plugin_ensure_step():
    """openclaw/pi: install_steps 必须含"探测+安装"的插件确保步(缺则 README 承诺不成立)。"""
    for plat, host_cmd, match in (("openclaw", "openclaw", "aimail"), ("pi", "pi", "pi-aimail")):
        steps = _REG["platforms"][plat]["install_steps"]
        ensure = [s for s in steps if s.get("kind") == "spawn" and s.get("skip_if")]
        assert ensure, f"{plat} 缺'探测+安装'的插件确保步"
        st = ensure[0]
        assert st["argv"][0] == host_cmd, f"{plat} 确保步应调宿主 CLI: {st['argv']}"
        assert st["skip_if"]["argv"][0] == host_cmd
        assert match in st["skip_if"]["match"], f"{plat} 探测匹配串应为 {match}"
        # 确保步必须排在注册之前(先装插件才能注册)
        kinds = [s.get("kind") for s in steps]
        assert kinds.index("spawn") < kinds.index("register_default"), \
            f"{plat} 插件确保步必须早于 register_default"


def test_openclaw_ensure_step_passes_trust_gate():
    """openclaw 三方插件安装必须显式带信任门(与 README 文档口径一致)。"""
    st = [s for s in _REG["platforms"]["openclaw"]["install_steps"]
          if s.get("kind") == "spawn" and s.get("skip_if")][0]
    assert "--force" in st["argv"] and "--accept-capabilities" in st["argv"]
    assert "install" in st["argv"]


def test_plugin_spec_is_unambiguous_npm_source():
    """插件 spec 必须指明是 registry 来源 —— 裸名会被 cwd 下**同名本地目录**遮蔽。

    2026-09-25 实测: 在 ~ 下执行 `openclaw plugins install openclaw-aimail`(裸名)
    被 /home/ubuntu/openclaw-aimail(本地 0.1.0 目录)遮蔽 ⇒ 装进 extensions/ 且
    缺少 CLI 注册 ⇒ `openclaw aimail register` 报 "does not know the command"。
    带 `@latest` 后走 npm registry(装到 npm/projects/, 0.1.15, 命令面正常)。
    """
    oc = [s for s in _REG["platforms"]["openclaw"]["install_steps"]
          if s.get("kind") == "spawn" and s.get("skip_if")][0]
    spec = oc["argv"][3]
    assert spec.startswith("openclaw-aimail@"), f"openclaw 插件 spec 必须是 npm 规格: {spec}"
    pi = [s for s in _REG["platforms"]["pi"]["install_steps"]
          if s.get("kind") == "spawn" and s.get("skip_if")][0]
    assert pi["argv"][2].startswith("npm:"), f"pi 插件 spec 必须显式 npm: 协议: {pi['argv'][2]}"


# ── ④ 地址改名后必须同步桥路由 ────────────────────────────────────

def test_rename_syncs_bridge_route(tmp_path, monkeypatch):
    """改名后: 新地址 upsert + 旧地址 DELETE(桥按地址匹配路由, 不改就断链)。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"
    (home / "bridge").mkdir(parents=True)
    (home / "bridge" / "aimail_routes.toml").write_text(
        '# Auto-generated by aimail-bridge.\n'
        '"agent.wei@x.tm" = "http://127.0.0.1:18789/aimail/inbound"\n')
    cli = _load(_CLI, "aimail_cli_route_sync")
    base = home / "systems" / "sid-x"
    (base / cli._addr_clean("main.wei@x.tm")).mkdir(parents=True)
    (base / cli._addr_clean("main.wei@x.tm") / "agentmail.json").write_text(json.dumps(
        {"email": "main.wei@x.tm", "webhook_url": "http://127.0.0.1:18789/aimail/inbound"}))

    calls = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), req.full_url, (req.data or b"").decode()))
        return _Resp()

    monkeypatch.setattr(cli.urllib.request, "urlopen", _fake_urlopen)
    cli._sync_bridge_route_after_rename("agent.wei@x.tm", "main.wei@x.tm", base, "sid-x")

    methods = [c[0] for c in calls]
    assert "POST" in methods, f"新地址未 upsert: {calls}"
    assert "DELETE" in methods, f"旧地址未摘除: {calls}"
    post = [c for c in calls if c[0] == "POST"][0]
    body = json.loads(post[2])
    assert body["email"] == "main.wei@x.tm"
    assert body["host"] == "http://127.0.0.1:18789/aimail/inbound", "host 必须传完整 URL(路径不丢)"
    delete = [c for c in calls if c[0] == "DELETE"][0]
    assert delete[1].endswith("agent.wei@x.tm"), delete[1]


def test_rename_route_sync_falls_back_to_old_target(tmp_path, monkeypatch):
    """新地址的 agentmail.json 缺 webhook_url ⇒ 回退旧路由目标(仍要 upsert)。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "home2"))
    home = tmp_path / "home2"
    (home / "bridge").mkdir(parents=True)
    (home / "bridge" / "aimail_routes.toml").write_text(
        '"old@x.tm" = "http://127.0.0.1:9101/aimail/inbound"\n')
    cli = _load(_CLI, "aimail_cli_route_sync2")
    base = home / "systems" / "sid-y"
    base.mkdir(parents=True)
    calls = []

    class _Resp:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b"{}"

    def _fake_urlopen(req, timeout=None):
        calls.append((req.get_method(), (req.data or b"").decode()))
        return _Resp()

    monkeypatch.setattr(cli.urllib.request, "urlopen", _fake_urlopen)
    cli._sync_bridge_route_after_rename("old@x.tm", "new@x.tm", base, "sid-y")
    post = json.loads([c for c in calls if c[0] == "POST"][0][1])
    assert post["email"] == "new@x.tm"
    assert post["host"] == "http://127.0.0.1:9101/aimail/inbound"


# ── ③ --home 绝对化(G2) ──────────────────────────────────────────

def test_relative_home_normalized_to_absolute(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".openclaw").mkdir()
    core = _load(_CLI_DIR / "runtime_core.py", "runtime_core_abspath_test")
    got = core.normalize_platform_home(Path(".openclaw"))
    assert os.path.isabs(str(got)), f"相对 --home 未绝对化: {got}"
    assert str(got) == str(tmp_path / ".openclaw")


def test_system_home_written_absolute(tmp_path, monkeypatch):
    """落盘侧兜底: 即使外部入口传相对 home,cfg.system_home 也必须绝对。"""
    monkeypatch.setenv("AIMAIL_HOME", str(tmp_path / "aimail-home"))
    monkeypatch.chdir(tmp_path)
    ss = _load(_CLI_DIR / "setup_system.py", "setup_system_abspath_test")
    ss._save_gateway_config("https://gw.example", "k" * 32, "sid-abspath-test",
                            system_home=".openclaw")
    cfg = json.loads((tmp_path / "aimail-home" / "systems" / "sid-abspath-test"
                      / "aimail_gateway.json").read_text())
    assert os.path.isabs(cfg["system_home"]), f"cfg.system_home 未绝对化: {cfg['system_home']}"
    assert cfg["system_home"] == str(tmp_path / ".openclaw")
