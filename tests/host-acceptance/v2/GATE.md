# L2 / L3 gate usage (SSE-V2)

This is the **gate** contract: what to run, what the results mean, and what is
deliberately out of scope. Doctrine lives in `tests/agent-env/pins.env` and
`tests/host-acceptance/v2/README.md`.

> 续做与发版前置条件见 `docs/qa/HANDOFF-L2-FIXTURE-CONTINUATION.md`（本轮契约 v2.3 之后的夹具清单：
> `s7`/C17 需宿主钩子、`s8`/C16 由 dsh 钩子提供证据（其余宿主记 GAP）、`s4`/C18 的证据分级）。

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
| `N/A` | the obligation does not apply to this host (e.g. C17 on a `no-session-model` host, 附 E) — printed, never counted | no |
| `RISK-ACCEPTED` | a measured defect the **owner has signed off** for this release (must carry `HOST_RISK_ACCEPTED` reference + printed evidence) — printed, never counted | no |

`RED>0 or GAP>0` ⇒ exit code 1. `SKIP_LEGACY` is printed but never counted as green —
a run with skips is not a green run, it is a green run *for its scope*.

## Stage map

| stage | asserts |
|---|---|
| `s1` | public `/apply` code → in-host `aimail_address(bind)` → binding 0600 with GatewaySystemId / AgentSystemId / workspace, no webhook, composed profile carries `tool-mail` |
| `s2` | after restart the aggregate pull arms (`polling enabled` / `pull scope` / `SSE reconcile: N … from M binding(s)`) and it arms *after* the last "no agent-scope binding" boot. **No local listener exists** (retired 2026-11-25): a binding carries no webhook_url and nothing answers on a loopback port |
| `s3` | whitelisted inbound → `emails` row → `pending_deliveries` row → drained (ack) — the inbound ladder; nothing asserted about model wording |
| `s4` | two addresses coexist on multi-address hosts (one aggregate stream covers both); a single-address host rejects the second with the **failed layer** (`C1` + `C18`: `failed: "wake"`). A paraphrase-only log is accepted but reported as the weaker evidence, never as C18 |
| `s5` | `pause` marks the binding paused; `unbind` removes it |
| `s6` | contract C8/§9 host side: new thread ⇒ new session, reply ⇒ same session (E3/E1+E2), all sessions in the binding's anchor。按 `HOST_CAN_ALLOCATE_SESSION` 分支：`0` 的宿主（会话由 CLI 决定）只判"三封都被 ack + thread root"，分配半条记 `N/A`（§16 C8 证明范围） |
| `s7` | contract C17/§9.3: after the target session is deleted, a reply is redirected to a new session, acked, and recorded as `session-gone`。按 `HOST_DELIVERY_CONFIRMATION` 分支：`no-session-model`（pi）记 `N/A`；`probe` / `unconfirmed` 都**受判**——缺 `s7_gone.json` 记 GAP，有证据则按 `used`/`reason` 判 RED（`unconfirmed` 不得用 N/A 掩盖实测丢信） |
| `s8` | contract C16/§9.2 I2（锚点隔离）：把 thread 指针指向**另一个锚点**的会话（该会话仍然存活），回信必须被 ack、**落在当前锚点**、并在 dispatch 行记 `anchor: anchor-moved`（带被弃边与外来锚点）。`HOST_CAN_ALLOCATE_SESSION=0` 的宿主记 `N/A`（没有第二个锚点可跨）；缺 `s8_anchor.json` 记 GAP，`rewritten=0`（场景没搭起来）同样 GAP，绝不记绿 |
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
| `C17-RISK-1` hermes / openclaw 的 C17 = `RISK-ACCEPTED`（owner 2026-11-25） | 这两家没有公开的落地确认 API（hermes 只有 `_gateway_accepted`；openclaw 的 200 是 cron 入队），且 rollout 方不会为我们开放接口。owner 书面接受"可能 ack 而未落地、系统不重投"的风险；`s7` 打印实测证据并记 `RISK-ACCEPTED`（不计绿/红）。deerflow 已改为 `probe`（投递后回读 run），不在此列 |
| `C17-SCOPE-1` C17 的证据口径（三值） | `HOST_DELIVERY_CONFIRMATION`：`probe`（可查会话存在性）/ `unconfirmed`（只能确认接管，**仍受判**）/ `no-session-model`（无会话可分配，义务为空 ⇒ `N/A`）。只有后者可 N/A；`unconfirmed` 上实测 ack-零落地 = RED。见 `docs/qa/RESPONSE-TO-REVIEW-C8-P0-1-P1-1.md` |
| `PI-C8-17-1` pi 的 C8 分配半条与 C17 = `N/A`（契约 §16 C8/C17 证明范围 + 附 E） | 依据（已核 pinned pi 1.5.2）：`newSession`/`fork`/`switchSession` 只存在于 `ExtensionCommandContext`（`dist/core/extensions/types.d.ts:303-336`，注释 "only safe in user-initiated commands"），后台投递只持有当前会话；会话身份由 CLI 调用（`--session-id` / `--continue` / `--session`）决定。**仍判**：三封信都被 ack、thread root 稳定、第二地址被拒（C1/C9）。s6 记 4 条 `N/A`，s7 记 `N/A` |
| `ACK-1` ack 语义 vs 宿主能力（P0-1 未闭环项） | openclaw 的 200＝cron job 入队、pi 的返回＝交给宿主 API，二者都拿不到"已落进 transcript"的确认；插件面没有 run/session 查询。修复见 `docs/qa/HANDOFF-L2-FIXTURE-CONTINUATION.md` §4.6.1；**C17 的可证性取决于 owner 选择 A/B/C**，未选定前不要把 C17 记作已证明 |
| `OC-C8-1` openclaw C8（会话复用）**不是宿主限制** | 宿主支持 `sessionKey` + `sessionMode:"persistent"`；默认 `isolated` 才会每次新开会话。适配器已修复：投递侧发 `sessionKey + persistent + idempotencyKey`，入站按 `selectSessionKey()` 先 E1（父信 meta 的 `session_key`）再 E2（thread 哈希）。见 `docs/qa/HANDOFF-L2-FIXTURE-CONTINUATION.md` §4.5。**不要按 EXEMPTED 处理**；重跑 s6 应看到 `sessionKeySource` 与 `sessionModeRequested: persistent` |
| `S7-HOOK-1` no host implements `host_s7_session_gone` yet | the `s7` stage reports `GAP: C17 unproven`. The case already consumes `/out/s7_gone.json` (`{acked, deleted, used, reason, root_ok}`), so wiring one host turns it green without touching the assertions. `s7` is opt-in (default stages stay `s1 s2 s3`), so an unimplemented hook does not poison the C8 stage. |
| `DSH-ONE-PATH`（2026-11-25） | dsh 的出货投递路径与夹具此前不是同一条：bundle 只挂 `mail-service`/`tools`/`mail-hub`，而 `mail-hub` 用自己的 `routeThread`（只有 E2、无 dispatch 行），`dsh/inbound.ts`（E1/C16/C17 + 日志）根本没被挂载。现已统一：`mail-hub.deliver()` → `deliverInbound()`（`decideInboundDelivery` + `logAimailDispatch`），`routeThread` 删除。**s6/s7/s8 从此测的就是出货链路** |
| `C16-DECIDED`（2026-11-25，开发裁定 + owner 口径） | **不签 N/A**：核查发现 E1（直接回复）此前**完全不校验锚点**——父信 meta 里 `workspace_path` 指向别的 workspace 时，回信会被直接投进那个外来会话（I2 违规）。已修：`runtime/routing.ts` 的 E1/E2 都做空间比对，命中外来锚点即放弃该边、在当前锚点重排，并带出 `anchorMoved{edge,space}`；dsh 把它写进 dispatch 行（`anchor: anchor-moved`）。判定口径：**能分配会话的宿主都受判**（`HOST_CAN_ALLOCATE_SESSION=0` 记 `N/A`，因为没有第二个锚点可跨），不再是"夹具能力待定"。单测已覆盖（routing 13 项 + dsh inbound 10 项），L2 证据由 `s8` 提供 |
| `S8-HOOK-1` only `dsh` implements `host_s8_anchor_isolation` (2026-11-25) | Since the dsh unification (below) this hook finally exercises the **shipped** path: `mail-hub` calls `deliverInbound`, which is where `anchorMoved` is recorded. `s8` consumes `/out/s8_anchor.json` (`{acked, moved, edge, moved_from, rewritten, foreign_session, used, leaks}`); dsh drives it via `lib/s8_drive.py` (rewrites the thread pointer's `workspace_path` to a foreign anchor, keeps the session alive). Other hosts report `GAP: C16 unproven` — tracked, never green. Wiring one host's hook turns its row green without touching the assertions. `s8` is opt-in (default stages stay `s1 s2 s3`). |
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

C8 (`docs/aimail-host-sse-contract-v2.md` §16 C8 / §9, v2.3: **E1 direct reply > E2 thread > E3 alloc**;
E1 hard inside the anchor, E2 may re-attach, E3 prefers a new session and may fall back to the anchor's
current one) had **no host-side evidence in either SDK** — no L2/L3 stage ever sent
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

**openclaw (tssdk) — RESOLVED as a HOST LIMITATION, not our defect (2026-11-24):**

Four experiments, in order:
1. fixture config with only `agent:` in `allowedSessionKeyPrefixes` → gateway refuses to start:
   `hooks.allowedSessionKeyPrefixes must include 'hook:' when hooks.defaultSessionKey is unset`.
   That — not the product — is why "switch on ⇒ nothing acked".
2. isolated POST /hooks/agent: `agent:main:aimail:<hash>` → 200; `badkey` → 400
   `sessionKey must start with one of: agent:, hook:`. So the host accepts our key shape.
3. the plugin's own dispatch log (`$AIMAIL_HOME/systems/<sid>/<addr>/agentmail.log`) shows it really
   asks for reuse: `"sessionKeyRequested": true, "sessionKey": "agent::aimail:17cab5d7…"`.
4. two identical POSTs with the SAME key, twice each shape:
      agent::aimail:aaaa     → 200, 200  → sessions 0 → 2
      agent:main:aimail:bbbb → 200, 503  → sessions 2 → 4
   The host creates a NEW session per hook run even for an identical caller key, and those sessions are
   not even listed under an `aimail` key. So on this host `sessionKey` is not a session-identity key.

Verdict: C8's first half cannot hold on openclaw through the hook surface — a host capability gap, not a
plugin defect. Recorded as a contract exception pending an owner ruling (accept it in the contract, or
pursue a host-side feature).

Separate product nit found on the way: our dispatch carries `agentId: ""`, so the key degenerates to
`agent::aimail:<hash>` (double colon) while the host's own keys look like `agent:main:cron:<uuid>`.
Even on a host that honored keys, that empty segment would collapse every agent into one namespace.

**Earlier note (superseded by the above):**

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

### s7 — C17 first real measurement (dsh, 2026-11-25): RED, product defect

Hook implemented (`lib/s7_drive.py`): send mail A in a new thread → delete the session it landed in
(host store) → reply to A → record ack / where it actually landed / root survival. Three runs, same
outcome:

```
mail A → session 7cf927e4… ; deleted ; reply → acked=True ; new sessions: none ; files written: 0
```

So the reply is ACKED and then dropped — and an ack stops the gateway retrying, which means an owner's
approval reply can vanish silently. Filed as `docs/qa/DEFECT-C17-SESSION-GONE.md` (S1). Root cause is
in the SDK: `runtime/routing.ts` returns `direct` from the meta index without checking that the session
still exists, there is no `session-gone` producer anywhere in `src/`, and the pull path acks on the
plugin's "delivered", which is itself just the host's HTTP 200.

`reason` is deliberately left empty by the driver: inventing it would hide exactly the gap the case exists
to expose.
