#!/usr/bin/env python3
"""aimail.install — SDK 自足安装/卸载入口(进程命令契约)。

架构(2026-09-02 定稿):CLI 是运维工具,不携带资源/patch;每个 SDK
(pysdk / tssdk 平台包)自带资源与平台 patch,并提供可被 spawn 的安装
入口 —— CLI(或用户)只需执行:

    python -m aimail.install install            --type hermes   [--home ~/.hermes] [--system-id SID]
    python -m aimail.install install            --type deerflow [--home <backend>] [--system-id SID]
    python -m aimail.install uninstall          --type hermes   [--home ~/.hermes] [--system-id SID]
    python -m aimail.install check-env          --type hermes   [--home ...]
    python -m aimail.install register-profiles  --home <hermes-root> [--system-id SID]  # internal

所有动作幂等;环境自检失败时明确提示"先运行 aimail CLI"。
"""
from __future__ import annotations

import sys


def _say(*a, **k):
    """SDK 进度/诊断输出一律走 stderr：调用方（CLI）以 stdout 为单行 JSON 协议。"""
    print(*a, file=sys.stderr, **k)

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ── 双形态自举:本文件位于 core 目录(pysdk/ 或 site-packages/aimail/)──
# flat core(aimail_base.py…)与 hermes/ deer-flow/ 子模块都在此目录下。
_CORE = os.path.dirname(os.path.abspath(__file__))
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from _resources_release import agentmail_home, release_all_systems  # noqa: E402


def _import_hermes(name: str):
    """import hermes 子模块 —— **同源优先**:先取与本 install.py 同一棵树的
    `hermes/<name>.py`(`_CORE` 已插在 sys.path 头),才回退包名 `aimail.hermes.<name>`。

    2026-09-30 P2 取证:原顺序(包名优先)在"CLI 自举把源码快照到
    `~/.aimail/bin/aimail-src/pysdk/` + 镜像里 pip 装着旧 aimailsdk(0.1.30)"
    的形态下取到**另一棵树**的旧实现(iso15 原文):
        File ".../aimail-src/pysdk/install.py", line 135, in hermes_webhook_anchor_gate
          gaps = list(ph.webhook_patch_gaps(webhook_py))
        AttributeError: module 'aimail.hermes.patch_webhook' has no attribute 'webhook_patch_gaps'
    ⇒ install.py 是新快照、patch_webhook 是 pip 旧包 = 混装,新增锚闸直接炸
    (rc≠0),后续 J 段全部缺前置成 GAP。**调用方与被调方必须同一棵树**;
    pip 形态下两路指向同一文件,换序无副作用。
    """
    try:
        return __import__(f"hermes.{name}", fromlist=["*"])
    except ImportError:
        return __import__(f"aimail.hermes.{name}", fromlist=["*"])


def _import_deerflow(name: str):
    """同 `_import_hermes`:同源优先(`deer-flow/<name>.py` 与本文件同树)。"""
    try:
        return __import__(f"deer-flow.{name}", fromlist=["*"])
    except ImportError:
        return __import__(f"aimail.deer-flow.{name}", fromlist=["*"])


# ═══════════════════════════════════════════════════════════════
# 环境自检(加载/安装前置:配置不到位 → 明确指引先跑 CLI)
# ═══════════════════════════════════════════════════════════════

def _machine_state() -> tuple:
    """机器环境三态:
    ('empty', msg)   — ~/.aimail/systems 无任何系统配置 → init + install
    ('systems', msg) — 有系统配置(可 install 复用/激活)
    """
    systems_root = os.path.join(agentmail_home(), "systems")
    if not os.path.isdir(systems_root) or not os.listdir(systems_root):
        return ("empty",
                "未发现任何系统配置(~/.aimail/systems 空)。\n\n"
                "  运行:   aimail install --home <宿主根>(激活系统并绑定)")
    return ("systems", "")


