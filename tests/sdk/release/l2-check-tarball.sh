#!/usr/bin/env bash
# Release gate L2 — inspect ONE npm tarball before publish.
# Usage: check-tarball.sh <tgz-path> <expected-version>
# Checks:
#   1. no hard-link entries (registry E415 "Hard link is not allowed")
#   2. no symlink entries
#   3. manifest inside has no "workspace:" dep spec
#   4. version == expected
#   5. main/types/bin targets exist inside the tarball
#   6. bundled packages (openclaw-aimail/pi-aimail): declared
#      bundleDependencies exist under package/node_modules/
#   7. tarball is non-empty (catches silent npm pack failures)
set -euo pipefail
TGZ="${1:?usage: check-tarball.sh <tgz> <expected-version>}"
EXPECT="${2:?usage: check-tarball.sh <tgz> <expected-version>}"
[ -f "$TGZ" ] || { echo "[L2] FAIL: tarball missing: $TGZ"; exit 1; }

LINKS=$(tar tvf "$TGZ" 2>/dev/null | grep -c ' link to ' || true)
SYMS=$(tar tvf "$TGZ" 2>/dev/null | grep -c ' -> ' || true)
FILES=$(tar tvf "$TGZ" 2>/dev/null | grep -c '^-' || true)

[ "$FILES" -gt 0 ] || { echo "[L2] FAIL: empty tarball (pack failed silently?)"; exit 1; }
[ "$LINKS" -eq 0 ] || { echo "[L2] FAIL: $LINKS hard-link entries (registry rejects E415)"; exit 1; }
[ "$SYMS" -eq 0 ] || { echo "[L2] FAIL: $SYMS symlink entries"; exit 1; }
echo "[L2] ok: $FILES regular files, 0 hard/sym links"

MANIFEST=$(tar xOzf "$TGZ" package/package.json 2>/dev/null) || { echo "[L2] FAIL: package/package.json missing"; exit 1; }
# 顶端 + **所有内嵌** manifest 都不得含 workspace: 规格。
# (2026-09-21 实测盲点: 原实现只查 package/package.json ⇒ pi-aimail/openclaw-aimail
#  的 bundled 副本 package/node_modules/@aimail/mail/package.json 遗留
#  "workspace:^" 也照样 PASS, 而 pnpm 系宿主安装即 EUNSUPPORTEDPROTOCOL。)
LEAKS=$(tar xOzf "$TGZ" --wildcards 'package/**/package.json' 'package/package.json' 2>/dev/null | grep -c 'workspace:' || true)
if [ "${LEAKS:-0}" -gt 0 ]; then
  echo "[L2] FAIL: 'workspace:' leaked into published manifest(s) — 含内嵌 bundled 副本"
  tar tzf "$TGZ" | grep 'package.json$' | while read -r m; do
    if tar xOzf "$TGZ" "$m" 2>/dev/null | grep -q 'workspace:'; then echo "        泄漏: $m"; fi
  done
  exit 1
fi
echo "[L2] ok: no 'workspace:' in any manifest (top-level + bundled)"
VER=$(echo "$MANIFEST" | python3 -c "import json,sys;print(json.load(sys.stdin)['version'])")
[ "$VER" = "$EXPECT" ] || { echo "[L2] FAIL: manifest version $VER != expected $EXPECT"; exit 1; }
echo "[L2] ok: version $VER"

# 5) main/types/bin presence
python3 - "$TGZ" "$MANIFEST" <<'PYEOF' || { echo "[L2] FAIL: dangling main/types/bin refs"; exit 1; }
import sys, tarfile, json, posixpath
names = tarfile.open(sys.argv[1], "r:gz").getnames()
m = json.loads(sys.argv[2])
for key in ("main", "types", "bin"):
    v = m.get(key)
    if not v:
        continue
    targets = v.values() if isinstance(v, dict) else [v]
    for t in targets:
        if not isinstance(t, str):
            continue
        t = t.lstrip("./")
        hit = any(n == f"package/{t}" or n.startswith(f"package/{t}/") or n.startswith(f"package/node_modules/{t}") for n in names)
        if not hit:
            print(f"[L2] FAIL: {key} target {t} not in tarball")
            sys.exit(1)
PYEOF
echo "[L2] ok: main/types/bin resolve"

# 6) bundled deps present
#    (审计 P1 2026-09-21: 原来尾部 `|| true` 把失败吞掉 —— 会先打印 FAIL 再打印
#     PASS 且 exit 0。bundleDependencies 缺失正是 E415 事故同族的打包回归。)
if python3 - "$TGZ" <<'PYEOF'
import sys, tarfile, json
t = tarfile.open(sys.argv[1], "r:gz")
names = set(t.getnames())
m = json.loads(t.extractfile("package/package.json").read())
bundle = m.get("bundleDependencies") or m.get("bundledDependencies") or []
missing = [b for b in bundle if f"package/node_modules/{b}/package.json" not in names]
if missing:
    print(f"[L2] FAIL: bundleDependencies missing in tarball: {missing}")
    sys.exit(1)
if bundle:
    print(f"[L2] ok: bundled deps present: {', '.join(bundle)}")
PYEOF
then
  :
else
  echo "[L2] FAIL: bundled-deps check failed — 声明了 bundleDependencies 但 tarball 内缺失(见上方 FAIL 行)"
  exit 1
fi

# 8) resources 指纹 == 仓库根真源(2026-09-25 单一真源改造)
#    分发点(dsh/openclaw/pi)的 resources/** 由 prepack 从仓根 resources/ 物化而来。
#    这条兜住"prepack 没跑 / 物化过期 / 只改了一份"⇒ 空或旧资源包**发不出去**。
#    (宿主侧 release 现在也会响亮报错, 但那时包已发布 → 必须在产物层拦。)
#    只对声明了 resources 的包生效(mail-core/mail 不带资源 → 跳过)。
GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$GATE_DIR/../.." && pwd)"
if python3 - "$TGZ" "$REPO_ROOT" <<'PYEOF'
import hashlib, json, pathlib, sys, tarfile

tgz, repo = sys.argv[1], pathlib.Path(sys.argv[2])
canon_dir = repo / "resources"
with tarfile.open(tgz, "r:gz") as t:
    man = json.loads(t.extractfile("package/package.json").read())
    ships = any(str(f).startswith("resources") for f in (man.get("files") or []))
    if not ships:
        print("[L2] skip: package does not ship resources")
        sys.exit(0)
    canon = {str(p.relative_to(canon_dir)): hashlib.sha256(p.read_bytes()).hexdigest()
             for p in canon_dir.rglob("*") if p.is_file()}
    inpk = {n.split("package/resources/", 1)[1]: hashlib.sha256(t.extractfile(n).read()).hexdigest()
            for n in t.getnames()
            if n.startswith("package/resources/") and t.getmember(n).isfile()}
missing = sorted(set(canon) - set(inpk))
extra = sorted(set(inpk) - set(canon))
diff = sorted(k for k in set(canon) & set(inpk) if canon[k] != inpk[k])
if missing or extra or diff:
    print(f"[L2] FAIL: resources 与真源不一致: missing={missing} extra={extra} diff={diff}")
    sys.exit(1)
print(f"[L2] ok: resources == repo canonical ({len(canon)} files)")
PYEOF
then
  :
else
  echo "[L2] FAIL: resources 指纹校验失败(prepack 未跑 / 物化过期 / 只改了一份)"
  exit 1
fi

echo "[L2] PASS: $TGZ ($VER)"
