#!/usr/bin/env python3
"""repair.py — 链路自动修复(标准命令行,替代会话内个性化编程)。

原则(用户定调 2026-08-30):
- 不重新发明检测:逐项跑 check_status.py 的既有检测,✗ 才修,修完复检。
- 不写第二份注册逻辑:修复动作全部复用 install/共享链函数
  (deploy_bridge.start_bridge、aimail bridge 命令的路由重刷、
  aimail_base.register_agent_email 注册链)。
- agentmail.json 是唯一信任源:修复方向 = 把本地权威值写回云端/bridge。
- 幂等:重复修复结果一致(webhook 配对、路由注册均幂等)。
"""
import json
import subprocess
import sys
import re  # noqa: E402
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import os  # noqa: E402
from _common import aimail_home as _aimail_home

# 与 scripts/aimail 同语义:空 env 回退 ~/.aimail
# 主根目录唯一真源 = pysdk/aimail_base.aimail_home()(canonical 实现);
# 此处 import 失败时的降级副本仅保底(坏态恢复)


AIMAIL_HOME = _aimail_home()
SYSTEMS_DIR = AIMAIL_HOME / "systems"

GREEN, YELLOW, RED, NC = "\033[92m", "\033[93m", "\033[91m", "\033[0m"
OK, WARN, CROSS = "✓", "⚠", "✗"
BRIDGE_CFG = AIMAIL_HOME / "bridge" / "aimail_bridge.toml"
BRIDGE_PID = AIMAIL_HOME / "bridge" / "bridge.pid"
BRIDGE_BIN = AIMAIL_HOME / "bridge" / "bin" / "aimail-bridge"
ROUTES_FILE = AIMAIL_HOME / "bridge" / "aimail_routes.toml"


def _ok(msg):
    print(f"  {GREEN}{OK}{NC} {msg}")


def _warn(msg):
    print(f"  {YELLOW}{WARN}{NC} {msg}")


def _fail(msg):
    print(f"  {RED}{CROSS}{NC} {msg}")


def _bridge_pids():
    try:
        out = subprocess.check_output(
            ["pgrep", "-f", r"aimail-bridge.*--config|aimail-bridge.*\.toml"],
            text=True, timeout=5)
        return [int(l.strip()) for l in out.splitlines() if l.strip().isdigit()]
    except subprocess.CalledProcessError:
        return []


def _load_gateway_cfg(sid: str):
    p = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not p.is_file():
        return None
    return json.loads(p.read_text())


def _gateway_client(sid: str):
    """aimail_tools._GatewayClient(完整方法集)。runtime_core 统一解析核心目录。"""
    gw = _load_gateway_cfg(sid)
    if not gw:
        return None, None
    from runtime_core import load_core
    load_core()
    from aimail_tools import _GatewayClient
    return _GatewayClient(gw["gateway_url"], gw["admin_key"]), gw


def _run_check(sid: str):
    """跑既有 check(子进程,零逻辑复制),返回 (all_pass, checks[])。"""
    cmd = [sys.executable, str(SCRIPTS_DIR / "check_status.py"), "--json", "--system-id", sid]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    except subprocess.TimeoutExpired:
        # check 挂起不拖垮 repair——该检查项记失败,修复阶梯继续执行
        return False, [{"name": "check_status", "pass": False,
                        "detail": "check_status.py timed out after 120s"}]
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return False, []
    checks = data.get("checks", data if isinstance(data, list) else [])
    return all(c.get("pass") for c in checks), checks


def _ensure_bridge_running() -> bool:
    pids = _bridge_pids()
    if pids:
        _ok(f"bridge 无需修(已在运行 pid={pids[0]})")
        return True
    _warn("bridge 未运行 → 幂等启动(deploy_bridge.start_bridge)")
    if not BRIDGE_CFG.exists() or not BRIDGE_BIN.exists():
        _fail("bridge 未部署(配置/二进制缺失)——先跑 install")
        return False
    from deploy_bridge import start_bridge
    if start_bridge(str(BRIDGE_BIN), str(BRIDGE_CFG), str(BRIDGE_PID)):
        _ok(f"bridge 已启动 (pid={BRIDGE_PID.read_text().strip() if BRIDGE_PID.exists() else '?'})")
        return True
    _fail("bridge 启动失败——查日志 ~/.aimail/logs/aimail-bridge.log")
    return False


def _refresh_routes(sid: str) -> bool:
    """重刷该系统路由 = 复用 aimail 的 bridge --system-id 逻辑(子进程)。"""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "aimail"), "bridge", "--system-id", sid],
        capture_output=True, text=True, timeout=60)
    sys.stdout.write(r.stdout or "")
    if r.returncode != 0:
        sys.stdout.write(r.stderr or "")
        return False
    return True


