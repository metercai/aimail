#!/usr/bin/env bash
# pysdk build for the L2 matrix (`run-matrix.sh --sdk py`).
#
# L2 tests the CURRENT DEV BUILD, so the wheel is built from the working tree here — the
# same thing `npm pack` is for the ts hosts. Build isolation fetches hatchling itself, so
# no tooling has to be installed on the runner beyond python3+pip.
#
# provides: pysdk_build <workdir>  -> exports SDK_TARBALL_DIR, SDK_FILE, SDK_VER
set -uo pipefail

V2_LIB="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AIMAIL_ROOT="$(cd "$V2_LIB/../../../.." && pwd)"

pysdk_build() {
  local work="${1:?workdir}"
  local dest="$work/sdkpack"
  mkdir -p "$dest"
  echo "── building dev pysdk wheel (pip wheel from the working tree)" >&2
  if ! ( cd "$AIMAIL_ROOT" && python3 -m pip wheel . -w "$dest" --no-deps \
           >"$work/pysdk-build.log" 2>&1 ); then
    echo "GAP: pysdk wheel build failed — tail of $work/pysdk-build.log:" >&2
    tail -12 "$work/pysdk-build.log" >&2
    return 2
  fi
  local whl first
  first="$(ls "$dest"/*.whl 2>/dev/null | head -1)"
  [ -n "$first" ] || { echo "GAP: pip wheel produced no wheel" >&2; return 2; }
  # agent_aimail-0.2.9-py3-none-any.whl -> 0.2.9
  whl="$(basename "$first")"
  export SDK_TARBALL_DIR="$dest"
  export SDK_FILE="$first"
  export SDK_VER="${whl#agent_aimail-}"; export SDK_VER="${SDK_VER%%-*}"
  echo "── L2 dev pysdk $SDK_VER ($whl)" >&2
}
