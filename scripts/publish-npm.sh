#!/usr/bin/env bash
# publish-npm.sh — publish @aimail TS packages from the pnpm monorepo.
#
# pnpm pack/publish rejects bundleDependencies under node-linker=isolated, so
# this script: (1) rewrites workspace:^ deps to concrete registry versions,
# (2) builds, (3) npm pack (bundles @aimail/* via bundleDependencies into
# node_modules/ inside the tarball), (4) npm publish with provenance, (5)
# restores package.json. Run from repo root: bash scripts/publish-npm.sh <pkg-dir> ...
#
# Owner ruling 2026-10-11 (single source + loud failures):
#   - the idempotency/skip logic lives only in this script (it was previously
#     re-copied in a step of .github/workflows/publish.yml — a dual source); the workflow only "hands over the package list".
#   - **per-package isolation**: a failure in one package no longer aborts the rest (previously set -e exited on the first failure,
#     and the release silently skipped a few packages); the failure list is summarized at the end and it exits non-zero.
#   - **visible on the registry**: evidence and warning only, never a failure criterion. The authoritative signal is the npm client exit code
#     (the server has accepted the publish); registry visibility is eventually consistent (measured up to ~10 min), so bounded polling
#     (900s) hitting the authoritative registry directly; a timeout only WARNS. Version authenticity is closed by L3 (installing the artifact from the registry).
#
# Prereqs: npm login (token in ~/.npmrc) or CI Trusted Publishing OIDC;
#          pnpm install already done; a clean git working tree (step 5 restores the manifest via git checkout).
set -uo pipefail