def _repair_webhook_pairing(sid: str, deep: bool = False) -> bool:
    """gateway webhook 配对修复。

    证据驱动(默认):pull 模式下 webhook_url 必须为空串(设值=云端直推
    loopback,必败;2026-08-30 实锤);secret 不可经 GET 回显,故仅当本机
    能读到该系统 pending 队列出现"空签名"证据时,才用 agentmail.json 的
    secret 成对重写(register 链 already-exists 分支,幂等)。
    --deep:跳过证据,直接按 agentmail.json 成对重写全部本系统 agent。
    """
    c, gw = _gateway_client(sid)
    if not c:
        _fail(f"gateway 配置缺失,跳过 webhook 配对修复(system {sid})")
        return False
    sys.path.insert(0, str(SCRIPTS_DIR.parent / "pysdk"))
    try:
        pend = c._request("POST", "/api/v1/admin/pending",
                          body={"filter": [], "emails": []})
    except Exception as e:
        _warn(f"pending 查询失败({e})——无法取空签名证据;用 --deep 直接重写")
        return False
    batches = pend.get("batches", pend if isinstance(pend, list) else [])
    empties = []
    for b in batches if isinstance(batches, list) else []:
        for d in b.get("deliveries", []):
            try:
                hs = json.loads(d.get("headers") or "{}")
            except json.JSONDecodeError:
                hs = {}
            if not (hs.get("X-Webhook-Signature") or hs.get("X-Webhook-Signature-V2")):
                empties.append((d.get("id"), d.get("email")))
    if not empties and not deep:
        _ok("webhook 配对无需修(无证据指向配对缺失)")
        return False
    if deep and not empties:
        # --deep:无条件按 agentmail.json 重写本系统全部 agent 配对
        targets = []
        for ajx in sorted((SYSTEMS_DIR / sid).glob("*/agentmail.json")):
            try:
                d = json.loads(ajx.read_text())
            except Exception:
                continue
            if d.get("email"):
                targets.append(d["email"])
        empties = [(None, e) for e in targets]
        _warn(f"--deep:按 agentmail.json 重写 {len(empties)} 个 agent 的 webhook 配对")
    _warn(f"需修复 {len(empties)} 个配对目标(证据={bool([x for x in empties if x[0]]) or deep})")
    fixed = 0
    for _id, email in empties:
        # 目录键公式与全局约定一致(non [\w.-] → '_';含点地址 agent.x@dom → agent.x_dom)
        _key = re.sub(r"[^\w.\-]", "_", str(email))
        aj = SYSTEMS_DIR / sid / _key / "agentmail.json"
        if not aj.is_file():
            # 宽松匹配:按目录名前缀找
            cands = [d for d in (SYSTEMS_DIR / sid).glob("*/agentmail.json")
                     if email and json.loads(d.read_text()).get("email") == email]
            aj = cands[0] if cands else None
        if not aj:
            _fail(f"{email}: 本地 agentmail.json 缺失——唯一信任源丢失,请重装该 agent")
            continue
        local = json.loads(aj.read_text())
        from runtime_core import load_core
        load_core()
        import aimail_base as _ab
        res = _ab.register_agent_email(
            c, sid, email,
            webhook_url=local.get("webhook_url", ""),
            webhook_secret=local.get("webhook_secret", ""),
            manager_address=local.get("manager_address", ""),
        )
        _ok(f"{email}: 注册链重跑完成(配对 url+secret){' (key 返回)' if res.get('api_key') else ''}")
        fixed += 1
    # 清掉坏 pending(空签名的件不会自愈——headers 插队时已定)
    for pid_, _e in empties:
        if pid_ is None:
            continue
        try:
            c._request("POST", "/api/v1/admin/pending/ack", body={"ids": [pid_]})
            _ok(f"ack 坏 pending id={pid_}")
        except Exception as e:
            _warn(f"ack {pid_} 失败: {e}")
    return fixed > 0


def _drain_stuck(sid: str) -> bool:
    """--deep:ack 全部 stuck pending(>10min)兜底清理。"""
    c, _ = _gateway_client(sid)
    if not c:
        return False
    import time as _t
    try:
        pend = c._request("POST", "/api/v1/admin/pending", body={"filter": [], "emails": []})
    except Exception as e:
        _warn(f"pending 查询失败: {e}")
        return False
    batches = pend.get("batches", pend if isinstance(pend, list) else [])
    stuck = []
    for b in batches if isinstance(batches, list) else []:
        for d in b.get("deliveries", []):
            created = d.get("created_at") or ""
            try:
                from datetime import datetime
                age = _t.time() - datetime.fromisoformat(created.replace("Z", "+00:00")).timestamp()
                if age > 600:
                    stuck.append(d.get("id"))
            except ValueError:
                continue
    for pid_ in stuck:
        try:
            c._request("POST", "/api/v1/admin/pending/ack", body={"ids": [pid_]})
            _ok(f"ack stuck pending id={pid_}")
        except Exception as e:
            _warn(f"ack {pid_} 失败: {e}")
    return bool(stuck)


