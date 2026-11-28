# L3 matrix — first fully green run (4 platforms × dsh/pi)

- run: https://github.com/metercai/aimail/actions/runs/46152522625
- ref: `main` @ `a03dadb0`
- result: **8/8 cells success** (L3 linux-amd64/arm64, macos-arm64, windows-amd64 × dsh, pi)
- every cell: the agent bootstrapped the published SDK itself
  (`agent-aimail@0.6.1` = npm latest) and the closed loop completed — bind →
  welcome → three-tag reply → persona approval → persona-applied notice →
  whoami persona + signature read-back.

| cell | bootstrapped by the agent | log |
| --- | --- | --- |
| linux-amd64 / dsh | `/home/runner/.dsh/profiles/web/node_modules/agent-aimail` | `L3_linux-amd64___dsh.assertions.txt` |
| linux-amd64 / pi | `/home/runner/.pi/agent/npm/node_modules/agent-aimail` | `L3_linux-amd64___pi.assertions.txt` |
| linux-arm64 / dsh | `…/.dsh/profiles/web/node_modules/agent-aimail` | `L3_linux-arm64___dsh.assertions.txt` |
| linux-arm64 / pi | `…/.pi/agent/npm/node_modules/agent-aimail` | `L3_linux-arm64___pi.assertions.txt` |
| macos-arm64 / dsh | `/Users/runner/.dsh/profiles/web/node_modules/agent-aimail` | `L3_macos-arm64___dsh.assertions.txt` |
| macos-arm64 / pi | `/Users/runner/.pi/agent/npm/node_modules/agent-aimail` | `L3_macos-arm64___pi.assertions.txt` |
| windows-amd64 / dsh | `/c/Users/runneradmin/.dsh/profiles/web/node_modules/agent-aimail` | `L3_windows-amd64___dsh.assertions.txt` |
| windows-amd64 / pi | `/c/Users/runneradmin/.pi/agent/npm/node_modules/agent-aimail` | `L3_windows-amd64___pi.assertions.txt` |

`*.assertions.txt` are the fixture's own ✓/✗/⊘ lines with the runner timestamp
stripped: the cell verdicts, not a curated selection.

## The five fixture defects this run closed

All five were found by attributing the previous matrix (run 46150466725) to its
real cause instead of reading the cells as product failures:

1. **`l3.local` did not resolve on any runner** (`c7fa518f`). The activation mail
   carries `gateway: http://l3.local:18123`; nothing resolved that name on a CI
   host, so every bind turn died. `_ensure_l3_hosts` maps it to loopback before
   the gateway starts.
2. **The persona-applied notice was judged on a transient row** (`a590e137`).
   `pending_deliveries` is deleted on ack and the SSE stream acked the notice in
   9 ms, while the agent's ledger already showed `inbound` + `dispatch=delivered`
   — the loop still reported "never reached the agent's ledger" for 240 s. The
   judgement now uses the durable evidence (ledger line for
   `[AIMail] Persona applied`), with the pending row as a fallback.
3. **`journey-pi.sh` called bare `timeout`** (`a590e137`). macOS runners have no
   GNU `timeout`, so no pi turn ever ran and the cell blamed the agent for not
   installing. Both journeys now use a portable `run_timeout`.
4. **dsh cleanliness was measured after the agent's first turn**
   (`f355d052`). The activation prompt legitimately tells the agent to install,
   so a package appearing during turn 1 is the agent's own bootstrap (the pass
   criterion), not contamination. The pin moved to *before* the prompt.
5. **The transcript reader could not decompress on windows** (`18cf1a6c`,
   `a03dadb0`). The pip `--user` route left no `zstandard`, and a native
   node/python cannot open a git-bash `/c/Users/...` path, so every route came
   back empty and the cell aborted on ENV GAP. The reader now tries
   `zstd`/`zstdcat`, then node `zlib.zstdDecompressSync` (the host is a node
   host), then python, and translates the path first. The integrity tripwire
   reuses the same reader, so it is active on windows too.
