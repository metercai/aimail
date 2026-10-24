#!/usr/bin/env bash
# Release workflow tool — version consistency, checked BEFORE tagging/publishing.
# (2026-10-08 layering fix: it checks version metadata and runs no code, so it is a release tool, not an integration layer;
#  called before tagging by scripts/bump.sh and advanced run-e2e.sh.)
#
# Version semantics (v0.1.7+, single product version line):
#   base X.Y.Z is shared by: git tag (vX.Y.Z / vX.Y.Z-rc.N), PyPI
#   (X.Y.Z stable / X.Y.ZrcN PEP440 prerelease), and every TS package
#   (X.Y.Z stable / X.Y.Z-rc.N). Release type must MATCH across all three:
#   dev iterations are rc on every source, go-live is stable everywhere.
#   A stable (non-rc) TS version is what makes bare-name host installs
#   (openclaw plugins install agent-aimail) work — host installers
#   reject prerelease versions from bare-name resolution.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
. "$(dirname "${BASH_SOURCE[0]}")/_npmview.sh"   # distinguishes "not published" (E404) from "query failed"

TAG_ARG="${1:-}"
python3 - "$TAG_ARG" <<'PYEOF'
import json, re, subprocess, sys

def base_and_rc(v):
    """'0.1.7' -> ('0.1.7', False); '0.1.7rc1' / '0.1.7-rc.1' -> ('0.1.7', True)."""
    m = re.match(r'^(\d+\.\d+\.\d+)', v)
    return (m.group(1) if m else None, bool(re.search(r'rc', v, re.I)))

fail = []
VERS = {}
DEPS = {}
pyproj = re.search(r'^version = "([^"]+)"', open('pyproject.toml').read(), re.M).group(1)
pyinit = re.search(r'__version__ = "([^"]+)"', open('pysdk/__init__.py').read()).group(1)
if pyproj != pyinit:
    fail.append(f'PyPI dual-source drift: pyproject={pyproj} pysdk={pyinit}')
print(f'[L1] pyproject={pyproj} pysdk={pyinit}')
py_base, py_rc = base_and_rc(pyproj)

tag = sys.argv[1] if len(sys.argv) > 1 else ''
if not tag:
    # Only tags with the **same base** as the local version are considered; when several share the base (e.g. v0.1.12 and v0.1.12-rc.2)
    # **prefer the one whose shape matches the local version** (stable → exact vX.Y.Z, rc → vX.Y.Z-rc.N).
    # The original used `git tag --sort=-v:refname` to take the "latest", but git's version sort places
    # '0.1.12-rc.2' **after** '0.1.12' ⇒ a stable release would read the stale rc tag and falsely report
    # "release-type mismatch" (measured false-red on v0.1.12, 2026-09-21).
    out = subprocess.run(['git', 'tag', '-l', 'v*'], capture_output=True, text=True).stdout
    cands = [t for t in out.split() if re.match(r'^v\d', t)]
    same = [t for t in cands if base_and_rc(t[1:])[0] == py_base]
    if same:
        exact = [t for t in same if base_and_rc(t[1:])[1] == py_rc]
        tag = (exact or same)[0]
if tag:
    t_base, t_rc = base_and_rc(tag[1:])
    print(f'[L1] tag {tag} base={t_base} rc={t_rc}')
    if t_base != py_base:
        fail.append(f'tag {tag} base {t_base} != PyPI base {py_base}')
    if t_rc != py_rc:
        fail.append(f'release-type mismatch: tag rc={t_rc} vs PyPI rc={py_rc} (dev=rc, go-live=stable)')

for p in ['agent-aimail']:
    d = json.load(open(f'tssdk/packages/{p}/package.json'))
    v = d['version']
    b, rc = base_and_rc(v)
    if not b or rc is None:
        fail.append(f'{p} version {v} is not valid semver')
    VERS[p] = v
    DEPS[p] = set((d.get('dependencies') or {}).keys())
    print(f'[L1] {p} {v} (base={b} rc={rc})')

# Audit D8: the workspace root package must stay private (it is not a publishable artifact). If it were
# mistakenly made publishable, a future `pnpm -r publish` would ship the wrong version and this gate would not catch it.
_root = json.load(open('tssdk/package.json'))
if not _root.get('private'):
    fail.append('tssdk/package.json must stay private (workspace root is not published)')
print(f"[L1] workspace root {_root.get('name')} {_root.get('version')} (private={bool(_root.get('private'))})")

_grp = {'agent-aimail'}
_ch = True
while _ch:
    _ch = False
    for _p, _ds in DEPS.items():
        if _p not in _grp and any(d in _grp or d.endswith('/mail-core') for d in _ds):
            _grp.add(_p); _ch = True