# ═══════════════════════════════════════════════════════════════
# 2026-09-04 维护套件:配置/资源/一致性修复(全部幂等)
# ═══════════════════════════════════════════════════════════════




def _registry_order_roots() -> list:
    """平台指针根候选(platforms.json order × home_dir——唯一平台知识源)。"""
    import json as _j
    try:
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        return [(n, reg["platforms"][n].get("home_dir", "." + n))
                for n in reg.get("order", []) if n in reg.get("platforms", {})]
    except Exception:
        return []


def _detect_platform_from_home(system_home):
    """与 cli/aimail.detect_platform_from_home 同口径的特征判定。"""
    p = Path(system_home)
    if p.name == ".pi" and (p / "agent").is_dir():
        return "pi"
    if p.name == ".dsh" and (p / "profiles").is_dir() and (p / "storages").is_dir():
        return "dsh"
    if (p / "hermes-agent").exists() or (p / "profiles").is_dir():
        return "hermes"
    if (p / "openclaw.json").is_file():
        return "openclaw"
    if (p / "backend" / "app" / "gateway").is_dir():
        return "deerflow"
    return "unknown"


def _auto_platform_home(sid: str) -> str:
    """单平台机自动解析平台根:恰好一个平台目录存在且特征匹配 → 用它;
    多平台/零平台 → ''(不猜,要求显式 --home)。"""
    hits = []
    home = Path.home()
    for plat, root in _registry_order_roots():
        d = home / root
        if d.exists() and _detect_platform_from_home(d) == plat:
            hits.append((plat, str(d)))
    if len(hits) == 1:
        return hits[0][1]
    if len(hits) > 1:
        # 多平台机:尝试用 gateway.json 已有 system_home
        gw = _load_gateway_cfg(sid) or {}
        sh = gw.get("system_home", "")
        if sh and Path(sh).is_dir():
            return sh
    return ""


def _pointer_paths_for(platform: str):
    """平台指针候选路径(platforms.json pointer 表驱动,与 aimail CLI 同一真源)。"""
    import json as _j
    try:
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        pdef = (reg.get("platforms", {}).get(platform) or {})
    except Exception:
        pdef = {}
    home = Path.home() / pdef.get("home_dir", f".{platform}")
    file = (pdef.get("pointer") or {}).get("file", ".agentmail")
    kind = (pdef.get("pointer") or {}).get("kind", "root")
    root_ptr = home / file
    if kind != "root_or_profiles":
        return [root_ptr]
    out = [root_ptr]
    profiles = home / "profiles"
    if profiles.is_dir():
        out += sorted(profiles.glob("*/.agentmail"))
    return out


def _sid_has_pointer(sid: str) -> bool:
    for plat in _pointer_registry_order():
        for ptr in _pointer_paths_for(plat):
            if ptr.is_file():
                try:
                    if json.loads(ptr.read_text()).get("system_id") == sid:
                        return True
                except Exception:
                    pass
    return False


def _pointer_registry_order() -> list:
    """注册表平台顺序(platforms.json order——CLI 唯一平台知识源)。"""
    try:
        import json as _j
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"), encoding="utf-8"))
        return list(reg.get("order", []))
    except Exception:
        return []


def _repair_gateway_config(sid: str, args_home: str = "") -> bool:
    """system_home/webhook_host 仅补缺,不覆盖已有值。"""
    gw_path = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not gw_path.is_file():
        _fail(f"gateway 配置不存在: {gw_path}")
        return False
    cfg = json.loads(gw_path.read_text())
    changed = False
    root = args_home or _auto_platform_home(sid)
    if not cfg.get("system_home"):
        if root and _detect_platform_from_home(Path(root)) != "unknown":
            cfg["system_home"] = root
            _ok(f"system_home backfilled: {root}")
            changed = True
        else:
            _warn("system_home 缺失且无法确定平台根(多平台机请用 --home)")
    if not cfg.get("webhook_host"):
        try:
            sys.path.insert(0, str(SCRIPTS_DIR))
            from setup_system import _detect_webhook_host
            wh = _detect_webhook_host(cfg.get("gateway_url", ""))
            if wh:
                cfg["webhook_host"] = wh
                _ok(f"webhook_host backfilled: {wh}")
                changed = True
        except Exception as e:
            _warn(f"webhook_host 探测失败(跳过): {e}")
    if changed:
        gw_path.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
        import os as _os
        _os.chmod(gw_path, 0o600)
    return changed


