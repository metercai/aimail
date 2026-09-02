#!/usr/bin/env python3
"""aimail.install — SDK 自足安装/卸载入口(进程命令契约)。

架构(2026-09-02 定稿):CLI 是运维工具,不携带资源/patch;每个 SDK
(pysdk / tssdk 平台包)自带资源与平台 patch,并提供可被 spawn 的安装
入口 —— CLI(或用户)只需执行:

    python -m aimail.install    --type hermes   [--home ~/.hermes] [--system-id SID]
    python -m aimail.install    --type deerflow [--home <backend>] [--system-id SID]
    python -m aimail.uninstall  --type hermes   [--home ~/.hermes] [--system-id SID]
    python -m aimail.install    --check-env --type hermes [--home ...]

所有动作幂等;环境自检失败时明确提示"先运行 agentmail CLI"。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

# ── 双形态自举:本文件位于 core 目录(pysdk/ 或 site-packages/aimail/)──
# flat core(aimail_base.py…)与 hermes/ deer-flow/ 子模块都在此目录下。
_CORE = os.path.dirname(os.path.abspath(__file__))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from _resources_release import release_all_systems, release_resources  # noqa: E402


def _import_hermes(name: str):
    """import hermes 子模块,兼容 pip(aimail.hermes.X)与 repo(hermes.X)。"""
    try:
        return __import__(f"aimail.hermes.{name}", fromlist=["*"])
    except ImportError:
        return __import__(f"hermes.{name}", fromlist=["*"])


def _import_deerflow(name: str):
    try:
        return __import__(f"aimail.deer-flow.{name}", fromlist=["*"])
    except ImportError:
        return __import__(f"deer-flow.{name}", fromlist=["*"])


# ═══════════════════════════════════════════════════════════════
# 环境自检(加载/安装前置:配置不到位 → 明确指引先跑 CLI)
# ═══════════════════════════════════════════════════════════════

def env_check_hermes(hermes_dir: str) -> int:
    """Hermes 自检:webhook.py/profiles.py 存在、pip aimail 可达。"""
    problems = []
    ha = os.path.join(hermes_dir, "hermes-agent")
    webhook_py = os.path.join(ha, "gateway", "platforms", "webhook.py")
    profiles_py = os.path.join(ha, "hermes_cli", "profiles.py")
    if not os.path.isfile(webhook_py):
        problems.append(
            f"webhook.py 缺失:{webhook_py}(hermes 未安装?--home 指向?)\n"
            f"  先运行: agentmail install --home {hermes_dir}")
    if not os.path.isfile(profiles_py):
        candidates = [os.path.join(ha, "cli", "profiles.py")]
        if not any(os.path.isfile(p) for p in candidates):
            problems.append(f"profiles.py 缺失(未打 profile 钩子所需文件)")
    # 配置绑定:系统目录/指针是否已由 CLI 建立
    ptr = os.path.join(os.path.expanduser("~"), ".hermes", ".agentmail")
    if not os.path.isfile(ptr):
        problems.append(
            "未找到绑定配置 ~/.hermes/.agentmail\n"
            "  先运行: agentmail install(激活系统并建立绑定)")
    for p in problems:
        print(f"[env-check] ✗ {p}")
    if problems:
        print("[env-check] 缺失项需 agentmail CLI 先行完成环境配置")
        return 1
    print("[env-check] hermes: OK")
    return 0


def env_check_deerflow(backend_dir: str) -> int:
    app_py = os.path.join(backend_dir, "app", "gateway", "app.py")
    if not os.path.isfile(app_py):
        print(
            f"[env-check] ✗ 未找到 deer-flow 入口 {app_py}(--home 应为 backend 目录)\n"
            "  先运行: agentmail install(配置环境)")
        return 1
    print("[env-check] deerflow: OK")
    return 0


# ═══════════════════════════════════════════════════════════════
# install
# ═══════════════════════════════════════════════════════════════

def install_hermes(hermes_dir: str, system_id: str = "") -> int:
    """Hermes 平台自足安装:pip 运行时已装(本命令即来自 pip aimail);
    webhook/profiles/toolsets 补丁 + profile 注册 + board 资源展开。"""
    ha = os.path.join(hermes_dir, "hermes-agent")
    webhook_py = os.path.join(ha, "gateway", "platforms", "webhook.py")
    profiles_py = os.path.join(ha, "hermes_cli", "profiles.py")
    if not os.path.isfile(profiles_py):
        alt = os.path.join(ha, "cli", "profiles.py")
        if os.path.isfile(alt):
            profiles_py = alt
    rc = 0

    ph = _import_hermes("patch_webhook")
    if os.path.isfile(webhook_py):
        changed = ph.patch_webhook(webhook_py)
        print(f"  hermes webhook patch: {'applied' if changed else 'already clean'}")
    else:
        print(f"  ✗ webhook.py 缺失:{webhook_py}(--home 应为 hermes 根)")
        rc = 1

    pp = _import_hermes("patch_profiles")
    if os.path.isfile(profiles_py):
        changed = pp.patch_profiles(profiles_py)
        print(f"  hermes profiles patch: {'applied' if changed else 'already clean'}")
    else:
        print(f"  ✗ profiles.py 缺失:{profiles_py}")
        rc = 1

    try:
        pt = _import_hermes("toolsets")
        changed = pt.patch_toolsets(ha)
        print(f"  hermes toolsets: {'registered' if changed else 'already registered'}")
    except Exception as e:  # noqa: BLE001
        print(f"  toolsets patch skipped: {e}")

    # profile 注册(读 env:HERMES_DIR/SYSTEM_ID/HERMES_PROFILES_DIR)
    env = dict(os.environ)
    env.setdefault("HERMES_DIR", hermes_dir)
    if system_id:
        env["SYSTEM_ID"] = system_id
    try:
        rp = _import_hermes("register_profiles")
        # register_profiles 读环境变量;直接函数调用需要它读 env —— 以
        # subprocess 复跑本模块的注册子命令,保证 env 契约一致
        _spawn_self(["register-profiles"], env=env)
    except Exception as e:  # noqa: BLE001
        print(f"  register profiles failed: {e}")

    # board 资源展开(幂等;覆盖全部已有 system 目录)
    rel = release_all_systems(os.path.join(_CORE, "resources", "board"))
    for r in rel:
        print(f"  resources: {r['board_dir']} (copied {r['copied']}, kept {r['skipped']})")

    # skills 展开(SKILL.md/DESCRIPTION.md → 每个 hermes profile 的 skills/agentmail)
    _release_hermes_skills(hermes_dir)

    print("  hermes install done. 重启 hermes gateway 使补丁生效(agentmail bridge restart 或宿主重启)")
    return rc


def _release_hermes_skills(hermes_dir: str) -> int:
    """SKILL.md + DESCRIPTION.md → {home}/profiles/*/skills/agentmail/(幂等)。"""
    skills_src = os.path.join(_CORE, "resources", "skills")
    if not os.path.isdir(skills_src):
        return 0
    profiles_root = os.path.join(hermes_dir, "profiles")
    targets = [hermes_dir]  # 默认 profile 根
    if os.path.isdir(profiles_root):
        targets += [os.path.join(profiles_root, d) for d in sorted(os.listdir(profiles_root))
                    if os.path.isdir(os.path.join(profiles_root, d))]
    n = 0
    for prof in targets:
        dst_dir = os.path.join(prof, "skills", "agentmail")
        for fname in ("SKILL.md", "DESCRIPTION.md"):
            src = os.path.join(skills_src, fname)
            if not os.path.isfile(src):
                continue
            dst = os.path.join(dst_dir, fname)
            if os.path.exists(dst):
                try:
                    same = open(dst, "rb").read() == open(src, "rb").read()
                except Exception:  # noqa: BLE001
                    same = False
                if same:
                    continue
            os.makedirs(dst_dir, exist_ok=True)
            shutil.copy2(src, dst)
            n += 1
    if n:
        print(f"  hermes skills: {n} file(s) → profiles/*/skills/agentmail")
    return n


