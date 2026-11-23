# L3 gate contract — post-release, dsh only (tssdk path)

**Verdict scope: this file is the single statement of what L3 covers.** If a document,
dashboard or release note claims a different cell set, this file wins.

## 1. What L3 is

L3 = the **published** SDK, installed **by the agent itself inside the journey**, run against
the **published** gateway, end to end, on a real OS/CPU with a real LLM. Only closed-loop
regression is in scope: code → prompt → plugin install → activate → address-code bind →
welcome → identity card + signature.

Not in scope: unit behaviour, contracts, install ergonomics on a dev machine, any artifact
built from the working tree.

## 2. Matrix — 4 platforms × 1 agent = 4 cells

| platform | runner | agent |
|---|---|---|
| linux-amd64 | `ubuntu-latest` | dsh |
| linux-arm64 | `ubuntu-24.04-arm` | dsh |
| macos-arm64 | `macos-latest` | dsh |
| windows-amd64 | `windows-latest` | dsh |

Workflow: `.github/workflows/l3.yml` (matrix `include` above; `workflow_dispatch` input
`cell=<platform>/<agent>` filters inside the job — a job-level `if` breaks dispatch, 422).

Executable: `tests/l3/run-l3.sh` per cell, which hands the journey to `tests/l3/dsh-v2.sh`.
Local wrapper: `tests/l3/run-v2.sh [--cell platform/agent]`.

## 3. Why dsh only

Owner ruling 2026-11-23. The retired cells and the reason each was retired:

| retired cell | reason |
|---|---|
| `dsh-aimail` (CLI entry) | the CLI install path (`aimail install`) is no longer shipped; the host plugin mechanism is the only install path |
| `hermes` (pysdk) | pysdk has not migrated to SSE-V2 → reported `SKIP_LEGACY`, never executed, never counted green |
| `pi`, `openclaw` | L2 scope — covered on this machine's OS+CPU, not an L3 cross-platform cell |

Fail-closed enforcement: `run-l3.sh` refuses any `L3_AGENT` other than `dsh` at the top
(`rc=1`, explicit message) instead of falling back to a retired path. `run-v2.sh` prints
`SKIP_LEGACY` for `hermes` without executing it. Consequence: editing the matrix alone can
never re-enable a retired cell.

## 4. Commands

```bash
# whole L3 (4 cells; the wrapper runs them locally one after another)
export LLM_API_KEY=…                 # runtime only, never committed
bash tests/l3/run-v2.sh

# one cell
bash tests/l3/run-v2.sh --cell linux-amd64/dsh

# CI (verifier only, post-release)
gh workflow run l3.yml -f cell=linux-amd64/dsh
```

Who may run: **verifier only**. A developer's deliverable is judged by L2; a developer run
of L3 is context, not evidence.

## 5. Result vocabulary

| token | meaning | affects verdict |
|---|---|---|
| `PASS` | assertion met | no |
| `RED` (exit 1) | assertion failed → product defect until attributed otherwise | yes |
| `GAP` (exit 2) | environment/incomplete prerequisites — never a pass | yes |
| `SKIP_LEGACY` | pysdk path not migrated | no |

A run with `SKIP_LEGACY` is green **for its scope**, not a green run.

### 5.1 A filtered cell's `success` means "not run"

With `workflow_dispatch -f cell=<platform>/<agent>`, the other cells keep a runner slot but
every step is skipped, so the job reports `success`. Their log says
`::notice::skipped by dispatch filter`. Count only the dispatched cell; never read a
one-cell dispatch as "4/4 green".

## 6. Evidence per cell

- `l3-<platform>-<agent>-logs` artifact: full stdout, gateway log, dsh host log, session
  persistence, delivery ledger excerpts (`dsh-v2.sh` dumps these on failure).
- Gateway pin + sha256 from `tests/agent-env/pins.env` (both layers read the same file;
  move it only through `update-pins.sh`).