def _repair_pointer(sid: str, platform_home: str) -> bool:
    """指针重建:确定平台根 + sid 无任何指针 + 目标指针文件不存在 → 写。"""
    if _sid_has_pointer(sid):
        return False
    if not platform_home:
        return False
    plat = _detect_platform_from_home(Path(platform_home))
    if plat == "unknown":
        return False
    ajx = sorted((SYSTEMS_DIR / sid).glob("*/agentmail.json"))
    email = ""
    for a in ajx:
        try:
            d = json.loads(a.read_text())
            if d.get("email"):
                email = d["email"]
                break
        except Exception:
            continue
    if not email:
        return False
    home = Path.home()
    roots = dict(_registry_order_roots())
    ptr = home / roots.get(plat, "." + plat) / ".agentmail"
    if ptr.exists():
        return False  # 已有指针(指向别的系统)不覆盖
    ptr.write_text(json.dumps({"system_id": sid, "email": email}, indent=2))
    _ok(f"pointer created: {ptr} → {sid}")
    return True


# ═══════════════════════════════════════════════════════════════
# 运行时资源重部署(注册表驱动:health_checks 探针 + install_steps 入口)
# ═══════════════════════════════════════════════════════════════

_REG_FILE_KINDS = ("file_contains", "file_contains_alt", "file_exists",
                   "file_exists_any", "glob_dir_any")


def _platform_def(plat: str) -> dict:
    """注册表平台定义(platforms.json = 唯一平台知识源)。"""
    import json as _j
    try:
        reg = _j.load(open(str(Path(__file__).resolve().parent / "platforms.json"),
                           encoding="utf-8"))
        return (reg.get("platforms", {}) or {}).get(plat, {}) or {}
    except Exception:
        return {}


def _failing_file_checks(plat: str, sh: str) -> list:
    """资源完好探针:注册表 health_checks 的文件类检查项里,哪些无命中。

    定位键三类都要认(path/alt/glob)——openclaw 的插件目录检查用 glob,
    漏读会把"资源在位"误判成"缺失"。
    """
    import glob as _g
    fails = []
    for ch in _platform_def(plat).get("health_checks", []) or []:
        if ch.get("kind") not in _REG_FILE_KINDS:
            continue
        pat = ch.get("path", "") or ch.get("alt", "") or ch.get("glob", "")
        for cand in _g.glob(pat.replace("{home}", sh).replace("{user_home}", str(Path.home()))):
            if ch.get("kind") == "glob_dir_any":
                break  # 目录类:模式命中即视为就位(不做内容匹配)
            try:
                if ch.get("kind") in ("file_exists", "file_exists_any"):
                    if Path(cand).is_file():
                        break
                elif ch.get("marker", "") in Path(cand).read_text(errors="replace"):
                    break
            except Exception:
                continue
        else:
            fails.append(ch)
    return fails


def _sdk_install_target(plat: str) -> str:
    """该平台 SDK 自足安装入口的 type(注册表 install_steps kind=sdk_install → target)。

    没有该步骤的平台(openclaw/pi/dsh)资源由平台自身管理(插件/宿主命令),
    CLI 不代装 → 返回空串,调用方改为打印注册表自带的修复提示。
    """
    for st in _platform_def(plat).get("install_steps", []) or []:
        if st.get("kind") == "sdk_install" and st.get("target"):
            return str(st["target"])
    return ""


def _repair_runtime_resources(sid: str, platform_home: str) -> bool:
    """L2 运行时资源缺失 → 走该平台的 SDK 自足安装入口幂等重装。

    目标类型由注册表 install_steps(sdk_install → target)给出;没有入口的
    平台只打印 health_checks 自带的 fix 提示,不 spawn 注定失败的安装命令。
    """
    gw = _load_gateway_cfg(sid) or {}
    sh = platform_home or gw.get("system_home", "")
    if not sh or not Path(sh).is_dir():
        _warn("平台根不可定位,跳过运行时资源重部署(deerflow 等远端平台请到宿主机执行)")
        return False
    plat = _detect_platform_from_home(Path(sh))
    if plat == "unknown":
        _warn("平台类型无法识别(平台根既非已知平台也不像自建)⇒ 跳过运行时资源重部署")
        return False
    fails = _failing_file_checks(plat, sh)
    if not fails:
        _ok(f"{plat} 运行时资源无需修")
        return False
    tgt = _sdk_install_target(plat)
    if not tgt:
        _warn(f"{plat} 运行时资源缺失,该平台无 SDK 安装入口(资源由平台自身管理)→ 按提示处理:")
        for ch in fails:
            _warn(f"  [{ch.get('id', '?')}] {ch.get('fix') or ch.get('fail_text', '')}")
        return False
    _warn(f"{plat} 运行时资源缺失 → 幂等重装(install.py install --type {tgt} --home {sh})")
    # 按文件路径调用(与本文件其它步骤一致),不依赖解释器内 import aimail;
    # install.py 自举 sys.path,repo(pysdk/)与 pip(site-packages/aimail/)布局通用。
    from runtime_core import resolve_core_dir
    install_py = os.path.join(resolve_core_dir(), "install.py")
    r = subprocess.run(
        [sys.executable, install_py, "install", "--type", tgt, "--home", sh,
         "--system-id", sid],
        capture_output=True, text=True, timeout=300)
    sys.stdout.write((r.stdout or "")[-600:])
    if r.returncode == 0:
        _ok("runtime resources reinstalled")
        return True
    _fail(f"重装失败(exit {r.returncode}): {(r.stderr or '')[-200:]}")
    return False


