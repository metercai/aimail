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

### s6 — contract C8 host-side evidence (added 2026-11-24)

C8 (`docs/aimail-host-sse-contract-v2.md:519`: same thread ⇒ same session, new thread ⇒ new session,
space = cwd) had **no host-side evidence in either SDK** — no L2/L3 stage ever sent
`In-Reply-To`/`References`, and both SDKs only pinned the decision logic in unit tests.

`s6` closes that. It runs inside the same journey container (so the binding survives), injects three
mails — a new thread, a reply to it, then a second new thread — and judges the HOST's own session
store rather than the plugin's arithmetic:

```
same thread  ⇒ no NEW session key for the reply, session count unchanged
new thread   ⇒ exactly one new key
no readable session evidence ⇒ FIXTURE GAP (never a pass)
no workspace reported by the host ⇒ the "space = cwd" half stays GAP
```

Evidence produced by the host adapter into `<journey>/s6.json`; the judgement lives in
`cases/s6_threading.sh` and is itself tested offline by `selftest-s6-case.sh`
(GOOD evidence ⇒ 0 RED, BAD evidence ⇒ 3 RED).

**hermes (pysdk) — PASS, measured 2026-11-24:**
```
step0 3 sessions
mail A (new thread)      -> 4 sessions, new key agent:main:aimail:dm:aimail:376d41b55841
reply (References: [A])  -> 4 sessions, no new key            ← same session reused
mail B (new thread)      -> 5 sessions, new key …449a808598a7  ← new session
all three mails acked by the host; space reported /opt/l2home
```
Measurement traps paid for here (do not repeat): the store is `$HERMES_HOME/sessions/sessions.json`
(one JSON, keyed by chat_id) — `HERMES_HOME` is `/opt/l2home`, **not** `/opt/data`; and "session2"
must mean "the session that handled the reply", never "the key that newly appeared", or a correctly
reused session reads as a violation.

**deerflow (pysdk) — PASS, measured 2026-11-24 (run 4):**
```
step1 3 threads  mail A (new thread)      -> new thread 7b1a2a03-bdba-5901-a538-01d0769b93d8
step2 3 threads  reply (References: [A])  -> no new thread (3 -> 3)      ← same host thread
step3 4 threads  mail B (new thread)      -> new thread cdb60deb-…      ← new host thread
acked [true,true,true]; space = /app
```
Three fixture defects were paid for on the way, all of them mine — worth knowing before touching
this host:
1. **`$!` is not the listener.** Phase 1 killed `$!` and phase 2 still hit
   `EADDRINUSE`; `port_free_after=60` proved the port never freed, the app lost its channel manager
   (`SSE reconcile: 0`) and no mail was ever delivered. The listener is now killed from OUTSIDE the
   container by process name.
2. **No single quotes inside the `docker exec … bash -lc '…'` body.** One `awk '…'` terminated the
   string and phase 1 died with zero evidence (my run 3). `bash -n` does not catch this.
3. Session identity must be read from the host store (`/app/backend/.deer-flow/data/deerflow.db`,
   tables matching `*thread*`), not computed in the fixture — the uuid5 thread id is derivable, but
   deriving it would only verify my own arithmetic.

**dsh (tssdk) — PASS, measured 2026-11-24 (run 4):**
```
mail A (new thread)     -> host session 3a1b5575-f0d8-42f3-8db8-8255506a6a40 (3 sessions)
reply (References: [A]) -> no new session (3 -> 3)                        ← same host session
mail B (new thread)     -> host session 7f3c2355-5b80-40cd-830f-212a47dfc5cb (3 -> 4)
acked [true,true,true]; space = /root/ws
```
Session identity comes from the host's own store directories under `$DSH_HOME/sessions/<ws>/<id>/`.
Four fixture defects were paid for here, all mine — the dsh adapter is a one-shot container with two
startup branches, so read this before touching it:
1. **The container is one-shot.** `docker exec` after it exits has nowhere to go, so the probe cannot
   run from outside (run 1: `acked=[true,false,false]`, empty snapshots).
2. **A single quote anywhere in the `bash -lc '…'` body kills the body.** Same class as deerflow.
3. **Don't compute the stage flag with nested quotes**: `-e "S6=$(case … esac)"` collapsed to 0 and the
   whole driver was skipped silently (run 2). Compute it in the outer shell.
4. **Put the probe after the `if/else`, not inside a branch.** The adapter has two startup paths; my
   block lived in the branch these runs never take (run 3: `ready` existed, `s6_drive.log` did not).
   The probe now lives in `lib/s6_drive.py`, bind-mounted, so it also avoids quoting entirely.

**openclaw (tssdk) — OPEN FINDING, not yet a verdict (2026-11-24):**

Measured from the host's own list (`openclaw sessions --json --limit all`), which is the only
readable identity here:
```
mail A (new thread)     -> session c3f8a845-…  (3 sessions)
reply (References: [A]) -> NEW session 48c7ca14-…  (3 -> 4)   ← C8 first half does NOT hold
mail B (new thread)     -> new session 44939e83-…  (4 -> 5)
acked [true,true,true]; space = /app/.openclaw/workspace
```
Two causes must be separated before this is called a product defect, and the current evidence does
not separate them:
1. the reply may not have carried `In-Reply-To`/`References` into the plugin on this host (the case
   currently ASSERTS root1 == root2 from the fixture instead of measuring the plugin's view — a
   weakness of my judgement, not of the product);
2. openclaw assigns session ids itself (its own chat/session semantics), in which case our
   thread-root routing cannot control session identity there — a contract gap needing a decision,
   not a code fix.

Measurement traps paid for here (do not repeat):
* reading `openclaw-agent.sqlite` directly mixes id-spaces (row ids like "3", chat ids, 40-bit
  counters): counts read 22 → 25 → 32 and looked like a violation. Use the host's own command.
* `session_transcript_active_events` is one row per transcript event (16–28 per mail), never a
  session count.

**Still to cover:** the openclaw verdict above, and the tssdk unit suite should consume
`contract/c8-thread-vectors.json` instead of keeping a private copy of the C8 cases.
Also outstanding: the tssdk unit suite should consume `contract/c8-thread-vectors.json` instead of
keeping its own copy of the C8 cases.
