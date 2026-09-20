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
. "$(dirname "${BASH_SOURCE[0]}")/_npmview.sh"   # 区分"未发布"(E404)与"查询失败"

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
    # 只认与本地版本**同 base** 的 tag; 同 base 有多个(如 v0.1.12 与 v0.1.12-rc.2)时
    # **优先与本地版本形态一致**的那个(stable→精确 vX.Y.Z, rc→vX.Y.Z-rc.N)。
    # 原实现用 `git tag --sort=-v:refname` 取"最新", 而 git 的 version sort 把
    # '0.1.12-rc.2' 排在 '0.1.12' **之后** ⇒ stable 发版时读到旧 rc tag, 误报
    # "release-type mismatch"(v0.1.12 实测假红, 2026-09-21)。
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

for p in ['mail-core', 'mail', 'dsh-aimail', 'openclaw-aimail', 'pi-aimail']:
    d = json.load(open(f'tssdk/packages/{p}/package.json'))
    v = d['version']
    b, rc = base_and_rc(v)
    if b != py_base:
        fail.append(f'{p} base {b} != PyPI base {py_base}')
    if rc != py_rc:
        fail.append(f'{p} release-type rc={rc} != PyPI rc={py_rc}')
    print(f'[L1] {p} {v} (base={b} rc={rc})')

# 审计 D8: 工作区根包必须 private(它不是发布物)。若被误改成可发布, 将来
# `pnpm -r publish` 类操作会发出错版本且本门禁不拦。
_root = json.load(open('tssdk/package.json'))
if not _root.get('private'):
    fail.append('tssdk/package.json must stay private (workspace root is not published)')
print(f"[L1] workspace root {_root.get('name')} {_root.get('version')} (private={bool(_root.get('private'))})")

if fail:
    print('[L1] FAIL: ' + '; '.join(fail))
    sys.exit(1)
print('[L1] PASS (versions aligned, release type consistent)')
PYEOF

echo "[L1] npm dependency-order gate (registry lookups may be slow)"
for p in mail-core mail dsh-aimail openclaw-aimail pi-aimail; do
  dir="tssdk/packages/$p"
  ver=$(python3 -c "import json;print(json.load(open('$dir/package.json'))['version'])")
  # 审计 P2: 查询失败(网络/registry)必须与"未发布"区分(见 _npmview.sh)
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
      echo "[L1] FAIL: $p@$ver depends on $dname@$dver which is NOT on the registry"
      exit 1
    fi
    echo "  ok: $p deps $dname@$dver (published)"
  done
done
echo "[L1] PASS"