def _repair_agentmail_json(sid: str) -> bool:
    """agentmail.json 补缺(可重建字段)+ webhook_url 对齐存活路由。"""
    import urllib.request, urllib.error
    gw = _load_gateway_cfg(sid) or {}
    changed = False
    sysdir = SYSTEMS_DIR / sid
    if not sysdir.is_dir():
        return False
    # routes 表(目标 URL)
    routes = {}
    if ROUTES_FILE.exists():
        for line in ROUTES_FILE.read_text().splitlines():
            line = line.strip()
            if "=" in line and not line.startswith("#"):
                k, v = line.split("=", 1)
                routes[k.strip().strip('"')] = v.strip().strip('"').strip(",")

    def _alive(url):
        if not url or not url.startswith("http"):
            return False
        try:
            urllib.request.urlopen(urllib.request.Request(url, data=b"", method="POST"), timeout=3)
            return True
        except urllib.error.HTTPError as e:
            return e.code != 404
        except Exception:
            return False

    for ajx in sorted(sysdir.glob("*/agentmail.json")):
        try:
            d = json.loads(ajx.read_text())
        except Exception:
            continue
        orig = dict(d)
        # 1) 可重建字段补缺(gateway.json 权威)
        for k in ("gateway_url", "domain", "system_id", "system_name", "manager_address"):
            if not d.get(k) and gw.get(k):
                d[k] = gw[k]
        # 2) webhook_url 对齐存活路由
        email = d.get("email", "")
        target = routes.get(email, "")
        declared = d.get("webhook_url", "")
        from urllib.parse import urlparse as _up
        _h = _up(target).hostname if target else ""
        _local = _h in ("127.0.0.1", "localhost", "::1")
        if target and _alive(target) and _local:
            if declared and not _alive(declared):
                d["webhook_url"] = target
                _ok(f"{ajx.parent.name}: webhook_url {declared} → {target}(declared 死、route 活)")
            elif declared and declared.rstrip("/") != target.rstrip("/"):
                d["webhook_url"] = target
                _ok(f"{ajx.parent.name}: webhook_url aligned to route target {target}")
        if d != orig:
            # 落盘走共享原子写(0600/tmp+rename;AUDIT-1 P1-2); 单文件失败只跳过该文件
            try:
                from aimail_base import save_agent_config as _sac
                _sac(d.get("agent_id", ""), d, sid)
                changed = True
            except Exception as e:  # noqa: BLE001
                _warn(f"{ajx.parent.name}: agentmail.json 写入失败({type(e).__name__}: {e})⇒ 跳过该文件")
    if not changed:
        _ok("agentmail.json 无需修(字段完整且 webhook_url 已对齐)")
    return changed


def _repair_routes_entries(sid: str) -> bool:
    """routes 缺条目 → 复用 bridge --system-id 重刷(bridge 侧自动生成)。"""
    sysdir = SYSTEMS_DIR / sid
    if not sysdir.is_dir() or not ROUTES_FILE.exists():
        return False
    routes = {}
    for line in ROUTES_FILE.read_text().splitlines():
        line = line.strip()
        if "=" in line and not line.startswith("#"):
            k, v = line.split("=", 1)
            routes[k.strip().strip('"')] = v.strip().strip('"').strip(",")
    missing = []
    skipped = []
    try:
        subs = sorted(sysdir.iterdir())
    except OSError as e:
        _warn(f"系统目录不可读({e})⇒ 跳过 routes 条目修复")
        return False
    for sub in subs:
        aj = sub / "agentmail.json"
        # 逐文件容错: 单文件不可读(权限/坏文件)只跳过该文件, 不终止整步(2026-09-20 实测)
        try:
            if not aj.is_file():
                continue
            d = json.loads(aj.read_text())
        except Exception as e:  # noqa: BLE001
            skipped.append(f"{sub.name}({type(e).__name__})")
            continue
        email = d.get("email", "")
        if email and email not in routes:
            missing.append(email)
    if skipped:
        _warn(f"routes 条目修复: 跳过 {len(skipped)} 个不可读文件: {', '.join(skipped)}")
    if not missing:
        _ok("routes 条目无需修")
        return False
    _warn(f"routes 缺条目: {missing} → bridge 重刷补齐")
    return _refresh_routes(sid)