def install_deerflow(backend_dir: str, system_id: str = "", manager: str = "") -> int:
    """DeerFlow 平台自足安装:app.py patch + 运行时 bundle + 注册/对账。"""
    md = _import_deerflow("manage")
    rc = 0
    try:
        changed = md.patch_backend_app(backend_dir)
        print(f"  deerflow app.py patch: {'applied' if changed else 'already clean'}")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ app.py patch failed: {e}")
        rc = 1
    try:
        n = md.install_bundle(backend_dir)
        print(f"  deerflow bundle: {n} file(s) installed")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ bundle install failed: {e}")
        rc = 1
    # 注册(地址+路由);幂等
    try:
        if manager:
            md.register_agents(manager=manager, system_id=system_id, agent="all")
        else:
            md.reconcile(system_id=system_id)
        print("  deerflow agents registered/reconciled")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ register/reconcile failed: {e}")
        rc = 1
    rel = release_all_systems(os.path.join(_CORE, "resources", "board"))
    for r in rel:
        print(f"  resources: {r['board_dir']} (copied {r['copied']})")
    return rc


# ═══════════════════════════════════════════════════════════════
# uninstall(与 install 对称:撤销自己打的 patch + 清理)
# ═══════════════════════════════════════════════════════════════

def uninstall_hermes(hermes_dir: str, system_id: str = "") -> int:
    ha = os.path.join(hermes_dir, "hermes-agent")
    rc = 0
    # git 形态:精确还原(gateway 是 git checkout,且无其他本地改动时)
    if os.path.isdir(os.path.join(ha, ".git")):
        try:
            out = subprocess.check_output(
                ["git", "-C", str(ha), "status", "--short"], text=True, timeout=10)
            modified = [l[3:].strip() for l in out.splitlines() if l.startswith(" M")]
            allowed = {"gateway/platforms/webhook.py", "toolsets.py", "hermes_cli/profiles.py"}
            if all(m in allowed for m in modified):
                for f in modified:
                    subprocess.call(["git", "-C", str(ha), "checkout", "--", f])
                    print(f"  ✓ reverted {f} (git)")
            else:
                print("  ⚠ hermes-agent 有额外未提交改动——跳过 git 还原,请检查 agentmail 痕迹")
        except Exception as e:  # noqa: BLE001
            print(f"  git revert failed: {e}")
    else:
        # 非 git → exact-text 撤销(与 patch 插入逐字匹配)
        pw = _import_hermes("patch_webhook")
        pp = _import_hermes("patch_profiles")
        try:
            pt = _import_hermes("toolsets")
        except Exception:  # noqa: BLE001
            pt = None
        for rel, fn in (
            ("gateway/platforms/webhook.py", pw.unpatch_webhook),
            ("hermes_cli/profiles.py", pp.unpatch_profiles),
        ):
            fp = os.path.join(ha, rel)
            if os.path.exists(fp):
                fn(fp)
        if pt is not None:
            for rel in ("toolsets.py",):
                fp = os.path.join(ha, rel)
                if os.path.exists(fp):
                    pt.unpatch_toolsets(fp)
        # 清 pyc
        for cache in ("gateway/platforms/__pycache__", "hermes_cli/__pycache__",
                      "cli/__pycache__", "tools/__pycache__"):
            cd = os.path.join(ha, cache)
            if os.path.isdir(cd):
                shutil.rmtree(cd, ignore_errors=True)
    # 移除旧 tools/ 拷贝(若有)
    for rel in ("tools/aimail_tools.py", "tools/aimail_base.py",
                "tools/aimail_board.py", "tools/hermes"):
        tgt = os.path.join(ha, rel)
        if os.path.exists(tgt):
            shutil.rmtree(tgt) if os.path.isdir(tgt) else os.unlink(tgt)
            print(f"  ✓ removed {rel}")
    # 本 SDK 进程即 pip aimail——不自行卸载(宿主 venv 管理由 CLI 决定)
    print("  hermes uninstall done(本地配置/网关侧清理由 agentmail CLI 负责)")
    return rc


