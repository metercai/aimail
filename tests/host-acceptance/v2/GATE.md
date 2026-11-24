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
#      *** verifier-only (post-release regression). Developers must not run L3. ***
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
  L3 runs **after** a release and is executed by the verifier only; a developer's
  deliverable is judged by L2, never by L3.
- `hermes` / `deerflow` (pysdk) are **gated, not skipped** — `SKIP_LEGACY=0` since 2026-11-24:
  hermes `s1 s2 s3 s4 neg` = PASS=14 (S4 asserts the single-address refusal, C1);
  deerflow `s1` = PASS=7, `s2 s3 s4 neg` = PASS=8, `s5` = PASS=2.
  L3 scope is stated once in `tests/l3/GATE.md` (4 platforms × dsh).
- Both layers download the **published** gateway pinned in `pins.env` (sha256 verified).
- **Install path = host plugin mechanism only** (`dsh plugin add` / `pi install` /
  `openclaw plugins install`). The SDK ships no CLI and `aimail install` is retired;
  no gate step may invoke an SDK CLI subcommand (see REQ-12).

## Known open items (not fixture defects)

| id | effect on the gate |
|---|---|
| `ING-1` gateway relays inbound SMTP to the outbound relay instead of storing it | `s3` cannot pass; `emails=0` is the signature. Reported for the product fix. |
| ~~pi live delivery~~ **wired 2026-11-23** | `hosts/pi.sh` `_pi_delivery` now runs one long-lived `pi --mode rpc` session in the journey HOME (docker `-i` is required, otherwise the container gets /dev/null on stdin and pi exits at once), injects into `$((GW_SMTP+1))` and waits for the pull queue to drain. Evidence: pi `s1 s2 s3` = PASS=12 RED=0 GAP=0, `inject.log` = `sent / observed max=1 final=0 / emails=1 / acked_after_poll=1`. Attribution rule: `arming_seen=1` + not acked ⇒ RED (product); `arming_seen=0` + not acked ⇒ GAP (fixture never attached the pull client). |

## Journey scoping (measured 2026-11-23, gateway v1.0.12)

A single `run-matrix.sh` invocation cannot carry every stage. The runner derives the
journey from `--stages`, and two stages change the journey itself:

| if the stage list contains | the journey does | consequence |
|---|---|---|
| `s5` | `LIFECYCLE=1` — pause/unbind, and the host-side mail injection is **skipped by design** (there is no binding left to deliver to) | `s3` reports `GAP: no injection evidence` in that run — run it separately |
| `s3` + `s4` together | mail is injected after 2 bindings exist and the negative step restarts the host afterwards | on 2026-11-23 the `neg` stage ended with `GAP: negative stage did not complete (rc: no web url)` — the neg host did not come up; `RED=0` throughout, so this is a fixture-completion gap, not a product red |

Recommended split (each invocation is one journey):

```bash
bash tests/host-acceptance/v2/run-matrix.sh --host dsh --stages "s1 s2 s3 s4 neg"   # delivery evidence
bash tests/host-acceptance/v2/run-matrix.sh --host dsh --stages "s5"                 # lifecycle
```

A GAP is not a pass: the gate stays non-zero. It is also not a product defect — do not
"fix" a GAP by relaxing an assertion; either complete the fixture or re-run the stage in
the scoping above.
