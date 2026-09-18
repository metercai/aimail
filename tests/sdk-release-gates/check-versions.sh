#!/usr/bin/env bash
# Release gate L1 — version consistency, checked BEFORE tagging/publishing.
#
# Version semantics (v0.1.7+, single product version line):
#   base X.Y.Z is shared by: git tag (vX.Y.Z / vX.Y.Z-rc.N), PyPI
#   (X.Y.Z stable / X.Y.ZrcN PEP440 prerelease), and every TS package
#   (X.Y.Z stable / X.Y.Z-rc.N). Release type must MATCH across all three:
#   dev iterations are rc on every source, go-live is stable everywhere.
#   A stable (non-rc) TS version is what makes bare-name host installs
#   (openclaw plugins install openclaw-aimail) work — host installers
#   reject prerelease versions from bare-name resolution.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

TAG_ARG="${1:-}"
python3 - "$TAG_ARG" <<'PYEOF'
import json, re, subprocess, sys

def base_and_rc(v):
    """'0.1.7' -> ('0.1.7', False); '0.1.7rc1' / '0.1.7-rc.1' -> ('0.1.7', True)."""
    m = re.match(r'^(\d+\.\d+\.\d+)', v)
    return (m.group(1) if m else None, bool(re.search(r'rc', v, re.I)))

fail = []
pyproj = re.search(r'^version = "([^"]+)"', open('pyproject.toml').read(), re.M).group(1)
pyinit = re.search(r'__version__ = "([^"]+)"', open('pysdk/__init__.py').read()).group(1)
if pyproj != pyinit:
    fail.append(f'PyPI dual-source drift: pyproject={pyproj} pysdk={pyinit}')
print(f'[L1] pyproject={pyproj} pysdk={pyinit}')
py_base, py_rc = base_and_rc(pyproj)

tag = sys.argv[1] if len(sys.argv) > 1 else ''
if not tag:
    out = subprocess.run(['git', 'tag', '--sort=-v:refname'], capture_output=True, text=True).stdout
    tag = next((t for t in out.split() if re.match(r'^v\d', t)), '')
if tag:
    t_base, t_rc = base_and_rc(tag[1:])
    print(f'[L1] tag {tag} base={t_base} rc={t_rc}')
    if t_base != py_base:
        fail.append(f'tag {tag} base {t_base} != PyPI base {py_base}')
    if t_rc != py_rc:
        fail.append(f'release-type mismatch: tag rc={t_rc} vs PyPI rc={py_rc} (dev=rc, go-live=stable)')

for p in ['mail-core', 'mail', 'dsh-aimail', 'openclaw-aimail', 'pi-aimail']:
    d = json.load(open(f'tssdk/packages/{p}/package.json'))
    v = d['version']
    b, rc = base_and_rc(v)
    if b != py_base:
        fail.append(f'{p} base {b} != PyPI base {py_base}')
    if rc != py_rc:
        fail.append(f'{p} release-type rc={rc} != PyPI rc={py_rc}')
    print(f'[L1] {p} {v} (base={b} rc={rc})')

if fail:
    print('[L1] FAIL: ' + '; '.join(fail))
    sys.exit(1)
print('[L1] PASS (versions aligned, release type consistent)')
PYEOF

echo "[L1] npm dependency-order gate (registry lookups may be slow)"
for p in mail-core mail dsh-aimail openclaw-aimail pi-aimail; do
  dir="tssdk/packages/$p"
  ver=$(python3 -c "import json;print(json.load(open('$dir/package.json'))['version'])")
  published=$(npm view "$p@$ver" version 2>/dev/null || true)
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
    ok=$(npm view "$dname@$dver" version 2>/dev/null || true)
    if [ -z "$ok" ]; then
      echo "[L1] FAIL: $p@$ver depends on $dname@$dver which is NOT on the registry"
      exit 1
    fi
    echo "  ok: $p deps $dname@$dver (published)"
  done
done
echo "[L1] PASS"
