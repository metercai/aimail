"""注册地址契约(2026-09-26/27 用户裁定后定稿)。

① 地址基名:平台的默认 agent 名只是**平台内部 id**(openclaw `main` / hermes `default`),
   地址基名必须过别名映射(`pysdk/aimail_base.email_for_agent` 契约)⇒ openclaw main 的地址
   是 `agent.<系统标识>@域`。旧实现把 registry 的 default_name(`main`)当请求名直接注册出去,
   云端落下 `main.xixi@aimail.token.tm`(应 `agent.xixi@…`),而控制台还打印 `main@…`(缺系统标识)。
② 注册路径**不推桥路由**: 路由是 CLI 的环境职责(2026-09-28 去桥化裁决), 且由
   `install/reset` 对账与宿主入站信号驱动 —— 不是注册命令的副产品; SDK 零桥符号
   (棘轮禁列)。本测试断言注册路径零 `/api/v1/routes` POST; 路由跟随由 L2 门禁
   (D2-T / J5-6e)按平台断言。
③ CLI 传**意图**(P6/C3, 2026-10-02): 注册器模板带 `--name`, 地址派生在注册器侧
   (TS `emailForAgent` / Python `email_for_agent` 同一规则); 本文件按同规则派生后断言。

不做任何网络: dummy `_run_registrar` 记录 argv,urlopen 记录(并拒绝)路由请求。
"""
import importlib.util
import json
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

_REPO = Path(__file__).resolve().parent.parent
_CLI = _REPO / "cli" / "aimail"
if str(_REPO / "pysdk") not in sys.path:
    sys.path.insert(0, str(_REPO / "pysdk"))


def _load_cli(name: str):
    loader = SourceFileLoader(name, str(_CLI))
    mod = importlib.util.module_from_spec(importlib.util.spec_from_loader(name, loader))
    loader.exec_module(mod)
    return mod


class _Rec:
    def __init__(self):
        self.oks, self.warns, self.fails, self.stdout = [], [], [], []

    def install(self, cli):
        cli._ok = lambda m: self.oks.append(str(m))
        cli._warn = lambda m: self.warns.append(str(m))
        cli._fail = lambda m, *a, **k: (_ for _ in ()).throw(AssertionError(f"_fail: {m}"))


class _Resp:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def read(self):
        return b"{}"


_DOMAIN = "aimail.token.tm"
_SYSTEM_NAME = "xixi"


def _addr_of(argv) -> str:
    """注册器收到的地址意图(C3): `--name <base>` 按注册器同规则派生 / `--email` 显式。"""
    import aimail_base
    if "--name" in argv:
        return aimail_base.email_for_agent(argv[argv.index("--name") + 1],
                                           _DOMAIN, _SYSTEM_NAME,
                                           default_aliases=())
    return argv[argv.index("--email") + 1]


def _cfg(*, bridge: bool):
    cfg = {
        "system_id": "shared-default-6905ddad",
        "system_name": _SYSTEM_NAME,
        "domain": _DOMAIN,
        "gateway_url": "https://aimail.token.tm",
        "admin_key": "k" * 32,
        "manager_address": "m@x.tm",
    }
    if bridge:                      # key present => deployment has a local bridge (pull/push)
        cfg["webhook_host"] = ""    # "" = pull mode
    return cfg


def _setup(tmp: Path, monkeypatch, argv_seen: list, posts: list, *, create_binding=True):
    cli = _load_cli("aimail_cli_reg_addr_" + str(abs(hash(str(tmp))))[:12])
    monkeypatch.setattr(cli, "AIMAIL_HOME", tmp / "home")
    (tmp / "home" / "systems").mkdir(parents=True, exist_ok=True)

    def _registrar(argv):
        argv_seen.append(list(argv))
        if create_binding:
            email = _addr_of(argv)
            d = tmp / "home" / "systems" / "shared-default-6905ddad" / cli._addr_clean(email)
            d.mkdir(parents=True, exist_ok=True)
            (d / "agentmail.json").write_text(json.dumps(
                {"email": email, "webhook_url": "http://127.0.0.1:18789/aimail/inbound"}))
        return 0, "registered"

    monkeypatch.setattr(cli, "_run_registrar", _registrar)

    def _urlopen(req, timeout=None):
        posts.append((req.get_method(), req.full_url, json.loads((req.data or b"{}").decode())))
        return _Resp()

    monkeypatch.setattr(cli.urllib.request, "urlopen", _urlopen)
    return cli