def _repair_pull_entry_key(sid: str) -> bool:
    """bridge pull.systems 的 admin_key 与 gateway.json 对齐(gateway.json 为权威源)。"""
    gw_path = SYSTEMS_DIR / sid / "aimail_gateway.json"
    if not gw_path.is_file() or not BRIDGE_CFG.exists():
        return False
    gw = json.loads(gw_path.read_text())
    gk = gw.get("admin_key", "")
    if not gk:
        return False
    try:
        import tomllib
        with open(BRIDGE_CFG, "rb") as f:
            td = tomllib.load(f)
    except Exception:
        return False
    systems = (td.get("pull", {}) or {}).get("systems") or []
    entry = next((x for x in systems if x.get("system_id") == sid), None)
    if entry is None:
        # 缺失 ⇒ 本机权威值齐全即可本地创建(不依赖服务端), 复用 install 的同一写入函数
        url = gw.get("gateway_url", "") or gw.get("aimail_url", "")
        if not url:
            _warn("pull 条目缺失且 gateway.json 无 gateway_url ⇒ 无法本地创建(先补齐配置)")
            return False
        try:
            from deploy_bridge import write_bridge_config
            write_bridge_config(
                str(BRIDGE_CFG),
                str(td.get("mode") or "pull"),
                str(td.get("bind") or "127.0.0.1:38081"),
                url, gk, sid, api_key=str(gw.get("api_key", "") or ""))
        except Exception as e:  # noqa: BLE001
            _fail(f"pull 条目创建失败: {type(e).__name__}: {e}")
            return False
        _ok("bridge pull 条目缺失 → 已本地创建(aimail_url/admin_key/system_id 来自 gateway.json)")
        return True
    if entry is None or entry.get("admin_key") == gk:
        return False
    raw = BRIDGE_CFG.read_text()
    # 精确替换该条目的 admin_key 值(条目行内)
    import re as _re
    new_raw, n = _re.subn(
        r'(\{[^}]*system_id\s*=\s*"' + _re.escape(sid) + r'"[^}]*admin_key\s*=\s*")[^"]*(")',
        r'\g<1>' + gk + r'\g<2>',
        raw, count=1)
    if n == 0:
        # 字段顺序可能相反(admin_key 在 system_id 前)
        new_raw, n = _re.subn(
            r'(\{[^}]*admin_key\s*=\s*")[^"]*("[^}]*system_id\s*=\s*"' + _re.escape(sid) + r'")',
            r'\g<1>' + gk + r'\g<2>',
            raw, count=1)
    if n == 0:
        _warn("pull 条目 admin_key 对齐失败(格式未匹配)——请手工核对 aimail_bridge.toml")
        return False
    BRIDGE_CFG.write_text(new_raw)
    try:
        os.chmod(BRIDGE_CFG, 0o600)  # 凭据文件(AUDIT-1 F6)
    except OSError:
        pass
    _ok(f"pull entry admin_key aligned to gateway.json ({sid})")
    return True


# ── 可修复性收口清单(用户定调 2026-09-20) ──────────────────────────────
# 每个 check 维度都必须登记, 不留"没交代"的项:
#   kind="auto" 本机确定可自修(不依赖服务端/宿主状态) ⇒ 阶梯必须覆盖;
#               复检后仍 FAIL 且前提满足 ⇒ 视为**缺陷**(输出标 D, 退出码 1)
#   kind="hint" 不可靠自修(依赖服务端/宿主进程/管理员) ⇒ repair 只提示, 不硬试;
#               复检残留属正常(输出标 H)
#   needs       自修所需外部前提; 前提不满足时该步"跳过并说明原因"(不计缺陷)
#   step        覆盖该维度的阶梯序号(仅 auto 需要)
REPAIRABILITY: dict[tuple[str, str], dict] = {
    ("config", "complete"):          {"kind": "auto", "step": 4},
    ("config", "gateway_json"):      {"kind": "auto", "step": 4},
    ("config", "system_home"):       {"kind": "auto", "step": 4},
    ("config", "pointer"):           {"kind": "auto", "step": 5},
    ("gateway", "config"):           {"kind": "auto", "step": 4},
    ("gateway", "smtp_port"):        {"kind": "auto", "step": 4},
    ("gateway", "health"):           {"kind": "hint",
                                      "why": "目标网关服务未运行/不可达 ⇒ 由宿主侧启动或修复网关"},
    ("gateway", "api_key"):          {"kind": "hint",
                                      "why": "admin key 缺失或失效 ⇒ 重新注册(aimail install)或由管理员补发"},
    ("bridge", "process"):           {"kind": "auto", "step": 1},
    ("bridge", "config"):            {"kind": "auto", "step": 1},
    ("bridge", "config-complete"):   {"kind": "auto", "step": 1},
    ("bridge", "config_consistency"): {"kind": "auto", "step": 1},
    ("bridge", "config-mode"):       {"kind": "auto", "step": 1},
    ("bridge", "activity"):          {"kind": "auto", "step": 1},
    ("bridge", "self_health"):       {"kind": "auto", "step": 1},
    ("bridge", "pull_path"):         {"kind": "hint",
                                      "why": "bridge 轮询目标网关 pending API 失败 ⇒ 需网关可达(宿主侧); 配置侧缺条目由 pull-entry 修"},
    ("bridge", "pull-entry"):        {"kind": "auto", "step": 9},
    ("bridge", "routes-entry"):      {"kind": "auto", "step": 8},
    ("bridge", "routes-target"):     {"kind": "hint",
                                      "why": "目标 agent 端点未运行/地址不可达 ⇒ 由宿主侧启动该 agent"},
    ("agent", "config"):             {"kind": "auto", "step": 7},
    ("agent", "config-json"):        {"kind": "auto", "step": 7},
    ("agent", "config-consistency"): {"kind": "auto", "step": 7},
    ("agent", "name_apikey"):        {"kind": "auto", "step": 7},
    ("agent", "config-complete"):    {"kind": "hint",
                                      "why": "system_name 等字段权威在服务端(本地无源) ⇒ 用 aimail install 重注册, 或管理员处理"},
    ("agent", "pointer"):            {"kind": "auto", "step": 5},
    ("agent", "hook"):               {"kind": "auto", "step": 6, "needs": ["platform_sdk"]},
    ("agent", "skill"):              {"kind": "auto", "step": 6, "needs": ["platform_sdk"]},
    ("agent", "toolset"):            {"kind": "auto", "step": 6, "needs": ["platform_sdk"]},
    ("agent", "webhook"):            {"kind": "auto", "step": 3},
    ("agent", "discovery"):          {"kind": "hint",
                                      "why": "平台侧尚无该 agent 记录 ⇒ 用平台自身命令创建/注册 agent"},
    ("agent", "register"):           {"kind": "hint",
                                      "why": "地址未在云端注册 ⇒ aimail install/注册链, 或由管理员处理"},
    ("agent", "session"):            {"kind": "hint",
                                      "why": "agent 会话/端点未运行 ⇒ 由宿主侧启动该 agent"},
    ("runtime", "mcp-payload"):      {"kind": "auto", "step": 6},
    ("runtime", "board-resources"):  {"kind": "auto", "step": 6},
    ("runtime", "host-payload-refs"): {"kind": "auto", "step": 6},
    ("runtime", "platform-locatable"): {"kind": "auto", "step": 5},
    ("system", "id"):                {"kind": "auto", "step": 4},
}