def env_check_hermes(hermes_dir: str) -> int:
    """Hermes 自检:webhook.py/profiles.py 存在、pip aimail 可达。"""
    state, hint = _machine_state()
    problems = []
    ha = os.path.join(hermes_dir, "hermes-agent")
    if not os.path.isdir(ha):
        _say("[env-check] ✗ hermes-agent 未安装(hermes 宿主缺失或 --home 指向错误)")
        _say(f"  先安装 hermes 宿主,再: aimail install --home {hermes_dir}")
        return 1
    webhook_py = os.path.join(ha, "gateway", "platforms", "webhook.py")
    profiles_py = os.path.join(ha, "hermes_cli", "profiles.py")
    if not os.path.isfile(webhook_py):
        problems.append(f"webhook.py 缺失:{webhook_py}")
    if not os.path.isfile(profiles_py):
        if not os.path.isfile(os.path.join(ha, "cli", "profiles.py")):
            problems.append("profiles.py 缺失")
    if state == "empty":
        for p in problems:
            _say(f"[env-check] ✗ {p}")
        _say(f"[env-check] ✗ {hint}")
        return 1
    # 配置绑定:系统目录/指针是否已由 CLI 建立(--home 参数化,AUDIT-1 P2-1)
    ptr = os.path.join(hermes_dir, ".agentmail")
    if not os.path.isfile(ptr):
        problems.append(
            f"未找到绑定配置 {hermes_dir}/.agentmail\n"
            f"  先运行: aimail install --home {hermes_dir}(激活并绑定)")
    for p in problems:
        _say(f"[env-check] ✗ {p}")
    if problems:
        _say("[env-check] 缺失项需 aimail CLI 先行完成环境配置")
        return 1
    _say("[env-check] hermes: OK")
    return 0


def env_check_deerflow(backend_dir: str) -> int:
    state, hint = _machine_state()
    app_py = os.path.join(backend_dir, "app", "gateway", "app.py")
    if not os.path.isfile(app_py):
        _say(
            f"[env-check] ✗ 未找到 deer-flow 入口 {app_py}(--home 应为 backend 目录)")
        if state == "empty":
            _say(f"[env-check] ✗ {hint}")
        else:
            _say("  先运行: aimail install(配置环境)")
        return 1
    if state == "empty":
        _say(f"[env-check] ✗ {hint}")
        return 1
    _say("[env-check] deerflow: OK")
    return 0


# ═══════════════════════════════════════════════════════════════
# install
# ═══════════════════════════════════════════════════════════════

# ═══════════════════════════════════════════════════════════════
# P2 关键锚硬门(owner 2026-09-30)
# ═══════════════════════════════════════════════════════════════
def hermes_webhook_anchor_gate(ph, webhook_py: str) -> int:
    """补丁后关键锚体检: 缺任一块 ⇒ rc=1 并点名缺哪块;齐锚 ⇒ rc=0。

    锚是 patch_webhook **打进去**的 ⇒ 必须在 patch_webhook 之后量;只看它的
    返回值不够 —— 返回值只说"有没有写文件"(幂等重跑恒 False), 锚在不在才是硬门。
    """
    gaps = list(ph.webhook_patch_gaps(webhook_py))
    if gaps:
        _say(f"  ✗ webhook 关键锚缺失:{', '.join(gaps)} — {webhook_py}")
        _say("      (缺任一块 ⇒ preprocessor 钩子/适配器挂不上, 该补丁不算已打好;"
              "宿主 webhook.py 版本与锚点不符, 修锚后重跑 install)")
        return 1
    return 0