- dsh version must equal the `DSH_VERSION` pin — checked right after ① installs it; a
  mismatch is a `GAP` (refuse to judge), not a red.

## 7. Paid-for incidents (do not reintroduce)

| symptom | root cause | guard now |
|---|---|---|
| every cell died at ① with `AGENT: unbound variable`, rc=127 | the host-version pin check sat **before** `AGENT=` was assigned, under `set -u`; it also read `dsh --version` before ① installed dsh | pin check moved after ①; assigning `AGENT` precedes any use |
| 9 cells advertised, 5 of them re-running a retired CLI path | matrix was never reduced when the install path was retired | matrix is 4 dsh cells; `run-l3.sh` refuses other agents |
| two "install semantics" (CLI vs plugin) in one gate | the CLI tail (④ install → ④b webhook host → ⑤ `aimail welcome` → ⑥ CLI signature probe) outlived the CLI | CLI tail deleted; `run-l3.sh` fails loudly if the journey ever falls through |
| every cell died at ② with `LOGDIR: unbound variable` | the per-cell gateway-pin evidence redirected to `$LOGDIR`, never assigned; `set -u` makes that fatal right after the gateway sha256 check | writes to `$LOG_ARCHIVE`; observed in run 45109593307 / job 137478660334 |
| `pins.env: line 27: GATEWAY_SHA256_linux-amd64=…: command not found` (×4) in every run | the file is *sourced*; `-` in a key is not a valid assignment, so bash executed the line — rc=127 aborts `host-images.yml` under `set -e`, and only polluted L3 | keys renamed to `GATEWAY_SHA256_linux_amd64` / `_linux_arm64` / `_macos_arm64` / `_windows_amd64`; the two grep consumers translate with `${plat//-/_}` |

Known accepted flake: a single Windows cell red with `No reply within …s` plus gateway
`partial MX delivery failure` — record it, rerun once, escalate on a repeat.

## 8. Matrix expansion: pi / openclaw — and the bootstrap doctrine

### 8.1 What L3 must simulate (owner ruling 2026-11-23)

L3 is a **user**, not a test harness. The journey starts with an applicant asking the gateway
for an activation code over the mail path, and the agent environment starts **clean** (no SDK,
no plugin). Installing the published SDK is **the agent's own bootstrap**, triggered by the
activation prompt — the fixture must never run the install.

The activation mail body already carries the instruction (advanced `email_notify.rs:149`):
> Activate and bind my AIMail mailbox address {address} with activation code {raw_code} at
> gateway {gw} using `aimail_address`. If `aimail_address` is unavailable or not registered,
> install the `agent_aimail` plugin SDK first: from PyPI via pip for Python, or from npm for
> Node.js. Then retry `aimail_address` with the same parameters.

So the fixture's only jobs are: apply for the code, hand the mail body over verbatim as a user
turn, and verify what happened. Anything the fixture installs itself is a **fake pass**.

### 8.2 Assertions that make "self-bootstrap" provable

1. before the turn: no `agent-aimail` in the host's plugin registry and no binding on disk
   (a pre-existing plugin means the environment was not clean — GAP, not PASS);
2. after the turn: the registry contains `agent-aimail@<npm latest>` installed **during the
   turn**, and the host transcript shows the agent's own install command (bash/tool call);
   the fixture's own command log must show no install;
3. then the bind: binding file with `agent_system_id=asid_…`, `gateway_url`, no `webhook_url`;
4. then the closed loop: welcome queued → applicant approves → `whoami` returns persona +
   signature;
5. install evidence and bind evidence are asserted separately, so "agent installed but never
   bound" cannot be reported as a generic failure.

### 8.3 Per-host mechanics (everything else is shared with the dsh journey)