def uninstall_deerflow(backend_dir: str) -> int:
    md = _import_deerflow("manage")
    rc = 0
    try:
        changed = md.unpatch_backend_app(backend_dir) if hasattr(md, "unpatch_backend_app") else False
        print(f"  deerflow app.py unpatch: {changed}")
    except Exception as e:  # noqa: BLE001
        print(f"  ✗ app.py unpatch failed: {e}")
        rc = 1
    bundle_dir = os.path.join(backend_dir, "routers", "aimail")
    if os.path.isdir(bundle_dir):
        shutil.rmtree(bundle_dir, ignore_errors=True)
        print(f"  ✓ removed bundle {bundle_dir}")
    print("  deerflow uninstall done")
    return rc


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def _spawn_self(args: list, env: dict | None = None) -> int:
    return subprocess.call([sys.executable, "-m", "aimail.install", *args],
                           env=env or os.environ)


def _cmd_register_profiles(env) -> int:
    rp = _import_hermes("register_profiles")
    old = dict(os.environ)
    os.environ.update({k: v for k, v in env.items() if v is not None})
    try:
        rp.register_emails()
    finally:
        os.environ.clear()
        os.environ.update(old)
    return 0


def main(argv: list | None = None) -> int:
    ap = argparse.ArgumentParser(prog="aimail.install", description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_ins = sub.add_parser("install", help="安装(幂等)")
    p_ins.add_argument("--type", choices=["hermes", "deerflow"], required=True)
    p_ins.add_argument("--home", default="", help="宿主根:hermes=~/.hermes;deerflow=backend 目录")
    p_ins.add_argument("--system-id", default="")
    p_ins.add_argument("--manager", default="")
    p_ins.set_defaults(fn=_run_install)

    p_uni = sub.add_parser("uninstall", help="卸载(撤销自身 patch)")
    p_uni.add_argument("--type", choices=["hermes", "deerflow"], required=True)
    p_uni.add_argument("--home", default="")
    p_uni.add_argument("--system-id", default="")
    p_uni.set_defaults(fn=_run_uninstall)

    p_chk = sub.add_parser("check-env", help="环境自检")
    p_chk.add_argument("--type", choices=["hermes", "deerflow"], required=True)
    p_chk.add_argument("--home", default="")
    p_chk.set_defaults(fn=_run_check)

    p_rp = sub.add_parser("register-profiles", help="(内部)Hermes profile 注册")
    p_rp.add_argument("--home", default="")
    p_rp.add_argument("--system-id", default="")
    p_rp.set_defaults(fn=_run_regprof)

    args = ap.parse_args(argv)
    return args.fn(args)


def _run_install(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        print("✗ install 需要 --home(宿主根目录)")
        return 1
    if args.type == "hermes":
        return install_hermes(home, args.system_id)
    return install_deerflow(home, args.system_id, args.manager)


def _run_uninstall(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        print("✗ uninstall 需要 --home")
        return 1
    if args.type == "hermes":
        return uninstall_hermes(home, args.system_id)
    return uninstall_deerflow(home)


def _run_check(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        print("✗ check-env 需要 --home")
        return 1
    if args.type == "hermes":
        return env_check_hermes(home)
    return env_check_deerflow(home)


def _run_regprof(args) -> int:
    env = dict(os.environ)
    env.setdefault("HERMES_DIR", args.home or "")
    if args.system_id:
        env["SYSTEM_ID"] = args.system_id
    return _cmd_register_profiles(env)


if __name__ == "__main__":
    sys.exit(main())