def install_hermes(hermes_dir: str, system_id: str = "") -> int:
    """Hermes 平台自足安装:pip 运行时已装(本命令即来自 pip aimailsdk);
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
        # P2(owner 2026-09-30): 关键锚缺任一块 ⇒ rc≠0 且点名缺哪块;齐锚(含幂等
        # "已打好")⇒ rc=0 —— 硬门见 hermes_webhook_anchor_gate()。
        if hermes_webhook_anchor_gate(ph, webhook_py):
            rc = 1
        else:
            _say(f"  hermes webhook patch: {'applied' if changed else 'already clean'}")
    else:
        _say(f"  ✗ webhook.py 缺失:{webhook_py}(--home 应为 hermes 根)")
        rc = 1

    pp = _import_hermes("patch_profiles")
    if os.path.isfile(profiles_py):
        changed = pp.patch_profiles(profiles_py)
        _say(f"  hermes profiles patch: {'applied' if changed else 'already clean'}")
    else:
        _say(f"  ✗ profiles.py 缺失:{profiles_py}")
        rc = 1

    try:
        pt = _import_hermes("toolsets")
        changed = pt.patch_toolsets(ha)
        _say(f"  hermes toolsets: {'registered' if changed else 'already registered'}")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
        _say(f"  ✗ toolsets patch failed: {e}")
        rc = 1

    # profile 注册(读 env:HERMES_HOME/SYSTEM_ID/HERMES_PROFILES_DIR)——直接
    # 函数调用(Rust CLI 终态走 spawn python -m aimail.install register-profiles,
    # 同 env 契约;repo 形态无 aimail 包名,不能 spawn)
    try:
        rp = _import_hermes("register_profiles")
        saved = {k: os.environ.get(k) for k in ("HERMES_HOME", "HERMES_PROFILES_DIR", "SYSTEM_ID")}
        os.environ["HERMES_HOME"] = hermes_dir
        os.environ["HERMES_PROFILES_DIR"] = os.path.join(hermes_dir, "profiles")
        if system_id:
            os.environ["SYSTEM_ID"] = system_id
        try:
            rp.register_emails()
        finally:
            for k, v in saved.items():  # restore only the keys we touched
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
        _say(f"  ✗ register profiles failed: {e}")
        rc = 1

    # board 资源展开(幂等;覆盖全部已有 system 目录)
    rel = release_all_systems(os.path.join(_CORE, "resources", "board"))
    for r in rel:
        _say(f"  resources: {r['board_dir']} (copied {r['copied']}, kept {r['skipped']})")

    # skills 展开(SKILL.md/DESCRIPTION.md → 每个 hermes profile 的 skills/agentmail)
    _release_hermes_skills(hermes_dir)

    _say("  hermes install done. 重启 hermes gateway 使补丁生效(aimail bridge restart 或宿主重启)")
    return rc


def _release_hermes_skills(hermes_dir: str) -> int:
    """SKILL.md + DESCRIPTION.md → {home}/profiles/*/skills/agentmail/(幂等)。"""
    skills_src = os.path.join(_CORE, "resources", "skills")
    if not os.path.isdir(skills_src):
        # 不再静默 return 0: 包自带资源缺失 = 打包/物化缺陷, 必须当场可见
        raise RuntimeError(
            f"skills 资源缺失: {skills_src}(仓库态: 跑 scripts/materialize-resources.sh;"
            f" pip 态: 重装 aimailsdk)")
    missing = [f for f in ("SKILL.md", "DESCRIPTION.md")
               if not os.path.isfile(os.path.join(skills_src, f))]
    if missing:
        raise RuntimeError(f"skills 资源不完整: {skills_src} 缺 {missing}")
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
        _say(f"  hermes skills: {n} file(s) → profiles/*/skills/agentmail")
    return n


def _deerflow_backend(root: str) -> str:
    """backend 目录归一:仓根含 backend/(标准布局)→ backend;否则视为已 backend。"""
    r = os.path.expanduser(root)
    return os.path.join(r, "backend") if os.path.isdir(os.path.join(r, "backend")) else r


def _assembly_env(system_id: str = "", extra_env: dict | None = None) -> dict:
    """装配步(install-skill.sh / install-mcp.sh)的 spawn 环境。

    两脚本按契约走 CLI 公开命令面(缺 `aimail` ⇒ exit 1, 不兜底),而仓库/容器
    形态里 CLI 常不在 PATH(镜像内 `command -v aimail` 必空)⇒ 这里把**同一棵树**
    的 `cli/` 补到 PATH 头;pip 布局没有该目录 ⇒ 保持环境 PATH(真缺 CLI 时
    脚本自己响亮失败 —— 不回退、不静默)。
    """
    env = dict(os.environ)
    if shutil.which("aimail", path=env.get("PATH")) is None:
        cli_dir = os.path.abspath(os.path.join(_CORE, os.pardir, "cli"))
        if os.path.isfile(os.path.join(cli_dir, "aimail")):
            env["PATH"] = cli_dir + os.pathsep + env.get("PATH", "")
    if system_id:
        env.setdefault("AIMAIL_SYSTEM_ID", system_id)
    for k, v in (extra_env or {}).items():
        env.setdefault(k, v)
    return env


