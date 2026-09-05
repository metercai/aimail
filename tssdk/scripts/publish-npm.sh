#!/usr/bin/env bash
# publish-npm.sh — publish @aimail TS packages from the pnpm monorepo.
#
# pnpm pack/publish rejects bundleDependencies under node-linker=isolated, so
# this script: (1) rewrites workspace:^ deps to concrete registry versions,
# (2) builds, (3) npm pack (bundles @aimail/* via bundleDependencies into
# node_modules/ inside the tarball), (4) npm publish with provenance, (5)
# restores package.json. Run from repo root: bash scripts/publish-npm.sh <pkg-dir> ...
#
# Prereqs: npm login (token in ~/.npmrc), pnpm install done, clean git tree.
set -euo pipefail

PKGS=("$@")
[ ${#PKGS[@]} -eq 0 ] && { echo "usage: $0 packages/mail-core [packages/mail] [packages/dsh-aimail] [packages/openclaw-aimail] [packages/pi-aimail]"; exit 1; }

root="$(cd "$(dirname "$0")/.." && pwd)"
ver_of() { python3 -c "import json;print(json.load(open('$1/package.json'))['version'])"; }

for pkg in "${PKGS[@]}"; do
  dir="$root/$pkg"
  name=$(python3 -c "import json;print(json.load(open('$dir/package.json'))['name'])")
  ver=$(ver_of "$dir")
  echo "═══ $name@$ver ═══"

  # 1) workspace:^ -> concrete registry range (^<dep version>). pnpm
  #    resolves workspace:^ locally, but the published manifest must carry a
  #    plain semver range npm understands — never a bare '^' or 'workspace:'.
  python3 - "$root" "$dir" <<'PYEOF'
import json, sys, re, os
root, d = sys.argv[1], sys.argv[2]
p = f"{d}/package.json"
raw = open(p).read()
data = json.loads(raw)

# name -> version map of every workspace package (for range rewriting)
ws_versions = {}
ws_root = os.path.join(root, "packages")
if not os.path.isdir(ws_root):
    ws_root = root  # standalone repo: packages live at the root level
for sub in os.listdir(ws_root):
    sub_pkg = os.path.join(ws_root, sub, "package.json")
    if os.path.isfile(sub_pkg):
        try:
            m = json.load(open(sub_pkg))
            if "name" in m and "version" in m:
                ws_versions[m["name"]] = m["version"]
        except Exception:
            pass

changed = False
for dep_group in ("dependencies", "peerDependencies", "devDependencies"):
    deps = data.get(dep_group) or {}
    for k, v in list(deps.items()):
        if not isinstance(v, str) or not v.startswith("workspace:"):
            continue
        spec = v[len("workspace:"):]
        ver = ws_versions.get(k)
        if ver is None:
            raise SystemExit(f"ERROR: workspace dep {k} not found in {root}/packages")
        # workspace:^x.y.z / workspace:~x.y.z -> ^x.y.z / ~x.y.z; bare
        # workspace:^ / ~ / * -> prefix + concrete version
        if spec in ("", "*"):
            deps[k] = ver
        elif spec in ("^", "~"):
            deps[k] = spec + ver
        else:
            deps[k] = spec  # already a full range/version
        changed = True
if changed:
    open(p, "w").write(json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    print("  deps rewritten to concrete versions")
PYEOF

  # 2) build (prepack runs build via npm lifecycle; keep explicit for clarity)
  (cd "$dir" && npm run build >/dev/null 2>&1 || true)

  # 3) pack (npm, not pnpm — isolated linker blocks bundled deps).
  #    Dereference workspace symlinks first: bundled @aimail/* dirs are
  #    pnpm symlinks into the monorepo; npm pack follows them but also
  #    expands sibling node_modules producing ../ tar entries npm drops.
  for link in "$dir"/node_modules/@aimail/*; do
    [ -L "$link" ] || continue
    target=$(readlink -f "$link" 2>/dev/null) || { echo "  ERROR: dangling symlink, skipping: $link"; continue; }
    echo "  deref: $(basename "$link") -> ${target#"$root"/}"
    rm "$link"
    mkdir -p "$link"
    (cd "$target" && tar cf - --exclude=node_modules .) | (cd "$link" && tar xf -)
  done
  echo "  building+packing:"
  tgz=$(cd "$dir" && npm pack --pack-destination /tmp | tail -1)
  # Registry rejects tarballs carrying hard links (E415 "Hard link is not
  # allowed"). npm pack re-bundles transitive deps of bundledDependencies
  # (e.g. typebox, whose published tree itself contains hard links under
  # pnpm installs) as literal tar link entries. Normalize: rewrite the
  # tarball expanding every hard/sym link to a regular file copy.
  python3 - "/tmp/$tgz" <<'PYEOF'
import sys, tarfile, io, os
name = sys.argv[1]
src = tarfile.open(name, "r:gz")
members = src.getmembers()
contents: dict[str, bytes] = {}
for m in members:
    if m.isfile():
        f = src.extractfile(m)
        contents[m.name] = f.read() if f else b""
out_name = name + ".plain"
out = tarfile.open(out_name, "w:gz")
for m in members:
    if m.islnk() or m.issym():
        data = contents.get(m.linkname)
        if data is None:
            continue  # unresolvable link: drop the member
        nm = tarfile.TarInfo(m.name)
        nm.size = len(data)
        nm.mode = m.mode or 0o644
        nm.mtime = m.mtime
        out.addfile(nm, io.BytesIO(data))
    else:
        data = contents.get(m.name)
        out.addfile(m, io.BytesIO(data) if data is not None else None)
out.close()
src.close()
os.replace(out_name, name)
print("  normalized hard links")
PYEOF
  echo "  packed: /tmp/$tgz"

  # 4) publish
  # --provenance requires CI OIDC; local publishes disable it explicitly
  # (package publishConfig.provenance would otherwise force OIDC lookup).
  prov="--provenance=false"
  [ -n "${CI:-}" ] && prov="--provenance"
  if [ "${DRY_RUN:-0}" = "1" ]; then
    npm publish --dry-run "/tmp/$tgz" --access public --tag latest $prov || true
  else
    # --tag latest = explicit form of the default: the published version
    # lands on `latest` directly (no dist-tag step, no token needed —
    # OIDC covers publish; dist-tag writes would need a classic token).
    npm publish "/tmp/$tgz" --access public --tag latest $prov
    echo "  published $name@$ver (latest)"
  fi

  # 5) restore package.json from git. In the monorepo tssdk/ is not itself a
  #    git root — resolve the top-level repo and check out the relative path.
  git_top=$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)
  if [ -n "$git_top" ]; then
    git -C "$git_top" checkout -- "${root#$git_top/}/$pkg/package.json"
  else
    (cd "$root" && git checkout -- "$pkg/package.json")
  fi
  echo "  package.json restored"
done
