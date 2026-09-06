#!/usr/bin/env bash
# Release gate L1 — version consistency, checked BEFORE tagging/publishing.
#  - PyPI dual-source: pyproject.toml == pysdk/__init__.__version__
#  - git tag (arg or latest v*) == PyPI version
#  - npm: for every package whose version != registry, its @aimail/*
#    dependency targets must already exist on the registry (order gate:
#    mail-core -> mail -> dsh/openclaw/pi).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

PYPROJ=$(python3 -c "import re;print(re.search(r'^version = \"([^\"]+)\"', open('pyproject.toml').read(), re.M).group(1))")
PYINIT=$(python3 -c "import re;print(re.search(r'__version__ = \"([^\"]+)\"', open('pysdk/__init__.py').read()).group(1))")
echo "[L1] pyproject=$PYPROJ pysdk=$PYINIT"
[ "$PYPROJ" = "$PYINIT" ] || { echo "[L1] FAIL: PyPI dual-source drift"; exit 1; }

# tag = latest v* (or the one being published)
TAG="${1:-$(git tag --sort=-v:refname | grep -E '^v[0-9]' | head -1)}"
if [ -n "$TAG" ]; then
  TVER="${TAG#v}"
  [ "$TVER" = "$PYPROJ" ] || { echo "[L1] FAIL: tag $TAG != pyproject $PYPROJ"; exit 1; }
  echo "[L1] tag $TAG == version $PYPROJ"
else
  echo "[L1] warn: no v* tag found (tag check skipped)"
fi

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
      # workspace:^ — the concrete version is the local workspace package's own
      dver=$(python3 -c "
import json
# map package name -> dir under tssdk/packages
import os
root = '$ROOT/tssdk/packages'
for sub in os.listdir(root):
    f = os.path.join(root, sub, 'package.json')
    if os.path.isfile(f):
        m = json.load(open(f))
        if m.get('name') == '$dname':
            print(m['version']); break")
    else
      dver="$dspec"  # concrete registry range, e.g. ^0.1.0-rc.19
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
