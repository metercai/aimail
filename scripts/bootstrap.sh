#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════════
# aimail bootstrap — one-shot install of the aimail CLI (Rust binary).
#
#   curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
#
# Zero runtime deps beyond curl: the CLI ships as a single self-contained
# binary (musl-static on Linux, no OpenSSL anywhere) in a GitHub Release.
# No python3, no tar, no source snapshot.
#
# What it does (all writes confined to ~/.aimail and ~/.local/bin, no sudo):
#   1. preflight: curl present
#   2. detect platform → aimail-<platform>[.exe] asset
#   3. fetch the binary from the aimail GitHub Release:
#      AIMAIL_VERSION pins a tag (e.g. cli-v0.1.37), default = newest cli-v* release
#   4. main dir skeleton ~/.aimail/{systems,logs,bridge} (0700) +
#      place the binary at ~/.aimail/bin/aimail (idempotent, atomic-ish swap)
#   5. disk headroom check (<100 MiB fail, <1 GiB warn)
#   6. smoke: `aimail --version` (also proves the binary runs on this machine)
#   7. link ~/.local/bin/aimail → ~/.aimail/bin/aimail
#   8. persist AIMAIL_* env vars into ~/.aimail/.env (existing keys never overwritten)
#   9. next-step guidance
#
# Machine-level gateway lock / bridge deploy no longer happen here: the Rust
# `aimail install` does both (local gateway → direct mode, remote → bridge).
#
# Re-running = upgrade (re-fetch + replace) + machine-prep re-check.
#   AIMAIL_VERSION=cli-vX.Y.Z  pin an exact release tag
#   AIMAIL_SKIP_INSTALL=1      keep the existing binary, only re-check env
#   AIMAIL_PROG_DIR=…          custom program root (default ~/.aimail/bin)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

# All output to stderr: under `curl … | bash` stdout is a pipe (block
# buffered) — buffered output would only flush at exit. stderr is unbuffered.
exec 1>&2

REPO="metercai/aimail"
AM_HOME="$HOME/.aimail"
PROG="${AIMAIL_PROG_DIR:-$AM_HOME/bin}"
BIN_DIR="$HOME/.local/bin"
REF="${AIMAIL_VERSION:-latest}"

say()  { printf '  %s\n' "$*"; }
ok()   { printf '  \033[32m✓\033[0m %s\n' "$*"; }
warn() { printf '  \033[33m⚠ %s\n' "$*"; }
die()  { printf '\033[31m✗ %s\n' "$*" >&2; exit 1; }

command -v curl >/dev/null || die "curl required"

# ── 1. platform detection → release asset suffix ─────────────────
os="$(uname -s)"
arch="$(uname -m)"
case "$os-$arch" in
  Linux-x86_64|Linux-amd64)     platform="linux-amd64";   ext="" ;;
  Linux-aarch64|Linux-arm64)    platform="linux-arm64";   ext="" ;;
  Darwin-arm64)                 platform="macos-arm64";   ext="" ;;
  MINGW*|MSYS*|CYGWIN*)         platform="windows-amd64"; ext=".exe" ;;
  *) die "unsupported platform: $os-$arch (need linux-amd64, linux-arm64, macos-arm64, or windows-amd64)" ;;
esac

# ── 2. resolve the release tag (AIMAIL_VERSION pins, default = newest cli-v*) ──
if [ "$REF" = "latest" ]; then
  TAG="$(curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 \
    "https://api.github.com/repos/$REPO/releases?per_page=30" \
    | grep -o '"tag_name": *"[^"]*"' | sed 's/"tag_name": *"//; s/"$//' \
    | grep -m1 '^cli-v' )" || TAG=""
  [ -n "$TAG" ] || die "no cli-v* release found on $REPO — the CLI has not been released yet"
else
  TAG="$REF"
fi
say "release: $TAG ($platform)"

# ── 3. fetch the platform binary from the release ────────────────
ASSET_URL="$(curl -fsSL --retry 2 --connect-timeout 15 --max-time 60 \
  "https://api.github.com/repos/$REPO/releases/tags/$TAG" \
  | grep -o '"browser_download_url": *"[^"]*"' | sed 's/"browser_download_url": *"//; s/"$//' \
  | grep -m1 "aimail-$platform$ext\$" )" || ASSET_URL=""
[ -n "$ASSET_URL" ] || die "no aimail-$platform asset on release $TAG"

TMP_BIN="$(mktemp /tmp/aimail-bootstrap-XXXXXX)"
trap 'rm -f "$TMP_BIN"' EXIT
say "fetching $ASSET_URL"
curl -fsSL --retry 2 --retry-delay 2 --connect-timeout 15 --max-time 300 \
  "$ASSET_URL" -o "$TMP_BIN" || die "download failed: $ASSET_URL"
