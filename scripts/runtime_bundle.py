#!/usr/bin/env python3
"""runtime_bundle — 运行时捆绑安装/校验助手(P1a 单一实现)。

背景: 运行时载荷已打包为 aimail Python 包(pip 渠道);各平台 provisioner
不再把仓库 tools/ 的绝对路径写进宿主配置,而是把自包含捆绑(bundle)拷贝到
平台安装位置,并落版本戳。运行时入口经 _aimail_bootstrap 从捆绑目录自举,
与仓库路径完全解耦(仓库改名/mv 不影响已部署运行时)。

源解析顺序(单一真源 = pip 包 > 仓库 tools/):
  1. `import aimail` 成功 → site-packages/aimail(= 发布载荷)
  2. 兜底:本仓库 tools/(dev 模式,未 pip install 时)

bundle 定义(源相对路径 → 捆绑内相对路径):
  hermes     核心4 + bootstrap + hermes/aimail_hermes.py   (扁平+子目录)
  mcp        核心4 + bootstrap + amail_mcp_server.py          (扁平)
  openclaw   核心4 + bootstrap + openclaw/{amail,amail_base,amail_openclaw_bridge}.py
  deer-flow  核心4 + bootstrap + router + 适配层,全扁平铺进宿主 routers/
             (宿主 app.py 经 `from .routers import aimail_inbound` 加载;
              router/适配层/core 同目录,bootstrap case-3 自举,零 env)

用法:
  runtime_bundle.py install <bundle> [--dest DIR] [--source-root DIR] [--force]
  runtime_bundle.py check   <bundle> [--dest DIR] [--source-root DIR]
  runtime_bundle.py source                    # 打印当前解析到的源根+类型
  bundle ∈ hermes|mcp|openclaw|deer-flow|skill-hermes|skill-openclaw|skill-deerflow|skill-dsh

退出码: install 0=完成(全部一致或已更新); check 0=一致,1=漂移/缺失,2=未安装。
check 输出机器可读行: DRIFT <file> / MISSING <file> / STALE-STAMP / OK <n files>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
from datetime import datetime, timezone

STAMP_NAME = ".aimail-runtime.json"

# 核心 4 + bootstrap(所有 bundle 共享)
_CORE_FILES = {
    "aimail_base.py": "aimail_base.py",
    "aimail_tools.py": "aimail_tools.py",
    "aimail_board.py": "aimail_board.py",
    "gateway_api.py": "gateway_api.py",
    "_aimail_bootstrap.py": "_aimail_bootstrap.py",
}

BUNDLES = {
    "hermes": {
        "default_dest": "~/.hermes/hermes-agent/tools",
        "files": dict(_CORE_FILES, **{"hermes/aimail_hermes.py": "hermes/aimail_hermes.py"}),
    },
    "mcp": {
        "default_dest": "~/.agentmail/mcp",
        "files": dict(_CORE_FILES, **{"amail_mcp_server.py": "amail_mcp_server.py"}),
    },
    "openclaw": {
        "default_dest": "~/.agentmail/openclaw",
        "files": dict(_CORE_FILES, **{
            "openclaw/amail.py": "openclaw/amail.py",
            "openclaw/amail_base.py": "openclaw/amail_base.py",
            "openclaw/amail_openclaw_bridge.py": "openclaw/amail_openclaw_bridge.py",
        }),
    },
    "deer-flow": {
        # 宿主 app.py 经 `from .routers import aimail_inbound` 加载 router,
        # router 与 core 必须同目录(bootstrap case-3)。目标铺平进宿主 routers/。
        "default_dest": "~/deer-flow/backend/app/gateway/routers",
        "files": dict(_CORE_FILES, **{
            "deer-flow/aimail_inbound.py": "aimail_inbound.py",
            "deer-flow/amail_base.py": "amail_base.py",
        }),
    },
    # skill bundles:纯 md 资源,无 bootstrap 需求,单独定义(无核心)
    "skill-hermes": {
        "default_dest": "__profile_skills__",  # provisioner 自管目标(逐 profile)
        "files": {"skills/SKILL.md": "SKILL.md", "skills/DESCRIPTION.md": "DESCRIPTION.md"},
        "no_stamp": True,
    },
    "skill-openclaw": {
        "default_dest": "~/.openclaw/skills/agentmail",
        "files": {"skills/SKILL.md": "SKILL.md", "skills/DESCRIPTION.md": "DESCRIPTION.md"},
        "no_stamp": True,
    },
    "skill-deerflow": {
        "default_dest": "~/deer-flow/skills/public/agentmail",
        "files": {"skills/SKILL.md": "SKILL.md", "skills/DESCRIPTION.md": "DESCRIPTION.md"},
        "no_stamp": True,
    },
    "skill-dsh": {
        "default_dest": "~/.dsh/skills/agentmail",
        "files": {"skills/SKILL.md": "SKILL.md"},
        "no_stamp": True,
    },
}

# CLI 声明的载荷最低版本(版本漂移防护:捆绑戳版本低于此 → WARN)
MIN_PAYLOAD_VERSION = "0.1.0"


def _md5(path: str) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_source_root(explicit: str = "") -> tuple[str, str]:
    """返回 (源根目录, 类型)。类型 ∈ pip|repo。"""
    if explicit:
        root = os.path.abspath(os.path.expanduser(explicit))
        kind = "repo" if os.path.isfile(os.path.join(root, "aimail_base.py")) else ""
        if not kind:
            raise SystemExit(f"ERROR: --source-root 无效(无 aimail_base.py): {root}")
        return root, kind
    # 1) pip 包
    try:
        import aimail  # type: ignore
        root = os.path.abspath(os.path.dirname(os.path.dirname(os.path.abspath(aimail.__file__))))
        # aimail.__file__ = site-packages/aimail/__init__.py → 包目录即载荷根
        pkg_dir = os.path.dirname(aimail.__file__)
        if os.path.isfile(os.path.join(pkg_dir, "aimail_base.py")):
            return pkg_dir, "pip"
    except Exception:
        pass
    # 2) 仓库 tools/(本文件在 scripts/ 下)
    root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
    if os.path.isfile(os.path.join(root, "aimail_base.py")):
        return root, "repo"
    raise SystemExit("ERROR: 运行时源未找到(pip aimail 未安装且仓库 tools/ 缺失)")


def _source_version(root: str, kind: str) -> str:
    if kind == "pip":
        try:
            import aimail  # type: ignore
            return getattr(aimail, "__version__", "0.0.0")
        except Exception:
            return "0.0.0"
    # repo: git describe(失败则 dev)
    try:
        out = subprocess_git_describe(root)
        return out
    except Exception:
        return "dev"


def subprocess_git_describe(root: str) -> str:
    import subprocess
    out = subprocess.check_output(
        ["git", "-C", root, "describe", "--always", "--dirty"],
        text=True, timeout=10, stderr=subprocess.DEVNULL).strip()
    return out or "dev"


def _stamp_path(dest: str) -> str:
    return os.path.join(dest, STAMP_NAME)


def install(bundle: str, dest: str = "", source_root: str = "", force: bool = False) -> int:
    spec = BUNDLES[bundle]
    root, kind = resolve_source_root(source_root)
    dest = os.path.abspath(os.path.expanduser(dest or spec["default_dest"]))
    version = _source_version(root, kind)

    changed, missing_src = [], []
    for src_rel, dst_rel in spec["files"].items():
        src = os.path.join(root, src_rel)
        dst = os.path.join(dest, dst_rel)
        if not os.path.isfile(src):
            missing_src.append(src_rel)
            continue
        need = force or not os.path.isfile(dst) or _md5(src) != _md5(dst)
        if need:
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(src, dst)
            changed.append(dst_rel)

    if missing_src:
        print(f"  ✗ {bundle}: 源缺失 {missing_src}(源根 {root})")
        return 1

    if not spec.get("no_stamp"):
        stamp = {
            "bundle": bundle,
            "version": version,
            "source": kind,
            "installed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "min_version": MIN_PAYLOAD_VERSION,
            "files": {rel: _md5(os.path.join(dest, rel))
                      for rel in spec["files"].values() if os.path.isfile(os.path.join(dest, rel))},
        }
        tmp = _stamp_path(dest) + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(stamp, f, indent=2, ensure_ascii=False)
        os.replace(tmp, _stamp_path(dest))

    if changed:
        print(f"  ✓ {bundle}: 更新 {len(changed)} 文件 → {dest} (v{version}, {kind})")
    else:
        print(f"  ✓ {bundle}: 已一致(跳过)→ {dest} (v{version}, {kind})")
    return 0


def check(bundle: str, dest: str = "", source_root: str = "") -> int:
    spec = BUNDLES[bundle]
    root, kind = resolve_source_root(source_root)
    dest = os.path.abspath(os.path.expanduser(dest or spec["default_dest"]))

    if not os.path.isdir(dest):
        print(f"NOT-INSTALLED {bundle} {dest}")
        return 2

    problems = 0
    for src_rel, dst_rel in spec["files"].items():
        src = os.path.join(root, src_rel)
        dst = os.path.join(dest, dst_rel)
        if not os.path.isfile(dst):
            print(f"MISSING {dst_rel}")
            problems += 1
            continue
        if os.path.isfile(src) and _md5(src) != _md5(dst):
            print(f"DRIFT {dst_rel}")
            problems += 1

    # 版本戳校验(漂移检测:静默失效 → WARN)
    if not spec.get("no_stamp"):
        sp = _stamp_path(dest)
        if not os.path.isfile(sp):
            print("STALE-STAMP (无版本戳)")
            problems += 1
        else:
            try:
                stamp = json.loads(open(sp, encoding="utf-8").read())
                v = stamp.get("version", "")
                if _lt(v, MIN_PAYLOAD_VERSION):
                    print(f"LOW-VERSION {v} < {MIN_PAYLOAD_VERSION}")
                    problems += 1
            except Exception:
                print("STALE-STAMP (损坏)")
                problems += 1

    if problems == 0:
        print(f"OK {bundle} ({len(spec['files'])} files, src={kind})")
        return 0
    return 1


def _lt(a: str, b: str) -> bool:
    """semver-ish 比较(仅数字段);非数字 → False(不报警)。"""
    def parts(v):
        try:
            return [int(x) for x in v.split("-")[0].split("+")[0].split(".") if x.isdigit()]
        except Exception:
            return []
    pa, pb = parts(a), parts(b)
    if not pa or not pb:
        return False
    for x, y in zip(pa, pb):
        if x != y:
            return x < y
    return len(pa) < len(pb)


def _resource_path(name: str, root: str, kind: str) -> str:
    """资源目录绝对路径(pip 包内 / 仓库内布局不同)。"""
    if name == "skills":
        return os.path.join(root, "skills") if kind == "pip" else os.path.join(root, "..", "skills")
    if name == "board-role":
        # pip: 包内 board_role_prompt_en/;repo: <repo>/board/role_prompt_en(root=tools/,上 1 级)
        return os.path.join(root, "board_role_prompt_en") if kind == "pip" else os.path.join(root, "..", "board", "role_prompt_en")
    raise SystemExit(f"ERROR: 未知资源 {name}(可选: skills|board-role)")


def source_path(name: str, explicit_root: str = "") -> str:
    root, kind = resolve_source_root(explicit_root)
    p = os.path.normpath(_resource_path(name, root, kind))
    if not os.path.isdir(p):
        raise SystemExit(f"ERROR: 资源目录不存在: {p}")
    return p


def main() -> int:
    ap = argparse.ArgumentParser(description="aimail 运行时捆绑安装/校验")
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("install", "check"):
        p = sub.add_parser(name)
        p.add_argument("bundle", choices=sorted(BUNDLES))
        p.add_argument("--dest", default="")
        p.add_argument("--source-root", default="")
        if name == "install":
            p.add_argument("--force", action="store_true")
    sub.add_parser("source")
    p_res = sub.add_parser("resource")
    p_res.add_argument("name", choices=["skills", "board-role"])
    p_res.add_argument("--source-root", default="")

    args = ap.parse_args()
    if args.cmd == "source":
        root, kind = resolve_source_root(args.source_root if hasattr(args, "source_root") else "")
        print(f"{kind}\t{root}")
        return 0
    if args.cmd == "resource":
        print(source_path(args.name, args.source_root))
        return 0
    if args.cmd == "install":
        return install(args.bundle, args.dest, args.source_root, args.force)
    return check(args.bundle, args.dest, args.source_root)


if __name__ == "__main__":
    sys.exit(main())
