#!/usr/bin/env bash
# V2 release gate: SDK gates + three host L2 + real two-Gateway L2.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
echo "== L0 =="
bash tests/sdk/l0-gate-tests.sh
echo "== L2 dsh =="
./tests/host-acceptance/run-l2-local-gateway.sh dsh
echo "== L2 pi =="
./tests/host-acceptance/run-l2-local-gateway.sh pi
echo "== L2 openclaw =="
./tests/host-acceptance/run-l2-local-gateway.sh openclaw
echo "== L2 two gateways =="
./tests/host-acceptance/run-l2-two-gateways.sh
echo "V2 RELEASE GATE PASS"
