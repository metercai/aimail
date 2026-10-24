#!/usr/bin/env bash
# Release gate L2 — inspect ONE npm tarball before publish.
# Usage: check-tarball.sh <tgz-path> <expected-version>
# Checks:
#   1. no hard-link entries (registry E415 "Hard link is not allowed")
#   2. no symlink entries
#   3. manifest inside has no "workspace:" dep spec
#   4. version == expected
#   5. main/types/bin targets exist inside the tarball
#   6. bundled deps (only if package declares bundleDependencies; single package agent-aimail usually doesn't)
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
# Top + **all embedded** manifests must not contain workspace: spec.
# (2026-09-21 measured blind spot: original implementation only checked package/package.json ⇒ pi-aimail/openclaw-aimail
#  bundled copy package/node_modules/@aimail/mail/package.json leftover
#  "workspace:^" still PASS, while pnpm host install is EUNSUPPORTEDPROTOCOL.)
LEAKS=$(tar xOzf "$TGZ" --wildcards 'package/**/package.json' 'package/package.json' 2>/dev/null | grep -c 'workspace:' || true)
if [ "${LEAKS:-0}" -gt 0 ]; then
  echo "[L2] FAIL: 'workspace:' leaked into published manifest(s) — including embedded bundled copy"
  tar tzf "$TGZ" | grep 'package.json$' | while read -r m; do
    if tar xOzf "$TGZ" "$m" 2>/dev/null | grep -q 'workspace:'; then echo "        leak: $m"; fi
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
#    (Audit P1 2026-09-21: original trailing `|| true` swallowed failure —— would print FAIL then print
#     PASS and exit 0. Missing bundleDependencies is same family of packaging regression as E415 incident.)
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
  echo "[L2] FAIL: bundled-deps check failed — declared bundleDependencies but missing in tarball(see FAIL line above)"
  exit 1
fi

# 8) resources fingerprint == repo root true source (2026-09-25 single source of truth refactor)
#    Distribution points(dsh/openclaw/pi) resources/** are materialized by prepack from repo root resources/.
#    This catches "prepack not run / materialization stale / only one copy changed" ⇒ empty or stale resource package **cannot be released**.
#    (Host-side release will now also fail loudly, but by then the package is already published → must intercept at the artifact layer.)
#    Only applies to packages that declare resources (mail-core/mail do not carry resources → skip).
GATE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# Gate is at <repo>/tests/sdk/release/ (3 levels down from repo root) ⇒ climb up 3 levels to repo root.
# (Historical bug: original `../..` only climbed 2 levels = <repo>/tests ⇒ canon_dir pointed to non-existent
#  tests/resources ⇒ canon={} ⇒ all shipped resources judged as extra, packages with resources must FAIL.)
REPO_ROOT="$(cd "$GATE_DIR/../.." && cd .. && pwd)"
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
    print(f"[L2] FAIL: resources inconsistent with source of truth: missing={missing} extra={extra} diff={diff}")
    sys.exit(1)
print(f"[L2] ok: resources == repo canonical ({len(canon)} files)")
PYEOF
then
  :
else
  echo "[L2] FAIL: resources fingerprint check failed (prepack not run / materialization expired / only one copy modified)"
  exit 1
fi

# 9) Clean project installability —— peer resolution feasibility(last check before publish)
#    2026-10-23 measured defect: agent-aimail@0.2.1 in a **clean npm project** `npm install` directly
#    ERESOLVE(rc=1). Root cause is not "host not installed", but npm resolving optional peer still will
#    transitive peer tree when resolving optional peers: dsh-session@0.1.0-rc.8 peer requires dsh-llm ^0.1.0-rc.8,
#    while dsh-llm declaration in package missed 0.1.0 line (only ^0.0.1-rc.1/^0.2.0/^0.2.1) ⇒ no solution.
#    `--legacy-peer-deps` can install but is a bypass of resolution, **not considered passing**.
#    Both scenarios must have rc=0:
#      G1 Bare project (no host at all): npm still needs to complete peer resolution validation
#      G2 first install dsh-agent@latest: materialize the entire peer tree — this is the real path that triggers mutual exclusion
INSTALL_TMP=$(mktemp -d)
trap 'rm -rf "$INSTALL_TMP"' EXIT
clean_install() {  # $1 = scenario name; rest = pre-install commands (can be empty)
  local name="$1"
  shift
  local d="$INSTALL_TMP/$name"
  local log="$INSTALL_TMP/$name.log"
  mkdir -p "$d" && ( cd "$d" && npm init -y >/dev/null 2>&1 )
  if [ "$#" -gt 0 ]; then
    ( cd "$d" && "$@" ) >"$INSTALL_TMP/$name-pre.log" 2>&1 || {
      echo "[L2] FAIL: $name pre-install failed (registry unreachable? see $INSTALL_TMP/$name-pre.log)"; tail -5 "$INSTALL_TMP/$name-pre.log"; exit 1; }
  fi
  ( cd "$d" && npm install --no-audit --no-fund "$TGZ" ) >"$log" 2>&1 && return 0
  echo "[L2] FAIL: $name — clean project npm install failed (bypass via --legacy-peer-deps forbidden)"
  grep -E "ERESOLVE|npm error" "$log" | head -8
  exit 1
}
clean_install bare
echo "[L2] ok: clean install (bare project)"
clean_install with-dsh npm install --no-audit --no-fund @deepseek-ai/dsh-agent@latest
echo "[L2] ok: clean install with dsh-agent@latest present"

echo "[L2] PASS: $TGZ ($VER)"
