# AIMail SSE contract — implementation self-audit (2026-11-18)

Sources audited: `docs/aimail-host-sse-contract.md`, `docs/host-capability-matrix.md`,
live code and the local-fixture L2 (`tests/host-acceptance/run-l2-local-gateway.sh`).

## Status matrix

| Contract area | DSH | Pi | OpenClaw |
|---|---|---|---|
| SSE only, no listener | PASS | PASS (single-address) | PASS |
| No CLI in activation | PASS | PASS (address-only) | PASS |
| `aimail_system` | PASS | **disabled by design (Appendix A)** | PASS |
| `aimail_address` | PASS | PASS | PASS |
| Binding workspace required | PASS | PASS | PASS |
| No `session_id` in binding | PASS active hub | PASS | PASS |
| Shared gateway/scope stream | PASS (planner) | address-only (one) | PASS (system key) |
| Thread/session routing | PASS (`routeThread`) | **BLOCKED: no host API** | PARTIAL (`sessionKey` config) |
| Ack only after delivery | PASS (workspace miss no ack) | N/A host-session | PASS |
| Welcome/manager SSE | PASS L2 | PASS L2 | PASS L2 |
| Single-address enforcement | N/A | PASS (`findOtherActiveAddress`) | N/A |

## Findings

### F1 — Pi cannot route to a workspace/session (host API blocked)

Evidence: latest Pi `1.5.2` exports `newSession` only on
`ExtensionCommandContext`; `ExtensionAPI` has no background create/deliver
session API. Pi's historical inbound path used `sendUserMessage` on the current
session. Appendix A therefore defines Pi as single-address and disables
`aimail_system`; this is implemented (`buildPiTools` 15 tools, address-only L2).

Status: deliberate degradation, not a hidden implementation.

### F2 — Pi pointer was not written by generic address activation

The generic address code writes a pointer only with `profileHome` or a profile
resolver. Pi/OpenClaw did not install the resolver, so an activated address
could persist a binding but not be discoverable after restart. Fixed by calling
`setProfileDirResolver()` in both host entries; L2 now reports
`pointer_written: true`.

### F3 — OpenClaw session continuity is conditional

Without `hooks.allowRequestSessionKey=true`, `/hooks/agent` creates a fresh
hook session for every delivery; workspace/agentId routing works, thread
continuity does not. With the flag and allowed prefix, the adapter sends
`agent:<agentId>:aimail:<thread-hash>`. This is Appendix B, not a contract
change. Default host config does not enable it.

### F4 — Pi/OpenClaw do not call `runtime/session-router.routeThread`

DSH hub does. Pi/OpenClaw use host delivery primitives and (for OpenClaw)
`threadRoot` hashing, but not the shared `routeThread`. This is a remaining
implementation deviation from the literal §8 wording, caused by the missing
Pi session API and the OpenClaw hook API shape.

### F5 — Legacy DSH `inbound.ts` still contains session_id routing

It is not mounted by `cordis.patch.yml` (three entries: mail/tool-mail/mail-hub),
but the source file still uses `cfg.session_id` for the old inbound path. Any
static audit that scans all files will flag it. It should be moved to a legacy
package/entry, not used by the SSE profile.

### F6 — DSH ack timing

The hub acks after `agent.followup()` is accepted into a session; it does not
wait for the LLM turn to complete. `workspaceFor` no longer invents a workspace
and missing workspace returns false (no ack), which fixes G7's ack-on-miss
behavior. A full turn-completion-before-ack assertion is not provided.

### F7a — G8 activation-path boundary

The G8 fixture wrote a binding from outside the host. That is not a supported
activation path (contract §5.2a). Read-on-boot only recovers an already
legitimately activated binding. G8 should be re-tested by driving an in-host
tool call, not by pre-writing `agentmail.json`.

### F7b — read-on-boot arming

DSH now logs `SSE reconcile: … stream(s)` and re-runs reconcile 2s after startup.
This covers bindings written by an out-of-process activator before restart.
Re-test G8 with a real DSH restart before declaring it fixed.

### F7 — real-host pull did not produce an agent turn (QA 2026-11-18)

Latest QA handoff (`07a8b2c`) reports that with the same gateway/system key:
Pi produced a turn, but DSH and OpenClaw did not. DSH hub showed
`connected:true`, `streams:["system:..."]`, `lastEventAt:""`; OpenClaw hooks
were enabled but no turn. The same key works through `startSystemPull()`
directly. This is a real-host delivery/agent-turn gap beyond the fake-ctx local
L2, and is still open.

### F8 — `ensureDefaultAgentBinding` still auto-binds when explicitly called

Startup no longer calls it, and SSE startup does not auto-bind, but the exported
function still performs system/binding creation if an external caller invokes
it. This should be deprecated/renamed for the new profile.

## Verification actually run

```text
L0 PASS
Pi single-address local-fixture L2 PASS
OpenClaw local-fixture L2 PASS (before the latest pointer/sessionKey edits)
DSH local-fixture L2 PASS (before the latest edits)
Pi/OpenClaw host tests 46/46
OpenClaw tests 43/43
```

## Verdict

Not “zero deviation”. The transport, no-CLI startup, workspace binding, Pi
single-address profile, shared system stream, and local-fixture L2 are
implemented and tested. The remaining literal deviations are F4 (Pi/OpenClaw
routeThread), F5 (legacy DSH inbound source), F6 (turn-completion ack), F7
(real-host DSH/OpenClaw no turn in pull mode), and F8 (auto-bind API still
exported). F1/F3 are explicit host capability exceptions in
the contract appendices, not silent claims.