def test_openclaw_default_registers_the_alias_mapped_address(tmp_path, monkeypatch):
    """openclaw main(无显式 -n)⇒ 注册器收到的地址必须是 agent.xixi@…(别名映射 + 系统标识)。"""
    argv_seen, posts = [], []
    cli = _setup(tmp_path, monkeypatch, argv_seen, posts)
    rec = _Rec()
    rec.install(cli)

    cli._register_agent_now("openclaw", "main", _cfg(bridge=True), "main", "")

    assert argv_seen, "注册器必须被调用"
    assert "--name" in argv_seen[-1], f"C3: CLI 应传意图 --name 而非拼好的地址: {argv_seen[-1]}"
    emailed = _addr_of(argv_seen[-1])
    assert emailed == "agent.xixi@aimail.token.tm", f"openclaw main 的地址基名须归一为 agent: {emailed}"


def test_openclaw_explicit_name_is_used_verbatim(tmp_path, monkeypatch):
    """显式 -n 指定名字时原样使用(不套平台默认别名),但仍带系统标识。"""
    argv_seen, posts = [], []
    cli = _setup(tmp_path, monkeypatch, argv_seen, posts)
    rec = _Rec()
    rec.install(cli)
    cli._register_agent_now("openclaw", "main", _cfg(bridge=True), "weijia", "")
    assert "--name" in argv_seen[-1]
    assert _addr_of(argv_seen[-1]) == "weijia.xixi@aimail.token.tm"


def test_cli_does_not_push_the_bridge_route(tmp_path, monkeypatch):
    """CLI 不得自己推桥路由: owner 是平台注册链(mail-core.registerBridgeRoute 等)。

    两份实现 = 两套真相;用户裁定"不能各做各的"(2026-09-27)。有 bridge 的部署也不许推。
    """
    argv_seen, posts = [], []
    cli = _setup(tmp_path, monkeypatch, argv_seen, posts)
    rec = _Rec()
    rec.install(cli)
    cli._register_agent_now("openclaw", "main", _cfg(bridge=True), "main", "")
    assert posts == [], f"CLI 注册路径不得发桥路由请求(owner=平台链): {posts}"


def test_report_prints_the_effective_address(tmp_path, monkeypatch, capsys):
    """成功报告必须打印实际地址(带系统标识),不得再打印裸名地址。"""
    argv_seen, posts = [], []
    cli = _setup(tmp_path, monkeypatch, argv_seen, posts)
    rec = _Rec()
    rec.install(cli)
    cli._register_agent_now("openclaw", "main", _cfg(bridge=False), "main", "")
    out = " ".join(rec.oks)
    assert "agent.xixi@aimail.token.tm" in out, f"报告须带系统标识: {out}"
    assert "main@aimail.token.tm" not in out, f"不得打印裸名地址: {out}"


def test_openclaw_default_does_not_trigger_rename(tmp_path, monkeypatch):
    """别名归一后不得再触发 rename(main→agent 不是"改名",是同一地址的两个名字)。"""
    argv_seen, posts = [], []
    cli = _setup(tmp_path, monkeypatch, argv_seen, posts)
    rec = _Rec()
    rec.install(cli)
    called = []
    monkeypatch.setattr(cli, "_rename_after_reg", lambda *a, **k: called.append(a))
    cli._register_agent_now("openclaw", "main", _cfg(bridge=True), "main", "")
    assert called == [], f"默认别名不得走 rename: {called}"