def _assemble_deerflow(home_root: str, system_id: str = "") -> int:
    """deerflow agent 侧装配:skills 落点 + MCP toolset 落点(幂等)。

    **复用产品既有脚本** `pysdk/deer-flow/install-skill.sh` / `install-mcp.sh`
    的解析与写入逻辑 —— SDK 不自持第二套写入;两脚本自身幂等(同内容跳过/覆盖),
    重复 install 不产生重复写。
    `DEER_FLOW_PROJECT_ROOT` 只在环境未声明时补成平台 home(沿用 install-mcp.sh
    落点序的 ③「运维已断言项目根 ⇒ 在其中新建」),让落点同时是 deer-flow
    `resolve_config_path()` **与** `get_skills_path()` 真会读的位置 ——
    否则写进状态目录/会话不读的目录等于没写(2026-09-30: skills 落点错即此)。
    任一脚本 rc!=0 ⇒ 返回 1(装配失败必须可见, 不静默降级)。
    """
    sdk = os.path.join(_CORE, "deer-flow")
    # 两步给**同一份** env: skills 与 MCP 的落点都必须是 deer-flow 真会读的
    # project_root(get_skills_path ③ / resolve_config_path ③)—— 少传一步 =
    # 那一步写进状态目录 = 会话读不到(2026-09-30 实测 skills 根因)。
    asm_env = _assembly_env(system_id, {"DEER_FLOW_PROJECT_ROOT": home_root})
    steps = (
        ("install-skill.sh", asm_env),
        ("install-mcp.sh", asm_env),
    )
    rc = 0
    for name, env in steps:
        path = os.path.join(sdk, name)
        if not os.path.isfile(path):
            _say(f"  ✗ deerflow 装配脚本缺失: {path}(打包/物化缺陷, 不静默跳过)")
            rc = 1
            continue
        r = subprocess.call(["bash", path], env=env, stdout=sys.stderr)
        if r != 0:
            _say(f"  ✗ deerflow {name} 装配失败(exit {r})")
            rc = 1
        else:
            _say(f"  deerflow {name}: assembled")
    return rc


def install_deerflow(backend_dir: str, system_id: str = "", manager: str = "") -> int:
    """DeerFlow 平台自足安装:app.py patch + 运行时 bundle + agent 侧装配 + 注册/对账。
    参数为仓根或 backend(backend 归一在 SDK 内——平台布局是适配知识)。"""
    home_root = os.path.expanduser(backend_dir)  # 平台 home(装配落点序的 ③ 用)
    backend_dir = _deerflow_backend(backend_dir)
    md = _import_deerflow("manage")
    rc = 0
    try:
        changed = md.patch_backend_app(backend_dir)
        _say(f"  deerflow app.py patch: {'applied' if changed else 'already clean'}")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
        _say(f"  ✗ app.py patch failed: {e}")
        rc = 1
    try:
        n = md.install_bundle(backend_dir)
        _say(f"  deerflow bundle: {n} file(s) installed")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
        _say(f"  ✗ bundle install failed: {e}")
        rc = 1
    # agent 侧装配(卡①(i), owner 批 2026-09-30): skills + MCP toolset 落点。
    # 纯 SDK 入口原来只做 patch/bundle/register ⇒ harness 只调 SDK 入口时
    # toolset 永远缺;这里把它并进安装链(复用既有脚本, 见 _assemble_deerflow)。
    if _assemble_deerflow(home_root, system_id=system_id):
        rc = 1
    # 注册(地址+路由);幂等
    try:
        if manager:
            md.register_agents(manager=manager, system_id=system_id, agent="all")
        else:
            md.reconcile(system_id=system_id)
        _say("  deerflow agents registered/reconciled")
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
        _say(f"  ✗ register/reconcile failed: {e}")
        rc = 1
    rel = release_all_systems(os.path.join(_CORE, "resources", "board"))
    for r in rel:
        _say(f"  resources: {r['board_dir']} (copied {r['copied']})")
    return rc


# ═══════════════════════════════════════════════════════════════
# uninstall(与 install 对称:撤销自己打的 patch + 清理)
# ═══════════════════════════════════════════════════════════════

def install_dsh(home: str, system_id: str = "", manager: str = "") -> int:
    """dsh agent 侧装配(卡②, owner 批 2026-09-30): web profile 的 skill/tool 暴露层。

    归属按 owner 边界裁决(2026-09-30): agent 内部工具/技能的暴露属 **SDK 范围** ⇒ 必须由
    本入口执行(harness 只调 SDK 入口)。按同一裁决, **agent 适配步骤本身不属 CLI** ——
    `cli/platforms.json` 的 dsh 装配步已迁移(D5, 2026-10-02): `kind=sdk_install,
    fn=install_dsh`, CLI 经 `_sdk_install` 调本入口, 不再自行 spawn 适配脚本。
    复用既有
    `pysdk/dsh/install-skill-tools.sh`(幂等); rc!=0 ⇒ 返回 1(装配失败必须可见)。
    `dsh plugin add` 属 CLI 安装步, 不在此重复(不越界、不造第二套)。
    `manager` 仅保持与 install_deerflow 同签名(dsh 注册在 CLI 面, F6 家族)。
    """
    env = _assembly_env(system_id, {"DSH_HOME": home, "DSH_PROFILE": "web"})
    path = os.path.join(_CORE, "dsh", "install-skill-tools.sh")
    if not os.path.isfile(path):
        _say(f"  ✗ dsh 装配脚本缺失: {path}(打包/物化缺陷, 不静默跳过)")
        return 1
    r = subprocess.call(["bash", path], env=env, stdout=sys.stderr)
    if r != 0:
        _say(f"  ✗ dsh install-skill-tools.sh 装配失败(exit {r})")
        return 1
    _say("  dsh skill/tool exposure: assembled")
    return 0