PKGS=("$@")
[ ${#PKGS[@]} -eq 0 ] && { echo "usage: $0 packages/mail-core [packages/mail] [packages/dsh-aimail] [packages/openclaw-aimail] [packages/pi-aimail]"; exit 1; }

# Repo-level tooling single source (owner ruling 2026-10-11): dev/release tools live only in the repo-root scripts/.
repo="$(cd "$(dirname "$0")/.." && pwd)"
root="$repo/tssdk"

jver() { python3 -c "import json;print(json.load(open('$1'))['version'])"; }
jname() { python3 -c "import json;print(json.load(open('$1'))['name'])"; }

# Full pipeline for one package. Convention: a failure must return non-zero (do not rely on set -e — the bash manual
# states that -e is ignored when a function runs inside a `||` list, so leaning on -e for error catching is unreliable). Success/already-published both return 0.
publish_one() {
  local pkg="$1" dir name ver published tgz dist_tag prov want want_tag vis _i _main git_top target link

  dir="$root/$pkg"
  name=$(jname "$dir/package.json") || { echo "  ERROR: cannot read name: $dir/package.json"; return 1; }
  ver=$(jver "$dir/package.json") || { echo "  ERROR: cannot read version: $dir/package.json"; return 1; }
  echo "═══ $name@$ver ═══"

  # 0) idempotency: version already on the registry → skip. Python-only releases
  #    share the v* tag with the npm workflow; unchanged TS versions must not burn
  #    a version number nor fail the build. --prefer-online: a stale 404 in the cache would make us
  #    re-publish a version that already exists (the registry answers E409) — the check must go over the network.
  published=$(npm view "$name@$ver" version --prefer-online 2>/dev/null || true)
  if [ -n "$published" ]; then
    echo "  already published ($name@$ver) — SKIP"
    return 0
  fi

  # 1) workspace:^ -> concrete registry range (^<dep version>). pnpm resolves
  #    workspace:^ locally, but the published manifest must carry a plain semver
  #    range npm understands — never a bare '^' or 'workspace:'.
  #    Shared script: the same step 3 reuses it for the **bundled copies** (see the deref loop below).
  python3 "$repo/scripts/rewrite_ws_deps.py" "$root" "$dir" || { echo "  ERROR: rewrite_ws_deps failed: $dir"; return 1; }

  # 2) build — explicit and **never swallow errors** (audit P0 2026-09-21: the old `>/dev/null 2>&1 || true`
  #    silently swallowed build failures, publishing the new version number with the stale lib/dist).
  #    Only packages that declare a build script build here (openclaw/pi use prepack);
  #    mail-core/mail/dsh lib/ comes from the workspace `pnpm build` (tsc -b) ⇒ here we
  #    assert the main entry is present, catching the "never built" case.
  if python3 -c "import json,sys;sys.exit(0 if 'build' in json.load(open('$dir/package.json')).get('scripts',{}) else 1)"; then
    (cd "$dir" && npm run build) || { echo "  ERROR: build failed: $dir"; return 1; }
  else
    _main=$(python3 -c "import json;print(json.load(open('$dir/package.json')).get('main',''))") || _main=""
    if [ -n "$_main" ] && [ ! -f "$dir/$_main" ]; then
      echo "  ERROR: main entry missing: $dir/$_main — run the workspace 'pnpm build' first"; return 1
    fi
    echo "  no build script — the workspace tsc -b output $_main is in place"
  fi

  # 3) pack (npm, not pnpm — isolated linker blocks bundled deps).
  #    Dereference workspace symlinks first: bundled @aimail/* dirs are pnpm
  #    symlinks into the monorepo; npm pack follows them but also expands sibling
  #    node_modules producing ../ tar entries npm drops.
  for link in "$dir"/node_modules/@aimail/*; do
    [ -L "$link" ] || continue
    target=$(readlink -f "$link" 2>/dev/null) || { echo "  ERROR: dangling symlink, skipping: $link"; continue; }
    echo "  deref: $(basename "$link") -> ${target#"$root"/}"
    rm "$link"
    mkdir -p "$link"
    (cd "$target" && tar cf - --exclude=node_modules .) | (cd "$link" && tar xf -) \
      || { echo "  ERROR: deref copy failed: $link"; return 1; }
    # The bundled copies' manifests must also have their workspace: specs rewritten — otherwise the release tarball's
    # embedded package.json keeps "workspace:^" (measured 2026-09-21: the embedded @aimail/mail in pi-aimail /
    # openclaw-aimail carried workspace:^ ⇒ pnpm-based hosts hit
    # EUNSUPPORTEDPROTOCOL). The deref result is the workspace original and must be treated like the main manifest.
    python3 "$repo/scripts/rewrite_ws_deps.py" "$root" "$link" \
      || { echo "  ERROR: rewrite_ws_deps failed (bundled): $link"; return 1; }
  done
  echo "  building+packing:"
  tgz=$(cd "$dir" && npm pack --pack-destination /tmp | tail -1) || { echo "  ERROR: npm pack failed: $dir"; return 1; }
  [ -n "$tgz" ] || { echo "  ERROR: npm pack produced no tarball name: $dir"; return 1; }
  # Registry rejects tarballs carrying hard links (E415 "Hard link is not
  # allowed"). npm pack re-bundles transitive deps of bundledDependencies
  # (e.g. typebox, whose published tree itself contains hard links under
  # pnpm installs) as literal tar link entries. Normalize: rewrite the
  # tarball expanding every hard/sym link to a regular file copy.
  python3 - "/tmp/$tgz" <<'PYEOF' || { echo "  ERROR: hard-link normalization failed: /tmp/$tgz"; return 1; }
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
  "$repo/tests/sdk/release/l2-check-tarball.sh" "/tmp/$tgz" "$ver" || { echo "  ERROR: L2 tarball gate failed: /tmp/$tgz"; return 1; }

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
  # Audit D8: turn the release-type declared by workflow_dispatch into a real gate (it previously existed only in the UI,
  # and a selected rc/stable was silently ignored — a declaration/deduction mismatch now fails immediately).
  want="${REQUIRE_RELEASE_TYPE:-}"
  want_tag=""
  case "$want" in
    '')      ;;
    stable)  want_tag="latest" ;;
    rc)      want_tag="rc" ;;
    *) echo "  ERROR: unknown REQUIRE_RELEASE_TYPE='$want' (expect rc|stable)"; return 1 ;;
  esac
  if [ -n "$want_tag" ] && [ "$want_tag" != "$dist_tag" ]; then
    echo "  ERROR: release-type mismatch — declared '$want' but version $ver deduces '$dist_tag'"
    return 1
  fi
  if [ "${DRY_RUN:-0}" = "1" ]; then
    npm publish --dry-run "/tmp/$tgz" --access public --tag "$dist_tag" $prov || true
  else
    npm publish "/tmp/$tgz" --access public --tag "$dist_tag" $prov || { echo "  ERROR: npm publish failed: $name@$ver"; return 1; }
    echo "  published $name@$ver ($dist_tag)"
  fi

  # 5) restore package.json from git. In the monorepo tssdk/ is not itself a
  #    git root — resolve the top-level repo and check out the relative path.
  git_top=$(git -C "$root" rev-parse --show-toplevel 2>/dev/null || true)
  if [ -n "$git_top" ]; then
    git -C "$git_top" checkout -- "${root#$git_top/}/$pkg/package.json" \
      || { echo "  ERROR: failed to restore package.json: $pkg — the working tree was left dirty, handle manually"; return 1; }
  else
    (cd "$root" && git checkout -- "$pkg/package.json") \
      || { echo "  ERROR: failed to restore package.json: $pkg — the working tree was left dirty, handle manually"; return 1; }
  fi
  echo "  package.json restored"

  # 6) visible on the registry (skipped under DRY_RUN) — visibility is **eventually consistent**, never a failure criterion:
  #    · authoritative signal = the npm client exit code (the server has accepted the publish request, already judged in the earlier steps);
  #    · visibility = a direct query to the authoritative registry's **version endpoint** (with a cache-buster). `npm view` can hit
  #      client/edge caches (a stale 404 or a stale hit); only the direct query is the truth.
  #    Measured 2026-10-11: dsh-aimail@0.1.51 was visible <90s after the client reported published; whereas
  #    pi-aimail@0.1.51 — the client reported published at 13:42:03Z, the registry was only visible at 13:52:17Z
  #    (≈10 min of server-side processing) — the 300s window at the time judged a **success** as a failure.
  #    ⇒ the window was widened to 900s; still invisible only WARNs, never FAILs. Version authenticity is closed by downstream L3
  #      (L3 installs the published artifact from the registry; if it cannot, that is a true red).
  if [ "${DRY_RUN:-0}" != "1" ]; then
    vis=""; _code=""
    for _i in $(seq 1 90); do
      _code="$(curl -s -o /dev/null -w '%{http_code}' "https://registry.npmjs.org/$name/$ver?t=$(date +%s%N)" || true)"
      [ "$_code" = "200" ] && { vis=1; break; }
      sleep 10
    done
    if [ -z "$vis" ]; then
      echo "  WARN: $name@$ver still not served by the authoritative registry within 900s (last HTTP $_code)"
      echo "        the client already reported published ⇒ the server may still be processing (npm-side eventual consistency), not judged as a failure"
      echo "        verify: curl -s 'https://registry.npmjs.org/$name/$ver?t=\$(date +%s%N)' -o /dev/null -w '%{http_code}\\n'"
      echo "        authenticity is closed by L3: install the published artifact from the registry, and a failed install is a true red"
    else
      echo "  registry visible: $name@$ver (HTTP $_code)"
    fi
  fi
  return 0
}

fail_pkgs=()
for pkg in "${PKGS[@]}"; do
  publish_one "$pkg" || fail_pkgs+=("$pkg")
done

if [ ${#fail_pkgs[@]} -gt 0 ]; then
  echo "═══ publish-npm.sh: FAILED: ${fail_pkgs[*]} ═══"
  exit 1
fi
echo "═══ publish-npm.sh: OK — ${#PKGS[@]} package(s) handled ═══"
