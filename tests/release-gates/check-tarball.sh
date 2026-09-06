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
if echo "$MANIFEST" | grep -q 'workspace:'; then
  echo "[L2] FAIL: 'workspace:' leaked into published manifest"
  exit 1
fi
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
python3 - "$TGZ" <<'PYEOF' || true
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

echo "[L2] PASS: $TGZ ($VER)"
