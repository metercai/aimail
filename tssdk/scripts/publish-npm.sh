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

  # 0) idempotency: version already on the registry → skip. Python-only
  #    releases share the v* tag with the npm workflow; unchanged TS
  #    versions must not burn a version number nor fail the build.
  published=$(npm view "$name@$ver" version 2>/dev/null || true)
  if [ -n "$published" ]; then
    echo "  already published ($name@$ver) — SKIP"
    continue
  fi

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

  # 2) build — 显式且**绝不吞错**(审计 P0 2026-09-21: 原 `>/dev/null 2>&1 || true`
  #    会把构建失败静默吞掉, 用旧 lib/dist 带新版本号发上 registry)。
  #    只有声明了 build script 的包在此构建(openclaw/pi 走 prepack);
  #    mail-core/mail/dsh 的 lib/ 由工作区 `pnpm build`(tsc -b) 产出 ⇒ 这里
  #    断言 main 入口在位, 兜住"从未构建"。
  if python3 -c "import json,sys;sys.exit(0 if 'build' in json.load(open('$dir/package.json')).get('scripts',{}) else 1)"; then
    (cd "$dir" && npm run build) || { echo "  ERROR: build failed: $dir"; exit 1; }
  else
    _main=$(python3 -c "import json;print(json.load(open('$dir/package.json')).get('main',''))")
    if [ -n "$_main" ] && [ ! -f "$dir/$_main" ]; then
      echo "  ERROR: main entry missing: $dir/$_main — 先跑工作区 'pnpm build'"; exit 1
    fi
    echo "  no build script — 工作区 tsc -b 产出的 $_main 在位"
  fi

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
  # Release gate L2: reject broken tarballs (E415 hard links, workspace:
  # leaks, dangling main/types, empty packs) BEFORE publish.
  "$root/../tests/sdk-release-gates/check-tarball.sh" "/tmp/$tgz" "$ver"

  # 4) publish
  # dist-tag by release type (version semantics v0.1.7+):
  #   stable (X.Y.Z)      -> --tag latest   (bare-name host installs resolve)
  #   rc (X.Y.Z-rc.N)     -> --tag rc       (dev iteration; latest stays on
  #                            the last stable so bare-name installs never
  #                            silently pick up a prerelease)
  # --provenance requires CI OIDC; local publishes disable it explicitly
  # (package publishConfig.provenance would otherwise force OIDC lookup).
  prov="--provenance=false"
  [ -n "${CI:-}" ] && prov="--provenance"
  if [[ "$ver" == *-rc.* ]]; then
    dist_tag="rc"
  else
    dist_tag="latest"
  fi
  echo "  release-type=$dist_tag (version $ver)"
  # 审计 D8: 把 workflow_dispatch 声明的 release-type 变成真门禁(原来只在 UI 上,
  # 选中 rc/stable 被静默忽略 —— 声明与推导不一致时直接失败)。
  want="${REQUIRE_RELEASE_TYPE:-}"
  want_tag=""
  case "$want" in
    '')      ;;
    stable)  want_tag="latest" ;;
    rc)      want_tag="rc" ;;
    *) echo "  ERROR: unknown REQUIRE_RELEASE_TYPE='$want' (expect rc|stable)"; exit 1 ;;
  esac
  if [ -n "$want_tag" ] && [ "$want_tag" != "$dist_tag" ]; then
    echo "  ERROR: release-type mismatch — 声明 '$want' 但版本 $ver 推导为 '$dist_tag'"
    exit 1
  fi
  if [ "${DRY_RUN:-0}" = "1" ]; then
    npm publish --dry-run "/tmp/$tgz" --access public --tag $dist_tag $prov || true
  else
    npm publish "/tmp/$tgz" --access public --tag $dist_tag $prov
    echo "  published $name@$ver ($dist_tag)"
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
