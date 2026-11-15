#!/usr/bin/env bash
# Native gateway tests (host Rust; no Docker). Requires the sibling repos and
# Rust 1.98.1 (the repos' pinned toolchain).
set -euo pipefail
CHAIN="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BASE="$CHAIN/aimail-gateway"
ADV="$CHAIN/aimail-advanced"

echo "== base cargo test"
(cd "$BASE" && cargo test --lib)

echo "== advanced cargo test (address + full)"
(cd "$ADV" && cargo test address)
(cd "$ADV" && cargo test)

echo "== gateway tests done (review machine_code failures on non-Linux hosts)"
