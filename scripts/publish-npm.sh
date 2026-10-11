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
#   - 幂等/skip 逻辑只在本脚本一处（原先 .github/workflows/publish.yml 的 step 里
#     重抄了一份，双真源）；workflow 只负责"给包目录清单"。
#   - **逐包隔离**：某个包失败不再中断其余包（此前 set -e 让首个失败即退出，
#     发布会静默少发几个包）；末尾汇总失败清单并非零退出。
#   - **上架可查**：判据是 registry 终态，不是客户端自报。npm 处理 tarball 需要时间，
#     期间 `npm view <pkg>@<ver>` 仍为 404（实测数分钟）；本脚本有界轮询到可见为止。
#
# Prereqs: npm login (token in ~/.npmrc) 或 CI 的 Trusted Publishing OIDC;
#          pnpm install 已完成; git 工作树干净（第 5 步用 git checkout 还原 manifest）。
set -uo pipefail

PKGS=("$@")
[ ${#PKGS[@]} -eq 0 ] && { echo "usage: $0 packages/mail-core [packages/mail] [packages/dsh-aimail] [packages/openclaw-aimail] [packages/pi-aimail]"; exit 1; }

# 仓级工具单源(owner 裁决 2026-10-11): 开发/发版工具只在仓根 scripts/ 一处。
repo="$(cd "$(dirname "$0")/.." && pwd)"
root="$repo/tssdk"

jver() { python3 -c "import json;print(json.load(open('$1'))['version'])"; }
jname() { python3 -c "import json;print(json.load(open('$1'))['name'])"; }

# 单包全流程。约定：失败必须 return 非零（不依赖 set -e —— 函数在 `||` 列表里执行时
# bash 手册明确 -e 会被忽略，靠 -e 兜错不可靠）。成功/已发布均 return 0。
publish_one() {
  local pkg="$1" dir name ver published tgz dist_tag prov want want_tag vis _i _main git_top target link

  dir="$root/$pkg"
  name=$(jname "$dir/package.json") || { echo "  ERROR: 读不到 name: $dir/package.json"; return 1; }
  ver=$(jver "$dir/package.json") || { echo "  ERROR: 读不到 version: $dir/package.json"; return 1; }
  echo "═══ $name@$ver ═══"

  # 0) idempotency: version already on the registry → skip. Python-only releases
  #    share the v* tag with the npm workflow; unchanged TS versions must not burn
  #    a version number nor fail the build. --prefer-online: 缓存里的陈旧 404 会让我们
  #    去重发一个已存在的版本（registry 回 E409）—— 判据必须走线上。
  published=$(npm view "$name@$ver" version --prefer-online 2>/dev/null || true)
  if [ -n "$published" ]; then
    echo "  already published ($name@$ver) — SKIP"
    return 0
  fi

  # 1) workspace:^ -> concrete registry range (^<dep version>). pnpm resolves
  #    workspace:^ locally, but the published manifest must carry a plain semver
  #    range npm understands — never a bare '^' or 'workspace:'.
  #    共享脚本: 同一步骤 3 也对 **bundled 副本**复用(见下 deref 循环)。
  python3 "$repo/scripts/rewrite_ws_deps.py" "$root" "$dir" || { echo "  ERROR: rewrite_ws_deps 失败: $dir"; return 1; }

  # 2) build — 显式且**绝不吞错**（审计 P0 2026-09-21: 原 `>/dev/null 2>&1 || true`
  #    会把构建失败静默吞掉, 用旧 lib/dist 带新版本号发上 registry）。
  #    只有声明了 build script 的包在此构建(openclaw/pi 走 prepack);
  #    mail-core/mail/dsh 的 lib/ 由工作区 `pnpm build`(tsc -b) 产出 ⇒ 这里
  #    断言 main 入口在位, 兜住"从未构建"。
  if python3 -c "import json,sys;sys.exit(0 if 'build' in json.load(open('$dir/package.json')).get('scripts',{}) else 1)"; then
    (cd "$dir" && npm run build) || { echo "  ERROR: build failed: $dir"; return 1; }
  else
    _main=$(python3 -c "import json;print(json.load(open('$dir/package.json')).get('main',''))") || _main=""
    if [ -n "$_main" ] && [ ! -f "$dir/$_main" ]; then
      echo "  ERROR: main entry missing: $dir/$_main — 先跑工作区 'pnpm build'"; return 1
    fi
    echo "  no build script — 工作区 tsc -b 产出的 $_main 在位"
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
    # bundled 副本的 manifest 也必须重写 workspace: 规格 —— 否则发布 tarball 的
    # 内嵌 package.json 遗留 "workspace:^"(2026-09-21 实测: pi-aimail /
    # openclaw-aimail 内嵌的 @aimail/mail 带 workspace:^ ⇒ pnpm 系宿主
    # EUNSUPPORTEDPROTOCOL)。deref 出来的是工作区原样, 须与主 manifest 同待遇。
    python3 "$repo/scripts/rewrite_ws_deps.py" "$root" "$link" \
      || { echo "  ERROR: rewrite_ws_deps 失败(bundled): $link"; return 1; }
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
  # 审计 D8: 把 workflow_dispatch 声明的 release-type 变成真门禁(原来只在 UI 上,
  # 选中 rc/stable 被静默忽略 —— 声明与推导不一致时直接失败)。
  want="${REQUIRE_RELEASE_TYPE:-}"
  want_tag=""
  case "$want" in
    '')      ;;
    stable)  want_tag="latest" ;;
    rc)      want_tag="rc" ;;
    *) echo "  ERROR: unknown REQUIRE_RELEASE_TYPE='$want' (expect rc|stable)"; return 1 ;;
  esac
  if [ -n "$want_tag" ] && [ "$want_tag" != "$dist_tag" ]; then
    echo "  ERROR: release-type mismatch — 声明 '$want' 但版本 $ver 推导为 '$dist_tag'"
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
      || { echo "  ERROR: package.json 还原失败: $pkg —— 工作树被留脏, 请人工处理"; return 1; }
  else
    (cd "$root" && git checkout -- "$pkg/package.json") \
      || { echo "  ERROR: package.json 还原失败: $pkg —— 工作树被留脏, 请人工处理"; return 1; }
  fi
  echo "  package.json restored"

  # 6) 上架可查(DRY_RUN 跳过): 客户端报 "published" 不等于 registry 已可见。
  if [ "${DRY_RUN:-0}" != "1" ]; then
    vis=""
    for _i in $(seq 1 18); do
      vis=$(npm view "$name@$ver" version --prefer-online 2>/dev/null || true)
      [ -n "$vis" ] && break
      sleep 10
    done
    if [ -z "$vis" ]; then
      echo "  ERROR: $name@$ver 在 180s 内未在 registry 可见 —— 发布不算完成"; return 1
    fi
    echo "  registry 可见: $name@$ver"
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
