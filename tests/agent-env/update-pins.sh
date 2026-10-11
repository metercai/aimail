#!/usr/bin/env bash
#
# agent-env/update-pins.sh — move the pinned test environment forward (MAINTENANCE ONLY).
#
# Tests must start from the same environment every time, so the versions they run against
# live in pins.env and are read by prepare.sh, verify.sh and docker/build.sh. This script
# is the deliberate way to move them forward. It is NEVER called from a gate.
#
# Usage:
#   update-pins.sh [--dry-run]      # --dry-run prints the resolution, writes nothing
#
# "Latest" is always resolved from an authoritative source, never guessed:
#   base images     : registry manifest digest of the tag (docker.io / ghcr.io API)
#   dsh/pi/pnpm     : registry.npmjs.org dist-tags.latest
#   hermes (host)   : newest commit of the branch the OFFICIAL installer tracks
#                     (REPO_URL/BRANCH read out of install.sh; git ls-remote)
#
# Not auto-resolved (they are not "latest"-shaped): BASE_DEERFLOW (a commit tag of a
# locally built source image) and HERMES_HOST_VERSION (the version that pin's commit
# reports) — the script prints what it knows and says what to re-check.
#
# After running: bash docker/build.sh (rebuild + offline acceptance), then commit pins.env.
set -uo pipefail
CTX="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PINS="$CTX/pins.env"
INSTALLER_URL="https://hermes-agent.nousresearch.com/install.sh"
DRY=0; [ "${1:-}" = "--dry-run" ] && DRY=1

[ -f "$PINS" ] || { echo "✗ pins.env not found at $PINS" >&2; exit 1; }
. "$PINS"

_jq() { python3 -c "import sys,json;d=json.load(sys.stdin);print($1)"; }

digest_of() {   # <[registry/]repo:tag> -> sha256:...
  local ref="$1" reg name tag tok
  case "$ref" in
    ghcr.io/*)   reg=ghcr.io;               name="${ref#ghcr.io/}" ;;
    docker.io/*) reg=registry-1.docker.io;  name="${ref#docker.io/}" ;;
    *)           reg=registry-1.docker.io;  name="$ref" ;;
  esac
  tag="${name##*:}"; name="${name%:*}"
  if [ "$reg" = ghcr.io ]; then
    tok="$(curl -fsS "https://ghcr.io/token?scope=repository:${name}:pull&service=ghcr.io" 2>/dev/null | _jq "d['token']")"
  else
    tok="$(curl -fsS "https://auth.docker.io/token?service=registry.docker.io&scope=repository:${name}:pull" 2>/dev/null | _jq "d['token']")"
  fi
  [ -n "${tok:-}" ] || return 1
  curl -fsSI -H "Authorization: Bearer $tok" \
    -H 'Accept: application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.docker.distribution.manifest.v2+json' \
    "https://$reg/v2/$name/manifests/$tag" 2>/dev/null \
    | awk 'tolower($1)=="docker-content-digest:"{print $2}' | tr -d '\r' | head -1
}

digest_tag_of() { printf '%s' "${1%%@*}"; }   # ref@sha256:.. -> ref

npm_latest() {  # <pkg> -> version
  curl -fsS "https://registry.npmjs.org/$(printf '%s' "$1" | sed 's|/|%2F|')" 2>/dev/null \
    | _jq "d['dist-tags']['latest']"
}

hermes_ref() {  # "<repo_url> <branch>" from the official installer
  local line repo branch
  line="$(curl -fsS --max-time 40 "$INSTALLER_URL" 2>/dev/null | grep -m1 '^REPO_URL=' || true)"
  repo="$(printf '%s' "$line" | sed -n 's/.*:-\(.*\)\.git}.*/\1/p')"
  branch="$(curl -fsS --max-time 40 "$INSTALLER_URL" 2>/dev/null | grep -m1 '^BRANCH=' | sed -n 's/^BRANCH="\?\([^"]*\)"\?.*/\1/p')"
  [ -n "$repo" ] || repo="https://github.com/NousResearch/hermes-agent"   # install.sh:23 default
  [ -n "$branch" ] || branch="main"                                       # install.sh:24 default
  printf '%s %s' "$repo" "$branch"
}

changed=0
set_pin() {   # set_pin <KEY> <new-value>
  local key="$1" new="$2" old
  old="$(eval "printf '%s' \"\${$key:-}\"")"
  if [ -z "$new" ]; then echo "  ! $key: could not resolve (kept: $old)"; return 0; fi
  if [ "$new" = "$old" ]; then echo "  = $key: $old"; return 0; fi
  echo "  → $key: $old  ⇒  $new"
  changed=1
  [ "$DRY" = 1 ] && return 0
  # rewrite the key in place (pins.env is line-oriented: KEY=value, comments allowed)
  python3 - "$PINS" "$key" "$new" <<'PY'
import sys
path, key, new = sys.argv[1], sys.argv[2], sys.argv[3]
out = []
for ln in open(path):
    out.append(f"{key}={new}\n" if ln.startswith(key + "=") else ln)
open(path, "w").writelines(out)
PY
}

echo "── resolving current latest${DRY:+ (dry run — nothing written)}"
for key in BASE_HERMES BASE_OPENCLAW BASE_NODE; do
  ref_tag="$(digest_tag_of "$(eval "printf '%s' \"\${$key}\"")")"   # repo:tag
  d="$(digest_of "$ref_tag")"
  set_pin "$key" "$ref_tag${d:+@$d}"
done
set_pin DSH_VERSION  "$(npm_latest @deepseek-ai/dsh)"
set_pin PI_VERSION   "$(npm_latest @earendil-works/pi-coding-agent)"
set_pin PNPM_VERSION "$(npm_latest pnpm)"
read -r _repo _branch <<EOF
$(hermes_ref)
EOF
set_pin HERMES_COMMIT "$(git ls-remote "$_repo" "$_branch" 2>/dev/null | awk 'NR==1{print $1}')"

echo "── not auto-resolved (check by hand when they move):"
echo "  · BASE_DEERFLOW=$BASE_DEERFLOW  (built from the deer-flow source checkout; retag on a new commit)"
echo "  · HERMES_HOST_VERSION=$HERMES_HOST_VERSION  (the version HERMES_COMMIT reports; a fresh host"
echo "    install prints it — prepare.sh hermes on a machine without one)"
if [ "$changed" = 0 ]; then echo "── pins are already current"
elif [ "$DRY" = 1 ]; then echo "── pins would change (re-run without --dry-run to write)"
else echo "── pins.env updated — now: bash docker/build.sh, then commit pins.env"; fi
