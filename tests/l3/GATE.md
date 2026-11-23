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

Known accepted flake: a single Windows cell red with `No reply within …s` plus gateway
`partial MX delivery failure` — record it, rerun once, escalate on a repeat.
