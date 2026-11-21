#!/usr/bin/env bash
# V2 release gate: SDK gates + three host L2 + real two-Gateway L2.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
echo "== L0 =="
bash tests/sdk/l0-gate-tests.sh
echo "== L2 dsh =="
./tests/host-acceptance/run-l2-local-gateway.sh dsh
echo "== L2 dsh real-host (B4 tool surface: dev plugin installed into the web profile) =="
./tests/host-acceptance/run-l2-dsh-real-host.sh
echo "== L2 pi =="
./tests/host-acceptance/run-l2-local-gateway.sh pi
echo "== L2 openclaw =="
./tests/host-acceptance/run-l2-local-gateway.sh openclaw
echo "== L2 real-host A2 arming (pi single + openclaw multi: B1/B2 fix on real host processes) =="
./tests/host-acceptance/run-real-host-arming.sh
echo "== real-host A3/A4 (0.5.1 tgz + injected welcome => real LLM turn + ack; needs local LLM) =="
./tests/host-acceptance/run-m6-a34-pi.sh
echo "== L2 two gateways =="
./tests/host-acceptance/run-l2-two-gateways.sh
echo "V2 RELEASE GATE PASS"