_UNREGISTERED = {"kind": "hint",
                 "why": "未登记的检查维度 ⇒ 暂按需管理员介入处理(请把它反馈给维护者)"}


def repairability(level: str, name: str) -> dict:
    """查该 check 维度的可修复性归属(未登记 ⇒ HINT 且明确说明)。"""
    return REPAIRABILITY.get((str(level or ""), str(name or "")), dict(_UNREGISTERED))


def classify_residual(residual: list) -> tuple:
    """复检残留 → (defects, hints)。

    defects: 本机可自修却仍残(缺陷, 需要看日志/改代码)
    hints:   需服务端/宿主/管理员介入(只提示, 属正常)
    """
    defects, hints = [], []
    for c in residual:
        info = repairability(c.get("level", ""), c.get("check", "") or c.get("name", ""))
        (defects if info.get("kind") == "auto" else hints).append((c, info))
    return defects, hints


def _repair_mcp_payload() -> bool:
    """运行时 mcp 载荷缺失/陈旧 → 本机 bundle 幂等重装(不依赖网络)。

    判定以**载荷状态**为准, 不以退出码为准(避免误报)。"""
    import runtime_bundle as rb
    try:
        st = rb.payload_state("mcp")
    except Exception as e:  # noqa: BLE001
        _warn(f"mcp 载荷状态不可读, 跳过: {e}")
        return False
    if not st.get("present"):
        _warn("mcp 载荷未安装 → 幂等安装")
    elif not (st.get("missing") or st.get("stale")):
        _ok("mcp 载荷无需修(完整且与当前版本一致)")
        return False
    else:
        bad = list(st.get("missing") or []) + list(st.get("stale") or [])
        _warn(f"mcp 载荷缺失/陈旧({', '.join(bad)}) → 幂等重装")
    try:
        rb.install("mcp", force=True)
    except Exception as e:  # noqa: BLE001
        _fail(f"mcp 载荷重装异常: {e}")
        return False
    try:
        st2 = rb.payload_state("mcp")
    except Exception:  # noqa: BLE001
        st2 = {}
    if st2.get("present") and not (st2.get("missing") or st2.get("stale")):
        _ok(f"mcp 载荷已重装(v{st2.get('version')})")
        return True
    _fail("mcp 载荷重装后状态仍不完整(见上方输出)")
    return False


