# L2 / L3 gate usage (SSE-V2)

This is the **gate** contract: what to run, what the results mean, and what is
deliberately out of scope. Doctrine lives in `tests/agent-env/pins.env` and
`tests/host-acceptance/v2/README.md`.

## Commands

```bash
# L2 — dev build, pre-installed, this machine's OS+CPU, three tssdk hosts
export LLM_API_KEY=…                      # Xiaomi key, runtime only
bash tests/host-acceptance/v2/run-matrix.sh --host all \
     --stages "s1 s2 s4 s5 neg"

# L2 — add delivery once the gateway ingress bug (ING-1) is fixed
bash tests/host-acceptance/v2/run-matrix.sh --host all \
     --stages "s1 s2 s3 s4 s5 neg"

# L3 — published SDK installed by the agent, cross-platform, dsh only
bash tests/l3/run-v2.sh                     # all dsh cells + hermes as SKIP_LEGACY
bash tests/l3/run-v2.sh --cell linux-amd64/dsh   # one cell, local
```

## Result vocabulary

| token | meaning | affects verdict |
|---|---|---|
| `PASS` | assertion met | no |
| `RED` | **product** defect | yes |
| `GAP` | fixture/environment defect | yes (fix the fixture) |
| `SKIP_LEGACY` | pysdk path, not migrated to SSE-V2 | no |

`RED>0 or GAP>0` ⇒ exit code 1. `SKIP_LEGACY` is printed but never counted as green —
a run with skips is not a green run, it is a green run *for its scope*.

## Stage map

| stage | asserts |
|---|---|
| `s1` | public `/apply` code → in-host `aimail_address(bind)` → binding 0600 with GatewaySystemId / AgentSystemId / workspace, no webhook, composed profile carries `tool-mail` |
| `s2` | after restart the aggregate pull arms (`polling enabled` / `pull scope` / `SSE reconcile: N … from M binding(s)`) and it arms *after* the last "no agent-scope binding" boot |
| `s3` | whitelisted inbound → `emails` row → `pending_deliveries` row → drained (ack) — the inbound ladder; nothing asserted about model wording |
| `s4` | two addresses coexist on dsh/openclaw (one aggregate stream covers both); pi rejects the second (C1) |
| `s5` | `pause` marks the binding paused; `unbind` removes it |
| `neg` | an invalid activation code writes no binding and does not arm |

## Scope

- **L2** = current dev build, pre-installed, three hosts (dsh / pi / openclaw), local platform.
- **L3** = published SDK, installed by the agent inside the journey, `dsh` across platforms.
- `hermes` / `deerflow` (pysdk) are `SKIP_LEGACY`: executed only with `--include-legacy`, never counted.
- Both layers download the **published** gateway pinned in `pins.env` (sha256 verified).

## Known open items (not fixture defects)

| id | effect on the gate |
|---|---|
| `ING-1` gateway relays inbound SMTP to the outbound relay instead of storing it | `s3` cannot pass; `emails=0` is the signature. Reported for the product fix. |
| pi live delivery | not wired (needs a long-lived pi process); `s3` reports that limitation instead of faking green |
