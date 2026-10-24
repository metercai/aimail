#!/usr/bin/env bash
# Bump version per package (owner 2026-10-07: package versions are independent; a core-package change ⇒ all five packages bump together, invoked per package by the caller)
# Usage: bump.sh <agent-aimail> <ver>
#       bump.sh pysdk <ver>     ← only touches pyproject.toml + pysdk/__init__.py
set -u
cd "$(dirname "$0")/.." || exit 1
p=${1:-}; v=${2:-}
[ -n "$p" ] && [ -n "$v" ] || { echo "usage: bump.sh <pkg|pysdk> <ver>"; exit 2; }
if [ "$p" = pysdk ]; then
  python3 -c "
import re,pathlib
a=pathlib.Path('pyproject.toml'); s=a.read_text(encoding='utf-8')
a.write_text(re.sub(r'(?m)^version = \".*\"', 'version = \"$v\"', s, count=1), encoding='utf-8')
b=pathlib.Path('pysdk/__init__.py'); t=b.read_text(encoding='utf-8')
b.write_text(re.sub(r'__version__ = \".*\"', '__version__ = \"$v\"', t, count=1), encoding='utf-8')
print('pysdk ->', '$v')"
  exit 0
fi
f="tssdk/packages/$p/package.json"
[ -f "$f" ] || { echo "unknown pkg: $p"; exit 2; }
python3 -c "
import json,pathlib
q=pathlib.Path('$f'); d=json.loads(q.read_text(encoding='utf-8'))
d['version']='$v'; q.write_text(json.dumps(d,indent=2,ensure_ascii=False)+chr(10), encoding='utf-8')
print('$p ->', '$v')"
bash scripts/check-versions.sh | tail -2
