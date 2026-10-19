#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# aimail agent installer — address-level bootstrap only.
#
#   curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/agent-install.sh | bash
#
# Differences from scripts/bootstrap.sh (system-level machine prep):
#   · no environment variables required (optional flags only)
#   · no bridge detection, no ~/.aimail/.env, no systems/logs dirs
#   · installs one static binary and stops; the binding is created later by
#     `aimail agent activate` during the conversation
#
# What it does:
#   1. detect platform → release asset
#   2. fetch the single static binary from the GitHub Release
#   3. verify sha256 when the release publishes a sidecar, otherwise record it
#   4. install to ~/.local/bin (atomic replace) and smoke `aimail version`
#   5. write ~/.aimail/agent-install.json (version, sha256, path, time)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail
exec 1>&2

REPO="metercai/aimail"
REF="latest"
BIN_DIR="${HOME}/.local/bin"
while [ $# -gt 0 ]; do
  case "$1" in
    --version) REF="${2:?--version needs a tag}"; shift 2 ;;
    --dir)     BIN_DIR="${2:?--dir needs a path}"; shift 2 ;;
    -h|--help) sed -n '3,22p' "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠ %s\n' "$*"; }
die()  { printf '\033[31m✗ %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl required"
command -v sha256sum >/dev/null || command -v shasum >/dev/null || die "sha256sum/shasum required"

os="$(uname -s)"; arch="$(uname -m)"
case "$os-$arch" in
  Linux-x86_64|Linux-amd64)  platform="linux-amd64";  ext="" ;;
  Linux-aarch64|Linux-arm64) platform="linux-arm64";  ext="" ;;
  Darwin-arm64)              platform="macos-arm64";  ext="" ;;
  Darwin-x86_64|Darwin-amd64) platform="macos-arm64"; ext="" ;; # rosetta fallback
  MINGW*|MSYS*|CYGWIN*)      platform="windows-amd64"; ext=".exe" ;;
  *) die "unsupported platform: $os-$arch" ;;
esac

if [ "$REF" = "latest" ]; then
  TAG="$(curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 \
    "https://api.github.com/repos/$REPO/releases?per_page=30" \
    | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//; s/"$//' \
    | grep -m1 '^cli-v' )" || TAG=""
  if [ -z "$TAG" ]; then
    TAG="$(git ls-remote --tags --sort=-v:refname "https://github.com/$REPO.git" 2>/dev/null \
      | awk '{print $2}' | sed 's|refs/tags/||' | grep -m1 '^cli-v' )" || TAG=""
  fi
  [ -n "$TAG" ] || die "no cli-v* release found on $REPO"
else
  TAG="$REF"
fi
VER="${TAG#cli-v}"
ASSET="aimail-$VER-$platform$ext"
URL="https://github.com/$REPO/releases/download/$TAG/$ASSET"
say "release: $TAG ($platform)"

TMP="$(mktemp "${TMPDIR:-/tmp}/aimail-agent-XXXXXX")"
trap 'rm -f "$TMP" "${TMP}.sha" 2>/dev/null || true' EXIT
curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 "$URL" -o "$TMP" \
  || die "download failed: $URL"
chmod +x "$TMP"

sum() { if command -v sha256sum >/dev/null; then sha256sum "$1" | awk '{print $1}'; else shasum -a 256 "$1" | awk '{print $1}'; fi; }
GOT="$(sum "$TMP")"
if curl -fsSL --connect-timeout 10 --max-time 30 "$URL.sha256" -o "${TMP}.sha" 2>/dev/null; then
  WANT="$(awk '{print $1}' "${TMP}.sha")"
  [ "$GOT" = "$WANT" ] || die "sha256 mismatch: got $GOT want $WANT"
  ok "sha256 verified ($GOT)"
else
  warn "release publishes no .sha256 sidecar; recorded $GOT (verify out of band)"
fi

mkdir -p "$BIN_DIR"
TARGET="$BIN_DIR/aimail$ext"
if [ -f "$TARGET" ]; then cp -f "$TARGET" "${TARGET}.old" 2>/dev/null || true; fi
mv -f "$TMP" "$TARGET"; chmod 755 "$TARGET"; rm -f "${TARGET}.old" 2>/dev/null || true
trap - EXIT
ok "installed: $TARGET"

"$TARGET" version >/dev/null 2>&1 || die "binary did not run: $TARGET version"
ok "smoke: aimail version ok"

mkdir -p "$HOME/.aimail"
printf '{"version":"%s","sha256":"%s","path":"%s","installed_at":"%s"}\n' \
  "$VER" "$GOT" "$TARGET" "$(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$HOME/.aimail/agent-install.json"
ok "recorded ~/.aimail/agent-install.json"

case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "$BIN_DIR is not on PATH — add it, or call $TARGET with an absolute path" ;;
esac
say "next: ask the agent to run  aimail agent doctor --questions"
