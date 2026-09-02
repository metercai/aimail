#!/usr/bin/env python3
"""repair.py — 链路自动修复(标准命令行,替代会话内个性化编程)。

原则(用户定调 2026-08-30):
- 不重新发明检测:逐项跑 check_status.py 的既有检测,✗ 才修,修完复检。
- 不写第二份注册逻辑:修复动作全部复用 install/共享链函数
  (deploy_bridge.start_bridge、agentmail scripts/agentmail 的路由重刷、
  aimail_base.register_agent_email 注册链)。
- agentmail.json 是唯一信任源:修复方向 = 把本地权威值写回云端/bridge。
- 幂等:重复修复结果一致(webhook 配对、路由注册均幂等)。
"""
import json
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import os  # noqa: E402

# 与 scripts/agentmail 同语义:空 env 回退 ~/.agentmail
_AH_ENV = os.environ.get("AIMAIL_HOME") or os.environ.get("AGENTMAIL_HOME", "")
AIMAIL_HOME = Path(_AH_ENV).expanduser() if _AH_ENV else Path.home() / ".agentmail"
SYSTEMS_DIR = AIMAIL_HOME / "systems"

GREEN, YELLOW, RED, NC = "\033[92m", "\033[93m", "\033[91m", "\033[0m"
OK, WARN, CROSS = "✓", "⚠", "✗"
BRIDGE_ADDR = "127.0.0.1:38081"
BRIDGE_CFG = AIMAIL_HOME / "bridge" / "aimail_bridge.toml"
BRIDGE_PID = AIMAIL_HOME / "bridge" / "bridge.pid"
BRIDGE_BIN = AIMAIL_HOME / "bridge" / "bin" / "aimail-bridge"


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
    p = SYSTEMS_DIR / sid / "agentmail_gateway.json"
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
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    try:
        data = json.loads(out.stdout)
    except json.JSONDecodeError:
        return False, []
    checks = data.get("checks", data if isinstance(data, list) else [])
    return all(c.get("pass") for c in checks), checks


def _ensure_bridge_running() -> bool:
    pids = _bridge_pids()
    if pids:
        return True
    _warn("bridge 未运行 → 幂等启动(deploy_bridge.start_bridge)")
    if not BRIDGE_CFG.exists() or not BRIDGE_BIN.exists():
        _fail("bridge 未部署(配置/二进制缺失)——先跑 install")
        return False
    from deploy_bridge import start_bridge
    if start_bridge(str(BRIDGE_BIN), str(BRIDGE_CFG), str(BRIDGE_PID)):
        _ok(f"bridge 已启动 (pid={BRIDGE_PID.read_text().strip() if BRIDGE_PID.exists() else '?'})")
        return True
    _fail("bridge 启动失败——查日志 ~/.agentmail/logs/aimail-bridge.log")
    return False


def _refresh_routes(sid: str) -> bool:
    """重刷该系统路由 = 复用 agentmail 的 bridge --system-id 逻辑(子进程)。"""
    r = subprocess.run(
        [sys.executable, str(SCRIPTS_DIR / "agentmail"), "bridge", "--system-id", sid],
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
        return False  # 无证据 → 无需修
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
        aj = SYSTEMS_DIR / sid / str(email).replace("@", "_at_").replace(".", "_") / "agentmail.json"
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
                from datetime import datetime, timezone
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


def repair(sid: str, deep: bool = False, dry_run: bool = False) -> int:
    print(f"  repair system={sid}{' [dry-run]' if dry_run else ''}{' [deep]' if deep else ''}")
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
    for c in still:
        _warn(f"复检仍 ✗ {c['level']}/{c['check']}: {c['detail'][:90]}")
    _warn("复检未全绿——逐项核对上方提示(含平台适配层/外部状态等不可自动修复项)")
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
        # 权威解析:直接复用 agentmail CLI 的 resolve_system_id(无扩展名 →
        # 显式 SourceFileLoader;仅取函数,CLI main 有 __main__ 守卫不执行)
        from importlib.machinery import SourceFileLoader
        loader = SourceFileLoader("agentmail_cli", str(SCRIPTS_DIR / "agentmail"))
        import importlib.util
        spec = importlib.util.spec_from_loader("agentmail_cli", loader)
        if spec is None:
            print("  无法加载 agentmail CLI 模块(system_id 解析失败)")
            return 1
        mod = importlib.util.module_from_spec(spec)
        loader.exec_module(mod)
        sid, _platform = mod.resolve_system_id(
            Path(args.home).expanduser() if args.home else Path(), "")
    if not sid:
        print("  无法确定 system_id(--system-id)")
        return 1
    return repair(sid, deep=args.deep, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main() or 0)
