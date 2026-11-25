# L2 / L3 harness (SSE-V2) — doctrine and usage

Owner-ruled separation (2026-11-23). Both layers test the **SDK**; neither is a CLI test.

| | L2 | L3 |
|---|---|---|
| SDK under test | **current dev build** (`pnpm build` + `npm pack`) | **published latest** (the agent installs it during the journey) |
| Install | pre-installed into the host profile | the agent downloads/installs it — **the install is a test point** |
| Breadth | broad functional suite (activation, arming, delivery, multi-address, lifecycle, negatives) | closed-loop regression only: code → prompt → install → activate → address-code bind → welcome → identity card + signature |
| Platform | **this machine's OS+CPU only** | **dsh (tssdk) + hermes (pysdk)** across OS+CPU |
| Gateway | **published latest binary** (pins.env) | same |

Rule 5 consequence: a gateway defect must be fixed, gated and **released first**; both layers then
re-run against the new published binary. Never test the SDK against a dev-built gateway.

## Layout

```
tests/host-acceptance/v2/
  lib/gw.sh          download+verify published gateway (pins.env), run it, sqlite helpers
  lib/gw.py          SDK-free signed client: public /apply, whitelist, admin code
  lib/assert.sh      PASS / RED / GAP / SKIP_LEGACY + exit code
  lib/evidence.sh    per-run evidence (run.json, host.log, binding.json sanitised)
  hosts/<host>.sh    adapter: boot / install / prompt / restart / log  (only place host names appear)
  cases/s1_activation.sh  s2_arming.sh  s3_delivery.sh
  cases/s4_multiaddr.sh   (C1 + C18: refusal must name the failed layer)
  cases/s5_lifecycle.sh   s6_threading.sh   (C8 / §9 mapping priority, host-side)
  cases/s7_session_gone.sh (C17 / §9.3 downgrade; needs host hook, GAP until wired)
  cases/negatives.sh
  run-matrix.sh      --host dsh|pi|openclaw|all  --stages "s1 s2"
```

## Result vocabulary

- `PASS` — assertion met
- `RED` — **product** defect (carries the smallest evidence line)
- `GAP` — fixture/environment defect; never counted as product red, must be fixed by QA
- `SKIP_LEGACY` — pysdk hosts (hermes/deerflow) not migrated to SSE-V2; printed, **never** counted as green

Exit code is non-zero when `RED>0` or `GAP>0`.

## Running

```bash
export LLM_API_KEY=<xiaomi key>          # real LLM, no proxy
bash tests/host-acceptance/v2/run-matrix.sh --host dsh
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2"
```

Evidence lands in `evidence/l2/<ts>-<host>-<case>/`.

## Status (round 1)

- Built and verified: published-gateway fixture, `/apply` public provisioning (including the
  `email_verify_code` requirement introduced by gateway v1.0.11), adapters for dsh/pi/openclaw,
  S1 (activation) + S2 (arming), evidence + vocabulary.
- Current verdict on the 0.5.1 dev build: **DSH S1 RED** — the session never emits a tool call
  (`turn/start` + `step/start` only), i.e. the open B4 (session tool surface) defect.
- Next: S3 delivery / S4 multi-address / S5 lifecycle / negatives; L3 runner (`tests/l3/run-v2.sh`)
  with pinned gateway + evidence.json; CI job.