chmod +x "$TMP_BIN"

# ── 4. main dir (0700 — holds admin keys/secrets) + place binary ──
mkdir -p "$AM_HOME/systems" "$AM_HOME/logs" "$AM_HOME/bridge"
chmod 700 "$AM_HOME" 2>/dev/null || true
ok "main dir $AM_HOME"

FREE_MIB=$(( $(df -Pk "$AM_HOME" | awk 'NR==2 {print $4}') / 1024 ))
if [ "$FREE_MIB" -lt 100 ]; then
  die "insufficient free disk on $AM_HOME: ${FREE_MIB} MiB (< 100 MiB)"
elif [ "$FREE_MIB" -lt 1024 ]; then
  warn "low free disk on $AM_HOME: ${FREE_MIB} MiB — watch logs/mail snapshots"
else
  ok "disk free: ${FREE_MIB} MiB"
fi

if [ "${AIMAIL_SKIP_INSTALL:-0}" = "1" ]; then
  [ -x "$PROG/aimail" ] || die "AIMAIL_SKIP_INSTALL=1 but no binary at $PROG/aimail"
  ok "existing binary kept (skip-install)"
else
  mkdir -p "$PROG"
  OLD="$PROG/aimail.old.$$"
  [ -f "$PROG/aimail" ] && mv "$PROG/aimail" "$OLD" 2>/dev/null || true
  mv "$TMP_BIN" "$PROG/aimail"
  chmod 755 "$PROG/aimail"
  rm -f "$OLD"
  trap - EXIT
  ok "binary installed at $PROG/aimail"
fi

# ── 5. smoke: the binary must actually run here ──────────────────
"$PROG/aimail" --version >/dev/null 2>&1 || die "binary did not run: $PROG/aimail --version"
ok "smoke: $( "$PROG/aimail" --version 2>/dev/null | head -1 )"

# ── 6. PATH entry ────────────────────────────────────────────────
mkdir -p "$BIN_DIR"
ln -sf "$PROG/aimail" "$BIN_DIR/aimail"
ok "linked $BIN_DIR/aimail"
case ":$PATH:" in
  *":$BIN_DIR:"*) ;;
  *) warn "add to PATH: export PATH=\"$BIN_DIR:\$PATH\"" ;;
esac

# ── 7. persist shell env vars into ~/.aimail/.env (future terminals) ──
# Only AIMAIL_* keys set in this shell are written; existing keys in the
# file are never overwritten. The file lives in the 0700 main dir.
ENV_FILE="$AM_HOME/.env"
(
  [ -f "$ENV_FILE" ] || printf '# aimail machine env — generated by bootstrap\n' >> "$ENV_FILE"
  for k in AIMAIL_URL AIMAIL_MANAGER_ADDRESS AIMAIL_ADMIN_KEY \
           AIMAIL_PRODUCT_CODE AIMAIL_SYSTEM_NAME AIMAIL_DOMAIN \
           AIMAIL_WEBHOOK_HOST AIMAIL_WEBHOOK_MODE; do
    v="${!k:-}"
    if [ -n "$v" ] && ! grep -q "^$k=" "$ENV_FILE" 2>/dev/null; then
      printf '%s=%s\n' "$k" "$v" >> "$ENV_FILE"
    fi
  done
)
chmod 600 "$ENV_FILE" 2>/dev/null || true
[ -f "$ENV_FILE" ] && ok "env persisted to $ENV_FILE"

# ── 8. env readiness (README scenarios A/B/C; set env BEFORE bootstrapping)
have_env() { [ -n "${!1:-}" ] || grep -q "^$1=" "$ENV_FILE" 2>/dev/null; }
if have_env AIMAIL_URL && \
   { { have_env AIMAIL_PRODUCT_CODE && have_env AIMAIL_SYSTEM_NAME; } || \
     { have_env AIMAIL_PRODUCT_CODE && have_env AIMAIL_DOMAIN; } || \
     { have_env AIMAIL_ADMIN_KEY && have_env AIMAIL_DOMAIN; }; }; then
  ok "env ready (one of the README scenarios)"
else
  warn "env incomplete — see README (bootstrap prerequisites) for the AIMAIL_* set, then re-run"
fi

# ── 9. next steps ────────────────────────────────────────────────
ok "bootstrap done — $TAG ($platform)"
echo
say "next:"
say "  1. activate a system: →  aimail install --home <agent-host-root>"
say "  2. verify the loop:   →  aimail welcome"
say "diagnose: aimail check · aimail stats"
