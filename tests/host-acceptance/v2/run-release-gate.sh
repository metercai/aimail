#!/usr/bin/env bash
# One command that produces the release decision for the SSE-V2 contract work.
#
# It is deliberately NOT a pass/fail wrapper: it runs every host × stage, prints the matrix, and keeps the
# N/A / EXEMPTED markers separate from green, because the owner ruled that host-identity limitations are
# recorded, not silently turned into passes (see docs/qa/RELEASE-CRITERIA.md R2/R4).
#
# Usage:  bash tests/host-acceptance/v2/run-release-gate.sh [--only dsh,hermes] [--quick]
set -uo pipefail
cd "$(dirname "$0")/../.." || exit 1
ROOT="$(pwd)"
set -a; [ -f /home/ubuntu/.hermes/profiles/verifier/.env ] && . /home/ubuntu/.hermes/profiles/verifier/.env; set +a
export LLM_API_KEY="${LLM_API_KEY:-$XIAOMI_API_KEY}"
OUT="${OUT:-/tmp/l2-release-gate}"; mkdir -p "$OUT"
ONLY=""; QUICK=0
while [ $# -gt 0 ]; do
  case "$1" in
    --only) ONLY="$2"; shift 2 ;;
    --quick) QUICK=1; shift ;;
    *) echo "unknown arg $1"; exit 2 ;;
  esac
done

i=0
row() {  # row <sdk> <host> <stages> <label>
  local sdk="$1" host="$2" stages="$3" label="$4" hp sp
  case ",${ONLY}," in *,*) [ -n "$ONLY" ] && { case ",$ONLY," in *",$host,"*) ;; *) return 0 ;; esac; } ;; esac
  i=$((i+1)); hp=$((18440 + i*2)); sp=$((12940 + i*2))
  local log="$OUT/$sdk-$host-$label.log"
  echo "── $sdk / $host  [$label]  stages=[$stages]  ports $hp/$sp"
  GW_HTTP_PORT=$hp GW_SMTP_PORT=$sp timeout 3000 \
    bash tests/host-acceptance/v2/run-matrix.sh --sdk "$sdk" --host "$host" --stages "$stages" >"$log" 2>&1
  local rc=$?
  printf '   rc=%s  %s\n' "$rc" "$(grep -aoE 'PASS=[0-9]+ RED=[0-9]+ GAP=[0-9]+ SKIP_LEGACY=[0-9]+' "$log" | tail -1)"
}

echo "════ L2 (s1-s4 + neg)"
row ts dsh      "s1 s2 s3 s4 neg" l2
row ts pi       "s1 s2 s3 s4 neg" l2
row ts openclaw "s1 s2 s3 s4 neg" l2
row py hermes   "s1 s2 s3 s4 neg" l2
row py deerflow "s1 s2 s3 s4 neg" l2

if [ "$QUICK" = 0 ]; then
  echo
  echo "════ C8 (s6) — pi only where an s6 hook exists"
  row ts dsh      "s1 s6" c8
  row ts openclaw "s1 s6" c8
  row py hermes   "s1 s6" c8
  row py deerflow "s1 s6" c8
  row ts pi       "s1 s6" c8
  echo
  echo "════ C17 (s7) — the one that blocks the release until P0-1 lands"
  row ts dsh      "s1 s6 s7" c17
  row py hermes   "s1 s6 s7" c17
  row py deerflow "s1 s6 s7" c17
fi

echo
echo "════ verdict table (green here is what R2/R4 read)"
for f in "$OUT"/*.log; do
  printf '%-32s %s\n' "$(basename "$f" .log)" \
    "$(grep -aoE 'PASS=[0-9]+ RED=[0-9]+ GAP=[0-9]+' "$f" | tail -1)"
done
echo
echo "════ every RED/GAP (grouped, for triage)"
grep -ahE "^RED|^GAP" "$OUT"/*.log 2>/dev/null | sed 's/^[[:space:]]*//' | sort | uniq -c | sort -rn | head -30
echo
cat <<'NOTE'
Reminder of what this table does NOT decide:
  * pi / C8, pi / C17, openclaw / C8 are host-session-identity items: N/A or EXEMPTED only with the owner's
    written signature recorded in GATE.md. Green here never overrides that.
  * C17 (dsh/hermes/deerflow) is defect P0-1 and stays RED until the SDK routes around a vanished session.
NOTE
