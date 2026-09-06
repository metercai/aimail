#!/usr/bin/env python3
"""runtime_core — 仓库侧维护脚本的运行时核心加载器(单一实现)。

用途: scripts/ 与 bin/ 下的维护脚本注册/解注册/测试 agent 时需要
导入运行时核心(aimail_base / aimail_tools / gateway_api)与平台
适配层(amail_base / aimail_hermes)。本模块统一解析核心目录并挂到
sys.path,消除各脚本散落的 `sys.path.insert(... "tools"...)` 仓路径耦合。

源解析(repo 优先 > pip 兜底):
  1. 仓库 pysdk/(维护脚本 = 仓库自身工具链,应用仓库当前代码;
     若 pip 优先,开发期装了旧版 aimail 包会用错代码)
  2. pip aimail(site-packages/aimail,仓库 pysdk/ 缺失时兜底)

用法(各维护脚本头部):
    import os, sys
    _SCRIPTS = os.path.dirname(os.path.abspath(__file__))          # 或按层级上溯
    if _SCRIPTS not in sys.path:
        sys.path.insert(0, _SCRIPTS)
    from runtime_core import load_core, load_adapter

    load_core()                      # 核心裸导入可用: import aimail_base ...
    load_adapter("openclaw")         # 可选: 适配层裸导入可用: import amail_base
    import aimail_base as _base
"""
from __future__ import annotations

import os
import sys

_CORE_DIR_NAME = "pysdk"
_ADAPTERS = ("openclaw", "hermes", "deer-flow")


def _repo_core_dir() -> str:
    """仓库 pysdk/ 绝对路径(本文件在 cli/ 下,上溯一级)。"""
    return os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", _CORE_DIR_NAME))


def _pip_core_dir() -> str | None:
    """pip aimail 包目录(载荷根,含 aimail_base.py);未安装返回 None。"""
    try:
        import aimail  # type: ignore
        pkg = os.path.dirname(os.path.abspath(aimail.__file__))
        if os.path.isfile(os.path.join(pkg, "aimail_base.py")):
            return pkg
    except Exception:
        pass
    return None


def resolve_core_dir() -> str:
    """返回运行时核心目录(repo pysdk/ 优先 > pip aimail)。"""
    repo = _repo_core_dir()
    if os.path.isfile(os.path.join(repo, "aimail_base.py")):
        return repo
    pip = _pip_core_dir()
    if pip:
        return pip
    raise SystemExit("ERROR: 运行时核心未找到(仓库 pysdk/ 与 pip aimail 均不可用)")


def load_core() -> str:
    """把核心目录挂到 sys.path(幂等),返回核心目录。

    挂上后核心模块的裸导入即可用: import aimail_base / aimail_tools /
    gateway_api(_aimail_bootstrap 亦在核心目录)。
    """
    d = os.path.abspath(resolve_core_dir())
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


def load_adapter(name: str) -> str:
    """把平台适配层子目录挂到 sys.path(幂等),返回适配层目录。

    name ∈ openclaw|hermes|deer-flow。挂上后适配层裸导入即可用
    (import amail_base / aimail_hermes)。核心目录会一并挂上
    (适配层依赖核心)。
    """
    if name not in _ADAPTERS:
        raise SystemExit(f"ERROR: 未知适配层 {name}(可选: {', '.join(_ADAPTERS)})")
    load_core()
    d = os.path.abspath(os.path.join(resolve_core_dir(), name))
    if not os.path.isdir(d):
        raise SystemExit(f"ERROR: 适配层目录缺失: {d}")
    if d not in sys.path:
        sys.path.insert(0, d)
    return d


# ── 系统身份默认解析(2026-09-05,消除"默认平台 = ~/.hermes"硬编码)──
# 独立脚本(welcome/ping/persona/check)无参调用时,身份解析必须平台无关:
# 显式 --system-id > env > 显式平台根指针 > 平台注册表指针 > 单系统目录。
# 语义与 cli/aimail resolve_system_id、check_status _detect_default_sid
# 的"事实推断,不猜平台"定调一致;歧义(多系统/多平台)时返回 '' 要求显式。

# 平台根 → 指针文件名(注册表顺序 = 探测优先级,与 check PLATFORMS 一致)
_PLATFORM_PTR_ROOTS = (
    ("openclaw", ".openclaw"),
    ("dsh", ".dsh"),
    ("pi", ".pi"),
    ("deerflow", ".deer-flow"),
    ("hermes", ".hermes"),
)


def _read_ptr_sid(ptr: os.PathLike) -> str:
    try:
        with open(ptr, encoding="utf-8") as f:
            import json
            return json.load(f).get("system_id", "")
    except Exception:
        return ""


