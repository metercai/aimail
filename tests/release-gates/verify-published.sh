#!/usr/bin/env bash
# Release gate L3 — post-publish smoke against the REGISTRY (real artifacts).
#  - PyPI: version present == local pyproject; clean-venv install + import +
#    one real search_mail call.
#  - npm 5 packages: registry version == local package.json; download the
#    registry tarball and re-run check-tarball.sh (registry truth, not the
#    locally packed artifact).
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
GATE="tests/release-gates"

PYVER=$(python3 -c "import re;print(re.search(r'^version = \"([^\"]+)\"', open('pyproject.toml').read(), re.M).group(1))")
echo "═══ [L3] PyPI aimailsdk==$PYVER ═══"
PYOK=$(curl -s "https://pypi.org/pypi/aimailsdk/$PYVER/json" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d['info']['version'])" 2>/dev/null || true)
[ "$PYOK" = "$PYVER" ] || { echo "[L3] FAIL: PyPI $PYVER not found (got '$PYOK')"; exit 1; }
echo "[L3] ok: PyPI $PYVER present"
V=$(mktemp -d)
python3 -m venv "$V" >/dev/null 2>&1
"$V/bin/pip" install -q --index-url https://pypi.org/simple/ "aimailsdk==$PYVER" 2>&1 | tail -1 || true
"$V/bin/python" - <<'PY' || { echo "[L3] FAIL: pip install/import/smoke"; rm -rf "$V"; exit 1; }
import sys, tempfile, os
# import aimail first: its __init__ bootstraps sys.path with the pysdk
# core_dir, which is what makes the flat-module imports resolvable
# (verify-wheel contract: pip-layout modules are aimail.*).
import aimail  # noqa
import aimail.aimail_tools as T  # noqa
assert "search_mail" in dir(T), "search_mail missing from installed sdk"
tmp = tempfile.mkdtemp(prefix="l3-")
os.environ["AIMAIL_HOME"] = tmp
T._resolve_agent_email = lambda: "agent.l3@test.local"
T._load_profile_config = lambda: {"gateway_url": "http://127.0.0.1:9", "api_key": "k",
                                  "email": "agent.l3@test.local", "save_raw_snapshots": True}
T.store_inbound_message("l3-mid", ["t"], "agent.l3@test.local",
                        preprocessed_payload={"subject": "release smoke",
                                              "body": "gate check", "sender": "x@y.z",
                                              "to": ["agent.l3@test.local"],
                                              "my_amail_addr": "agent.l3@test.local"})
r = T.search_mail(query="release")
assert r.get("count") == 1, f"search_mail miss: {r}"
print("[L3] ok: install + import + search_mail smoke passed")
PY
rm -rf "$V"

echo "═══ [L3] npm 5 packages ═══"
for p in mail-core mail dsh-aimail openclaw-aimail pi-aimail; do
  dir="tssdk/packages/$p"
  ver=$(python3 -c "import json;print(json.load(open('$dir/package.json'))['version'])")
  name=$(python3 -c "import json;print(json.load(open('$dir/package.json'))['name'])")
  reg=$(npm view "$name@$ver" version 2>/dev/null || true)
  [ "$reg" = "$ver" ] || { echo "[L3] FAIL: $name registry=$reg != local=$ver"; exit 1; }
  echo "[L3] ok: $name@$ver on registry"
  URL=$(npm view "$name@$ver" dist.tarball 2>/dev/null)
  [ -n "$URL" ] || { echo "[L3] FAIL: no tarball URL for $name@$ver"; exit 1; }
  curl -sL "$URL" -o /tmp/l3-$$.tgz
  "$GATE/check-tarball.sh" "/tmp/l3-$$.tgz" "$ver" || { rm -f /tmp/l3-$$.tgz; exit 1; }
  rm -f /tmp/l3-$$.tgz
done
echo "═══ [L3] PASS — all published artifacts verified from registry ═══"
