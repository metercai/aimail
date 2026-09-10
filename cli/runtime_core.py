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
    load_adapter("hermes")          # 可选: 适配层裸导入可用: import aimail_hermes
    import aimail_base as _base
"""
from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

_CORE_DIR_NAME = "pysdk"
_ADAPTERS = ("hermes", "deer-flow")


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

    name ∈ hermes|deer-flow。挂上后适配层裸导入即可用
    (import aimail_hermes)。核心目录会一并挂上
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
def _platform_ptr_roots() -> tuple:
    """平台指针根(platforms.json order × home_dir——唯一平台知识源)。"""
    import json as _j
    from pathlib import Path as _P
    try:
        reg = _j.load(open(str(_P(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        return tuple((n, reg["platforms"][n].get("home_dir", "." + n))
                     for n in reg.get("order", []) if n in reg.get("platforms", {}))
    except Exception:
        return ()


_PLATFORM_PTR_ROOTS = _platform_ptr_roots()


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


def _resolve_aimail_home(aimail_home=None) -> str:
    """主根目录解析(本模块单点):显式参数 > AIMAIL_HOME env > ~/.aimail。
    canonical 规则与 pysdk/aimail_base.aimail_home() 同构。"""
    return str(aimail_home or os.environ.get("AIMAIL_HOME", "")
               or pathlib.Path.home() / ".aimail")


def single_system_sid(aimail_home=None) -> str:
    """systems/ 下恰有一个含 aimail_gateway.json 的目录 → 返回该 sid;
    多个或零个 → ''(不猜,要求显式 --system-id)。"""
    import pathlib
    ah = _resolve_aimail_home(aimail_home)
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
    ah = _resolve_aimail_home(aimail_home)
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
    ah = _resolve_aimail_home(aimail_home)
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


def parse_setup_stdout(out: str) -> dict:
    """容错解析 setup_system.py 的 stdout(单 JSON 契约的防御层)。

    CLI 契约是“stdout 只有一行 JSON”,但宿主/依赖模块在 import 期往 stdout
    打一行 warning 就会让整体 json.loads 失败(2026-09-11 实测:激活成功、
    配置已落盘,却报 setup finished without a system_id)。先试整体,再退回
    取最外层 {...} 切片;都失败返回 {}。
    """
    for cand in (out, _slice_object(out)):
        if not cand:
            continue
        try:
            data = json.loads(cand)
        except Exception:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _slice_object(out: str) -> str:
    a, b = out.find("{"), out.rfind("}")
    return out[a:b + 1] if 0 <= a < b else ""


def newest_system_sid(aimail_home=None, within_secs: int = 180) -> str:
    """刚写入的那个系统的 sid(激活路径的本地兜底)。

    仅当 setup 的 stdout 不可解析时才用:取 systems/*/aimail_gateway.json 中
    mtime 最新者,且必须落在 within_secs 窗口内——旧配置绝不能被误当成本次
    运行的结果。多个候选取最新;无候选返回 ""。
    """
    root = os.path.join(_resolve_aimail_home(aimail_home), "systems")
    now, best, best_m = time.time(), "", 0.0
    try:
        names = os.listdir(root)
    except OSError:
        return ""
    for name in names:
        p = os.path.join(root, name, "aimail_gateway.json")
        try:
            m = os.stat(p).st_mtime
        except OSError:
            continue
        if now - m > within_secs or m <= best_m:
            continue
        best, best_m = name, m
    return best


def normalize_platform_home(home):
    """平台根归一(2026-09-11 C 修)。

    `--home` 契约是“平台目录本身”(如 ~/.pi)。若用户传了**父目录**(例如含
    `.pi/agent` 的目录),平台判定会落空并回退成 hermes(表现为报
    webhook.py/profiles.py 缺失、`.agentmail` 写成 no_config)。这里:
      1) 给定目录本身命中某平台判据 → 原样返回;
      2) 否则其下恰好命中一个平台子目录 → 返回该子目录;
      3) 都不中 → 原样返回(让下游按原逻辑报错,不静默改语义)。
    判据与 check_status._detect_platform / 注册表同源(cli/platforms.json)。
    """
    try:
        p = Path(home).expanduser()
    except Exception:
        return home
    if not p.is_dir():
        return p

    reg_file = Path(__file__).resolve().parent / "platforms.json"
    try:
        reg = json.loads(reg_file.read_text())
    except Exception:
        return p

    def hits(d: Path) -> str:
        for name in reg.get("order", []):
            det = ((reg.get("platforms") or {}).get(name) or {}).get("detect") or {}
            dn = det.get("dir_name", "")
            markers = det.get("markers") or []
            if not markers or (dn and d.name != dn):
                continue
            if all((d / m).exists() for m in markers):
                return name
        return ""

    if hits(p):
        return p
    try:
        subs = sorted(x for x in p.iterdir() if x.is_dir())
    except Exception:
        return p
    for sub in subs:
        if hits(sub):
            return sub
    return p


if __name__ == "__main__":
    print(f"core\t{load_core()}")
    print(f"platform-sid\t{platform_pointer_sid()}")
    print(f"single-system-sid\t{single_system_sid()}")
