#!/usr/bin/env python3
"""把 manifest 里的 `workspace:` 依赖规格重写成具体 semver 范围。

为什么需要独立脚本(2026-09-21): 原实现内联在 publish-npm.sh 步骤 1, 只作用于
**包自身**的 package.json; 而步骤 3 会把 bundledDependencies 指向的 @aimail/*
从工作区 deref 成**实体副本**, 那些副本的 package.json 仍是工作区原样 ⇒
pi-aimail/openclaw-aimail 的发布 tarball 内嵌 manifest 遗留
`"@aimail/mail-core": "workspace:^"`, pnpm 系宿主安装即
EUNSUPPORTEDPROTOCOL("Unsupported URL Type workspace:")。故抽成脚本, 主 manifest
与每个 bundled 副本都过一遍。

用法: rewrite_ws_deps.py <workspace_root> <manifest_or_dir> [<manifest_or_dir> ...]
"""
import json
import os
import sys


def ws_versions(root: str) -> dict:
    """workspace 内 包名 → 版本(用于把 workspace:^ 落成 ^<ver>)。"""
    out: dict = {}
    ws_root = os.path.join(root, "packages")
    if not os.path.isdir(ws_root):
        ws_root = root  # 独立仓: 包就在根下
    for sub in sorted(os.listdir(ws_root)):
        sub_pkg = os.path.join(ws_root, sub, "package.json")
        if os.path.isfile(sub_pkg):
            try:
                m = json.load(open(sub_pkg))
                if "name" in m and "version" in m:
                    out[m["name"]] = m["version"]
            except Exception:
                pass
    return out


def rewrite(root: str, target: str) -> bool:
    p = target if target.endswith(".json") else os.path.join(target, "package.json")
    if not os.path.isfile(p):
        return False
    data = json.loads(open(p).read())
    vers = ws_versions(root)
    changed = False
    for group in ("dependencies", "peerDependencies", "devDependencies"):
        deps = data.get(group) or {}
        for k, v in list(deps.items()):
            if not isinstance(v, str) or not v.startswith("workspace:"):
                continue
            spec = v[len("workspace:"):]
            ver = vers.get(k)
            if ver is None:
                raise SystemExit(f"ERROR: workspace dep {k} not found under {root}/packages")
            # workspace:^x.y.z / ~x.y.z → ^x.y.z / ~x.y.z; 裸 ^ / ~ / * → 前缀+具体版本
            if spec in ("", "*"):
                deps[k] = ver
            elif spec in ("^", "~"):
                deps[k] = spec + ver
            else:
                deps[k] = spec
            changed = True
    if changed:
        open(p, "w").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    return changed


def main(argv: list) -> int:
    if len(argv) < 3:
        print(__doc__)
        return 2
    root, targets = argv[1], argv[2:]
    for t in targets:
        if rewrite(root, t):
            print(f"  deps rewritten to concrete versions: {t}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
