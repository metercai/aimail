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

  # 1) workspace:^ -> concrete version (bundle carries the real code; the
  #    manifest version just needs to be resolvable for npm dedupe logic)
  python3 - "$dir" <<'PYEOF'
import json, sys, re
d = sys.argv[1]
p = f"{d}/package.json"
raw = open(p).read()
data = json.loads(raw)
changed = False
for dep_group in ("dependencies", "peerDependencies", "devDependencies"):
    deps = data.get(dep_group) or {}
    for k, v in list(deps.items()):
        if isinstance(v, str) and v.startswith("workspace:"):
            deps[k] = re.sub(r"^workspace:", "", v)
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
    target=$(readlink -f "$link")
    rm "$link"
    mkdir -p "$link"
    (cd "$target" && tar cf - --exclude=node_modules .) | (cd "$link" && tar xf -)
  done
  tgz=$(cd "$dir" && npm pack --pack-destination /tmp 2>/dev/null | tail -1)
  echo "  packed: /tmp/$tgz"

  # 4) publish
  # --provenance requires CI OIDC; local publishes disable it explicitly
  # (package publishConfig.provenance would otherwise force OIDC lookup).
  prov="--provenance=false"
  [ -n "${CI:-}" ] && prov="--provenance"
  if [ "${DRY_RUN:-0}" = "1" ]; then
    npm publish --dry-run "/tmp/$tgz" --access public --tag rc $prov || true
  else
    # npm >= 11.5 refuses implicit-latest prerelease publishes: tag `rc`,
    # then move `latest` onto the new version explicitly.
    npm publish "/tmp/$tgz" --access public --tag rc $prov
    npm dist-tag add "$name@$ver" latest
    echo "  published $name@$ver (latest)"
  fi

  # 5) restore package.json from git
  (cd "$root" && git checkout -- "$pkg/package.json")
  echo "  package.json restored"
done
