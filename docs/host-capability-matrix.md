# Host capability matrix (DSH / Pi / OpenClaw)

Status: Phase-0 working note for the SSE unification plan
([`tssdk-sse-unification-plan.md`](tssdk-sse-unification-plan.md)).
Only capabilities verified from installed type definitions are marked “known”.

| Capability | DSH | Pi | OpenClaw |
|---|---|---|---|
| Plugin lifecycle | Cordis entries; background services OK (known) | Extension `apply(api)`; event handlers (known) | Plugin entry (known); background consumer TBD |
| Workspace | `workspaceRegistry` + `default-workspace` (known) | `ExtensionContext.cwd`; workspace is the current project directory; registry/list TBD | Per-agent workspace/agentDir (known from existing adapter); resolver TBD |
| Session handle | `ctx.agents.create/get/resume`, `agent.followup` (known) | `ExtensionCommandContext.newSession/switchSession`, `sendUserMessage` (known; command vs background context TBD) | Agent/session API TBD |
| Wake a turn | `agent.followup` (known) | `sendUserMessage` / `sendMessage(triggerTurn)` (known) | TBD |
| Tool registration | `ctx.tools.register` (known) | `api.registerTool` (known) | Plugin tool API (known from existing adapter) |
| Pointer | pointer file in DSH home (known) | `~/.pi` pointer (known) | openclaw pointer (known) |
| Current session cwd | session header cwd (known) | `ctx.cwd` (known) | per-agent workspace (known) |
| Stream credential scope | system/domain key, address fallback (known) | per-address key today; system scope TBD | domain/system + per-agent TBD |

## Phase-0 questions to close before code

1. **Pi background context**: can an extension hold a long-lived SSE consumer
   outside a command handler, and call `sendUserMessage` from it?
2. **Pi workspace catalog**: is `cwd` the only workspace identity, or is there a
   stable workspace registry/id usable across sessions?
3. **Pi session creation from background**: `newSession` is exposed on
   `ExtensionCommandContext`; confirm the equivalent safe API for a service.
4. **OpenClaw gateway-to-agent**: how one domain/system key covers multiple
   isolated agents; how to resolve agentId → workspace/agentDir without
   duplicating openclaw internals.
5. **OpenClaw background lifecycle**: plugin entry lifetime vs gateway process;
   SSE reconnect/ownership.

Until these are verified, Pi/OpenClaw adapters MUST NOT be claimed conformant.