def uninstall_hermes(hermes_dir: str, system_id: str = "") -> int:
    ha = os.path.join(hermes_dir, "hermes-agent")
    rc = 0
    # git 形态:精确还原(gateway 是 git checkout,且无其他本地改动时)
    if os.path.isdir(os.path.join(ha, ".git")):
        try:
            out = subprocess.check_output(
                ["git", "-C", str(ha), "status", "--short"], text=True, timeout=10)
            modified = [l[3:].strip() for l in out.splitlines() if l.startswith(" M")]
            allowed = {"gateway/platforms/webhook.py", "toolsets.py",
                       "hermes_cli/profiles.py", "cli/profiles.py"}
            if all(m in allowed for m in modified):
                for f in modified:
                    subprocess.call(["git", "-C", str(ha), "checkout", "--", f], stdout=sys.stderr)
                    _say(f"  ✓ reverted {f} (git)")
            else:
                _say("  ⚠ hermes-agent 有额外未提交改动——跳过 git 还原,请检查 aimail 痕迹")
        except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
            _say(f"  git revert failed: {e}")
    else:
        # 非 git → exact-text 撤销(与 patch 插入逐字匹配)
        pw = _import_hermes("patch_webhook")
        pp = _import_hermes("patch_profiles")
        try:
            pt = _import_hermes("toolsets")
        except Exception:  # noqa: BLE001
            pt = None
        # 与 install 的路径探测同一逻辑:profiles.py 可能在 hermes_cli/ 或 cli/(AUDIT-1 P2-3)
        profiles_rel = "hermes_cli/profiles.py"
        alt = os.path.join(ha, "cli", "profiles.py")
        if not os.path.exists(os.path.join(ha, profiles_rel)) and os.path.exists(alt):
            profiles_rel = "cli/profiles.py"
        for rel, fn in (
            ("gateway/platforms/webhook.py", pw.unpatch_webhook),
            (profiles_rel, pp.unpatch_profiles),
        ):
            fp = os.path.join(ha, rel)
            if os.path.exists(fp):
                fn(Path(fp))
        if pt is not None:
            for rel in ("toolsets.py",):
                fp = os.path.join(ha, rel)
                if os.path.exists(fp):
                    pt.unpatch_toolsets(Path(fp))
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
            _say(f"  ✓ removed {rel}")
    # 本 SDK 进程即 pip aimail——不自行卸载(宿主 venv 管理由 CLI 决定)
    # profile 级状态撤销(与 install 的注册链/skills 发布对称——曾由 CLI
    # _uninstall_hermes 持有,平台边界收口迁 SDK):
    #   指针(sid 匹配)/skills/agentmail 目录/config.yaml toolsets 条目/
    #   webhook_subscriptions 路由(含根 profile=hermes_dir,install 的
    #   release targets 也含根——对称)
    _uninstall_hermes_profiles(hermes_dir, system_id)
    _say("  hermes uninstall done(本地配置/网关侧清理由 aimail CLI 负责)")
    return rc