def repair(sid: str, deep: bool = False, dry_run: bool = False, home: str = "") -> int:
    print(f"  repair system={sid}{' [dry-run]' if dry_run else ''}{' [deep]' if deep else ''}")
    deep_home = str(Path(home).expanduser()) if home else _auto_platform_home(sid)
    passed, checks = _run_check(sid)
    if not checks:
        _fail("check 无输出——system_id 是否有效?")
        return 1
    fails = [c for c in checks if not c.get("pass")]
    if passed:
        _ok("check 全绿,无需修复")
        return 0
    for c in fails:
        _warn(f"check ✗ {c['level']}/{c['check']}: {c['detail'][:90]}")

    # 修复阶梯:无条件逐级执行(每步幂等,重复修复结果一致)。
    # 不按 check 分级做条件触发——check 对本机 bridge 存活等场景存在
    # 误判缺口(2026-08-30 实测:bridge 死报 "remote ✓"),全阶梯执行
    # 才能遍历各种异常情况。
    plan = [
        ("bridge 存活确保(死了则 start_bridge 幂等拉起)", lambda: _ensure_bridge_running()),
        ("bridge 路由重刷(文件+admin API 热加载)", lambda: _refresh_routes(sid)),
        ("gateway webhook 配对修复(证据驱动;--deep 直接重写)",
         lambda: _repair_webhook_pairing(sid, deep=deep)),
        ("gateway 配置回填(system_home/webhook_host 仅补缺)",
         lambda: _repair_gateway_config(sid, args_home=deep_home)),
        ("平台指针重建(仅当确定平台根且指针缺失)",
         lambda: _repair_pointer(sid, deep_home)),
        ("运行时资源重部署(补丁标记/资源缺失 → 幂等重装)",
         lambda: _repair_runtime_resources(sid, deep_home)),
        ("运行时载荷刷新(mcp 缺失/陈旧 → 本机 bundle 幂等重装)",
         lambda: _repair_mcp_payload()),
        ("agentmail.json 补缺 + webhook_url 对齐存活路由",
         lambda: _repair_agentmail_json(sid)),
        ("bridge routes 条目补齐(缺条目 → 重刷)",
         lambda: _repair_routes_entries(sid)),
        ("bridge pull 条目 admin_key 对齐 gateway.json",
         lambda: _repair_pull_entry_key(sid)),
    ]
    if deep:
        plan.append(("stuck pending 清理(--deep)", lambda: _drain_stuck(sid)))

    if dry_run:
        print("  [dry-run] 将执行:")
        for desc, _fn in plan:
            print(f"    - {desc}")
        return 0

    for desc, fn in plan:
        print(f"\n  ── {desc} ──")
        try:
            fn()
        except Exception as e:
            _fail(f"修复动作异常: {e}")

    # 复检
    print("\n  ── 复检 ──")
    passed2, checks2 = _run_check(sid)
    still = [c for c in checks2 if not c.get("pass")]
    if passed2:
        _ok("复检全绿")
        return 0
    defects, hints = classify_residual(still)
    for c, info in defects:
        _warn(f"[D 本机可修·仍未修] {c['level']}/{c['check']}: {c['detail'][:90]}")
    for c, info in hints:
        _warn(f"[H 需管理员/宿主] {c['level']}/{c['check']}: {c['detail'][:80]}")
        if info.get("why"):
            _warn(f"    → {info['why']}")
    _warn(f"复检未全绿: 本机可修缺陷 {len(defects)} 项 / 需管理员介入 {len(hints)} 项")
    if defects:
        _warn("⇒ 存在本机可修却未修复的项(缺陷): 请连同上方日志反馈维护者")
    else:
        _warn("⇒ 剩余项均属不可靠自修范畴(repair 只提示, 不硬试, 由系统管理员处理)")
    return 1


def main():
    import argparse
    ap = argparse.ArgumentParser(description="链路自动修复(check ✗ → 复用函数链修复 → 复检)")
    ap.add_argument("--system-id", required=False, default="")
    ap.add_argument("--home", help="平台 home(默认单指针机自动解析)")
    ap.add_argument("--deep", action="store_true",
                    help="深修:跳过证据直接按 agentmail.json 重写 webhook 配对 + 清 stuck pending")
    ap.add_argument("--dry-run", action="store_true", help="只列将执行的修复,不写任何状态")
    args = ap.parse_args()

    sid = args.system_id
    if not sid:
        # 权威解析:直接复用 aimail CLI 的 resolve_system_id(无扩展名 →
        # 显式 SourceFileLoader;仅取函数,CLI main 有 __main__ 守卫不执行)
        from importlib.machinery import SourceFileLoader
        loader = SourceFileLoader("aimail_cli", str(SCRIPTS_DIR / "aimail"))
        import importlib.util
        spec = importlib.util.spec_from_loader("aimail_cli", loader)
        if spec is None:
            print("  无法加载 aimail CLI 模块(system_id 解析失败)")
            return 1
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        sid, _platform = mod.resolve_system_id(
            Path(args.home).expanduser() if args.home else Path(), "")
    if not sid:
        print("  无法确定 system_id(--system-id)")
        return 1
    return repair(sid, deep=args.deep, dry_run=args.dry_run, home=args.home or "")


if __name__ == "__main__":
    sys.exit(main() or 0)
