#!/usr/bin/env python3
"""Rewrite `workspace:` dependency specs in a manifest to concrete semver ranges.

Why a standalone script (2026-09-21): the original was inlined in publish-npm.sh step 1 and only
touched **the package's own** package.json; but step 3 derefs the @aimail/* packages pointed to by
bundledDependencies into **physical copies** whose package.json is still the workspace original ⇒
the embedded manifest in the pi-aimail/openclaw-aimail release tarball kept
`"@aimail/mail-core": "workspace:^"`, and a pnpm-based host install immediately hit
EUNSUPPORTEDPROTOCOL ("Unsupported URL Type workspace:"). So it is extracted into a script that the
main manifest and every bundled copy all pass through.

Usage: rewrite_ws_deps.py <workspace_root> <manifest_or_dir> [<manifest_or_dir> ...]
"""
import json
import os
import sys


def ws_versions(root: str) -> dict:
    """Package name → version within the workspace (used to resolve workspace:^ to ^<ver>)."""
    out: dict = {}
    ws_root = os.path.join(root, "packages")
    if not os.path.isdir(ws_root):
        ws_root = root  # standalone repo: the packages live at the root
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
            # workspace:^x.y.z / ~x.y.z → ^x.y.z / ~x.y.z; bare ^ / ~ / * → prefix + concrete version
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