| step | dsh (today: **fixture installs — to be reconciled**) | pi | openclaw |
|---|---|---|---|
| install by the agent | `dsh plugin add agent-aimail` must move into the agent's shell turn | agent runs `pi install agent-aimail@latest` (or npm) in its bash tool | agent runs `openclaw plugins install agent-aimail@latest` |
| user turn channel | dsh web API session RPC | `pi -p "<mail body>"` | `openclaw agent --local "<mail body>"` |
| install applied | host restart | next `pi` process | next `openclaw` run |
| bind evidence | binding file + `status.json` SSE | binding file + plugin log | binding file + `SSE reconcile` |
| LLM evidence | `sessionStats.llmMs` | assistant text in the turn output | turn output + dispatch log |

**Deviation to fix on dsh**: `dsh-v2.sh` step [3] has the *user* run `dsh plugin add` and step
[4] restarts the host. Under 8.1 that install must be the agent's own action; dsh stays green
either way, so this is a doctrine alignment, not a blocker — track it here until the dsh
journey is rewritten to the same shape.

### 8.4 Rule for adding cells

A cell enters `l3.yml` only after its journey passes once locally. A cell that can only die is
noise, not coverage. Platform-inapplicable gates (POSIX modes on Windows, llmMs counters the
host does not expose) are reported as skipped-with-reason and never counted as passes.

### 8.5 dsh self-bootstrap: first PASS under the doctrine (2026-11-23, local linux-amd64)

```
[1] 申请人在网关申请激活码（走邮件），正文原样作为用户消息
[2] 干净 profile（无插件、无 binding）收到正文
[3] ✓ clean state confirmed
    user nudge（纯文字）→ ✓ agent bootstrapped the plugin itself:
      agent-aimail@0.5.2 (= npm latest), installed_by=agent, fixture ran no install
    transcript 证据: plugin_manager {"action":"install_bundle","target":"agent-aimail"}
[4] ✓ 重启宿主（插件在下一次启动生效）
[5] ✓ binding 落地 / ownership row / mode 600 / no webhook_url / SSE streams non-empty
[6] ✓ welcome 入队 → manager approve → persona-applied
[7] ✓ whoami: persona='L3 dsh V2 regression persona', signature='-- L3 dsh V2'
VERDICT: PASS
```
pins: gateway v1.0.14, npm agent-aimail 0.5.2, dsh 0.2.2-rc.1.

判决条件（不得放宽）：安装必须是 agent 自己发起（宿主 plugin_manager 工具、或宿主的 CLI、或 npm/pnpm 命令），
夹具零 install；环境必须在安装前干净；夹具泄露（transcript 出现 tests/l3）⇒ INVALID，不判产品。

### 8.6 pi self-bootstrap: PASS (2026-11-24, local linux-amd64)

```
[2] clean pi env (no plugin, no binding, no settings reference)
[3] ✓ agent bootstrapped the plugin itself: agent-aimail@0.5.2 (= npm latest), installed_by=agent
[4] next pi process picks it up
[5] ✓ binding landed (agent_system_id=asid_…, gateway_url, no webhook_url)
[6] ✓ welcome queued → applicant approves → persona applied
[7] ✓ whoami: persona + signature in effect
VERDICT: PASS
```
Host hooks: `pi_host_turn` (one `pi -p` per user turn), `plugin_pkg` (find under `$PI_HOME`),
no llmMs counter (turn output is the evidence), no daemon to restart.

Traps this brought out, all fixture-side (each one would have produced a wrong verdict):
1. `pi`'s settings are **declarative** (`packages: ["npm:agent-aimail"]`): pi re-installs the package
   by itself on start, so a leftover settings entry is indistinguishable from an agent self-bootstrap.
   Clean state now includes clearing that reference, and the clean-state assertion rejects it.
2. a welcome that is delivered+acked leaves no `pending_deliveries` row; querying only the queue made
   a correct calibration look like a failure (false RED). The evidence now comes from `emails` first.
3. `run-v2.sh` reported PASS with zero cells after silently skipping pi — zero cells now exit FAIL.