def _uninstall_hermes_profiles(hermes_dir: str, system_id: str) -> None:
    """撤销各 profile(含根)的 aimail 状态:pointer/skills/toolsets 条目/路由。"""
    targets = [hermes_dir]
    profiles_root = os.path.join(hermes_dir, "profiles")
    if os.path.isdir(profiles_root):
        targets += [os.path.join(profiles_root, d) for d in sorted(os.listdir(profiles_root))
                    if os.path.isdir(os.path.join(profiles_root, d))]
    for prof in targets:
        ptr = os.path.join(prof, ".agentmail")
        if os.path.isfile(ptr):
            try:
                with open(ptr) as f:
                    data = json.load(f)
                if data.get("system_id") == system_id:
                    os.unlink(ptr)
                    _say(f"  ✓ removed pointer {ptr}")
            except Exception:  # noqa: BLE001
                pass
        sk = os.path.join(prof, "skills", "agentmail")
        if os.path.isdir(sk):
            shutil.rmtree(sk, ignore_errors=True)
            _say(f"  ✓ removed skill {sk}")
        # config.yaml:platform_toolsets 移除 agentmail 条目(终态单名)
        cfg = os.path.join(prof, "config.yaml")
        if os.path.isfile(cfg):
            try:
                with open(cfg) as f:
                    content = f.read()
                new = re.sub(r"^[ \t]*-[ \t]*agentmail[ \t]*\r?$\n?", "", content, flags=re.M)
                if new != content:
                    with open(cfg, "w") as f:
                        f.write(new)
                    _say(f"  ✓ config toolset cleaned {cfg}")
            except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
                _say(f"  ⚠ config.yaml clean failed: {e}")
        # webhook 订阅路由(aimail-inbound,终态单名)
        subs = os.path.join(prof, "webhook_subscriptions.json")
        if os.path.isfile(subs):
            try:
                with open(subs) as f:
                    data = json.load(f)
                removed = [rn for rn in ("aimail-inbound",)
                           if rn in (data if isinstance(data, dict) else {})]
                if removed:
                    for rn in removed:
                        del data[rn]
                    with open(subs, "w") as f:
                        json.dump(data, f, indent=2, ensure_ascii=False)
                    _say(f"  ✓ webhook route removed {subs}")
            except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
                _say(f"  ⚠ webhook_subscriptions clean failed: {e}")


def uninstall_deerflow(backend_dir: str, system_id: str = "") -> int:
    """DeerFlow SDK 卸载:还原 app.py patch + 删运行时 bundle。
    参数为仓根或 backend(归一在 SDK 内)。

    system_id: 由注册表驱动调用传入。registry 的 sdk_uninstall 步按
    `fn(home, sid)` 位置调用(见 cli/aimail `_sdk_uninstall`),与
    `uninstall_hermes(hermes_dir, system_id="")` 对齐;本函数不消费它
    (deer-flow 的运行时产物都落在 backend 目录下,不按 sid 分)。F9 2026-09-25:
    原签名只收 1 个位置参数 ⇒ `aimail uninstall` 在 deerflow 上抛 TypeError,
    网关侧注销整段被跳过、夹具零残留门禁判红。"""
    backend_dir = _deerflow_backend(backend_dir)
    md = _import_deerflow("manage")
    rc = 0
    # 先还原 app.py(否则删 bundle 后宿主重启 import 失败——AUDIT-1 P1-4)
    try:
        md.unpatch_backend_app(backend_dir)
    except (Exception, SystemExit) as e:  # noqa: BLE001 — manage.py 失败路径 raise SystemExit
        _say(f"  ✗ app.py unpatch failed: {e}")
        rc = 1
    bundle_dir = os.path.join(backend_dir, "routers", "aimail")
    if os.path.isdir(bundle_dir):
        shutil.rmtree(bundle_dir, ignore_errors=True)
        _say(f"  ✓ removed bundle {bundle_dir}")
    if rc == 0:
        _say("  deerflow uninstall done")
    return rc


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

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
    p_ins.add_argument("--type", choices=["hermes", "deerflow", "dsh"], required=True)
    p_ins.add_argument("--home", default="", help="宿主根:hermes=~/.hermes;deerflow=backend 目录;dsh=DSH home")
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
        _say("✗ install 需要 --home(宿主根目录)")
        return 1
    if args.type == "hermes":
        return install_hermes(home, args.system_id)
    if args.type == "dsh":
        return install_dsh(home, args.system_id, args.manager)
    return install_deerflow(home, args.system_id, args.manager)


def _run_uninstall(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        _say("✗ uninstall 需要 --home")
        return 1
    if args.type == "hermes":
        return uninstall_hermes(home, args.system_id)
    return uninstall_deerflow(home)


def _run_check(args) -> int:
    home = args.home or os.environ.get("AIMAIL_SYSTEM_HOME", "")
    if not home:
        _say("✗ check-env 需要 --home")
        return 1
    if args.type == "hermes":
        return env_check_hermes(home)
    return env_check_deerflow(home)


def _run_regprof(args) -> int:
    env = dict(os.environ)
    env.setdefault("HERMES_HOME", args.home or "")
    if args.system_id:
        env["SYSTEM_ID"] = args.system_id
    return _cmd_register_profiles(env)


if __name__ == "__main__":
    sys.exit(main())