def platform_pointer_sid(home=None) -> str:
    """平台注册表指针 → system_id:依次查各平台 .agentmail,第一个有
    system_id 的返回(home 默认 Path.home())。Hermes 特例:根指针缺失时
    查 profiles/*/.agentmail(profile 级指针)。全部无 → ''。"""
    import pathlib
    home = pathlib.Path(home or pathlib.Path.home())
    for _plat, root in _PLATFORM_PTR_ROOTS:
        ptr = home / root / ".agentmail"
        if ptr.is_file():
            sid = _read_ptr_sid(ptr)
            if sid:
                return sid
        if root == ".hermes":
            profiles = home / ".hermes" / "profiles"
            if profiles.is_dir():
                for p in sorted(profiles.glob("*/.agentmail")):
                    if p.is_file():
                        sid = _read_ptr_sid(p)
                        if sid:
                            return sid
    return ""


def single_system_sid(aimail_home=None) -> str:
    """systems/ 下恰有一个含 aimail_gateway.json 的目录 → 返回该 sid;
    多个或零个 → ''(不猜,要求显式 --system-id)。"""
    import pathlib
    ah = aimail_home or os.environ.get("AIMAIL_HOME", "") or str(pathlib.Path.home() / ".aimail")
    systems = pathlib.Path(ah).expanduser() / "systems"
    if not systems.is_dir():
        return ""
    found = ""
    for d in sorted(systems.iterdir()):
        if d.is_dir() and (d / "aimail_gateway.json").is_file():
            if found:  # 第二个系统 → 歧义
                return ""
            found = d.name
    return found


def resolve_system_id(explicit_sid: str = "", agent_home: str = "") -> str:
    """独立脚本的统一 system_id 默认链(平台无关)。

    explicit_sid(--system-id) > env(SYSTEM_ID/AIMAIL_SYSTEM_ID) >
    agent_home 指针(显式平台根时) > 平台注册表指针 > 单系统目录。
    返回 '' 表示无法唯一判定(调用方报错并提示 --system-id)。
    """
    sid = (explicit_sid or os.environ.get("SYSTEM_ID", "")
           or os.environ.get("AIMAIL_SYSTEM_ID", "")).strip()
    if sid:
        return sid
    if agent_home:
        import pathlib
        ptr = pathlib.Path(agent_home).expanduser() / ".agentmail"
        if ptr.is_file():
            sid = _read_ptr_sid(ptr)
            if sid:
                return sid
    sid = platform_pointer_sid()
    if sid:
        return sid
    return single_system_sid()


# ── install 目标双向反查(2026-09-06)──
# install 允许只带 --home 或只带 --system-id:配置里 system_home 与
# system_id 互反查。归属不唯一时不猜(返回 '' 由调用方提示显式参数)。

def _cfg_system_home(sid: str, aimail_home=None) -> str:
    import json
    import pathlib
    ah = aimail_home or os.environ.get("AIMAIL_HOME", "") or str(pathlib.Path.home() / ".aimail")
    cfg = pathlib.Path(ah).expanduser() / "systems" / sid / "aimail_gateway.json"
    try:
        return str(json.loads(cfg.read_text(encoding="utf-8")).get("system_home", "") or "")
    except Exception:
        return ""


def _norm_home(p: str) -> str:
    import pathlib
    if not p:
        return ""
    try:
        return os.path.abspath(str(pathlib.Path(p).expanduser()))
    except Exception:
        return p or ""


def system_home_from_sid(sid: str, aimail_home=None) -> str:
    """sid → 其配置里的 system_home(绝对化)。无 → ''。"""
    return _norm_home(_cfg_system_home(sid, aimail_home))


def sid_from_system_home(system_home: str, aimail_home=None) -> str:
    """home → 归属系统:扫描全部 systems/*/ 配置,匹配且唯一 → 该 sid;
    零或多个 → ''(不猜)。"""
    import pathlib
    ah = aimail_home or os.environ.get("AIMAIL_HOME", "") or str(pathlib.Path.home() / ".aimail")
    systems = pathlib.Path(ah).expanduser() / "systems"
    target = _norm_home(system_home)
    found = ""
    if not systems.is_dir() or not target:
        return ""
    for d in sorted(systems.iterdir()):
        if not (d.is_dir() and (d / "aimail_gateway.json").is_file()):
            continue
        if _norm_home(_cfg_system_home(d.name, ah)) == target:
            if found:  # 第二个归属 → 歧义,不猜
                return ""
            found = d.name
    return found


if __name__ == "__main__":
    print(f"core\t{load_core()}")
    print(f"platform-sid\t{platform_pointer_sid()}")
    print(f"single-system-sid\t{single_system_sid()}")