_gv = {VERS[p] for p in _grp if p in VERS}
if len(_gv) > 1:
    print('[L1] note (not a failure): versions within the closure group are independent ' + str(sorted(_gv)) + ' — owner decision 2026-10-07 ✓')
print(f'[L1] closure(mail-core) = {sorted(_grp)} versions={sorted(_gv)}')

if fail:
    print('[L1] FAIL: ' + '; '.join(fail))
    sys.exit(1)
print('[L1] PASS (versions aligned, release type consistent)')
PYEOF

echo "[L1] npm dependency-order gate (registry lookups may be slow)"
# ── Same-batch pending semantics ────────────────────────────────────────────────
# RELEASE_BATCH="agent-aimail"
#   Comma-separated, ordered by **publish order**. Allowing one "dependency not on the registry yet" requires all three:
#     1) the dependency is in this batch list;
#     2) the dependency's position in the list is **earlier** than the depender's (publish-npm.sh publishes serially in the same order);
#     3) the dependency's local workspace version == the version resolved from the dependency declaration.
#   Missing any one ⇒ still red by the original criterion. **Not setting RELEASE_BATCH = exactly the original strict behavior**,
#   so CI / third-party callers are unaffected; the batch just makes "published in this same batch" explicit.
declare -A BATCH_POS=() BATCH_VER=()
BATCH="${RELEASE_BATCH:-}"
if [ -n "$BATCH" ]; then
  while read -r _bn _bi _bv; do
    [ -n "$_bn" ] || continue
    BATCH_POS["$_bn"]="$_bi"; BATCH_VER["$_bn"]="$_bv"
  done < <(python3 - "$BATCH" <<'PY'
import json, os, sys
for i, n in enumerate([x.strip() for x in sys.argv[1].split(',') if x.strip()]):
    f = os.path.join('tssdk/packages', n, 'package.json')
    v = json.load(open(f))['version'] if os.path.isfile(f) else ''
    print(n, i, v)
PY
)
  echo "[L1] release batch declared (publish order): ${BATCH}"
fi
for p in agent-aimail; do
  dir="tssdk/packages/$p"
  ver=$(python3 -c "import json;print(json.load(open('$dir/package.json'))['version'])")
  # Audit P2: a query failure (network/registry) must be distinguished from "not published" (see _npmview.sh)
  if ! published=$(npm_version "$p" "$ver"); then
    echo "[L1] FAIL: registry query failed for $p@$ver"; exit 1
  fi
  if [ -n "$published" ]; then
    echo "  skip $p@$ver (already on registry)"
    continue
  fi
  deps=$(python3 -c "
import json
d = json.load(open('$dir/package.json')).get('dependencies', {})
print(' '.join(f'{k}:{v}' for k, v in d.items() if 'aimail' in k))")
  for dep in $deps; do
    dname="${dep%%:*}"; dspec="${dep#*:}"
    if [[ "$dspec" == workspace:* ]]; then
      dver=$(python3 -c "
import json, os
root = '$ROOT/tssdk/packages'
for sub in os.listdir(root):
    f = os.path.join(root, sub, 'package.json')
    if os.path.isfile(f):
        m = json.load(open(f))
        if m.get('name') == '$dname':
            print(m['version']); break")
    else
      dver="$dspec"  # concrete registry range, e.g. ^0.1.7
    fi
    dver="${dver#^}"; dver="${dver#~}"
    if ! ok=$(npm_version "$dname" "$dver"); then
      echo "[L1] FAIL: registry query failed for $dname@$dver"; exit 1
    fi
    if [ -z "$ok" ]; then
      base="${dname##*/}"
      if [ -n "${BATCH_POS[$base]+x}" ]; then
        pos_dep="${BATCH_POS[$base]}"; pos_self="${BATCH_POS[$p]:-}"
        if [ -z "$pos_self" ]; then
          echo "[L1] FAIL: $p@$ver depends on $dname@$dver (declared in batch) but $p itself is not in RELEASE_BATCH"
          exit 1
        fi
        if [ "$pos_dep" -ge "$pos_self" ]; then
          echo "[L1] FAIL: batch order wrong: $p(pos=$pos_self) depends on $dname(pos=$pos_dep) — dependency must come earlier"
          exit 1
        fi
        if [ "${BATCH_VER[$base]}" != "$dver" ]; then
          echo "[L1] FAIL: $p@$ver declares $dname@$dver but the batch has $dname@${BATCH_VER[$base]}"
          exit 1
        fi
        echo "  pending(batch[$pos_dep<$pos_self]): $p deps $dname@$dver (not on registry yet; lands earlier in this batch)"
        continue
      fi
      echo "[L1] FAIL: $p@$ver depends on $dname@$dver which is NOT on the registry"
      exit 1
    fi
    echo "  ok: $p deps $dname@$dver (published)"
  done
done
echo "[L1] PASS"
