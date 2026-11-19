# Host capability matrix (DSH / Pi / OpenClaw)

Status: Phase-0 working note for the SSE unification plan
([`tssdk-sse-unification-plan.md`](tssdk-sse-unification-plan.md)).
Only capabilities verified from installed type definitions are marked “known”.

| Capability | DSH | Pi | OpenClaw |
|---|---|---|---|
| Plugin lifecycle | Cordis entries; background services OK (known) | Extension `apply(api)`; SSE pull default, listener legacy (known) | Plugin entry; SSE default, listener legacy (known); shared consumer TBD |
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

## Profile boundaries (2026-11-18)

- **Pi**: single-address only; `aimail_system` is intentionally not exposed.
- **OpenClaw**: workspace/agentId routing always; thread session continuity only
  when `hooks.allowRequestSessionKey=true` and the prefix matches.

## Latest checks (2026-11-18)

- Pi `@earendil-works/pi-coding-agent@1.5.2` (latest): `ExtensionAPI` still has
  no `newSession`; it remains only on `ExtensionCommandContext`, created for
  command handlers. Background extension code cannot create a session.
- OpenClaw `2026.11.5`: `/hooks/agent` can accept a caller `sessionKey` only when
  `hooks.allowRequestSessionKey=true` (optionally constrained by
  `hooks.allowedSessionKeyPrefixes`). The adapter now sends a thread-stable
  `agent:<agentId>:aimail:<hash>` key only when that flag is enabled; otherwise
  it omits the key and the host creates a fresh hook session.

### OpenClaw sessionKey configuration

To make a reply return to the originating thread's session, enable the host
policy and constrain the prefix:

```json
{
  "hooks": {
    "allowRequestSessionKey": true,
    "allowedSessionKeyPrefixes": ["agent:main:aimail:"]
  }
}
```

The adapter sends `agent:<agentId>:aimail:<thread-hash>` only when the flag is
true and the key matches an allowed prefix. Otherwise it omits `sessionKey` and
OpenClaw creates a fresh hook session (no contract break; no session targeting).


## Contract version

Current target: [`aimail-host-sse-contract-v2.md`](aimail-host-sse-contract-v2.md)
(AgentSystemId aggregation, B1/B2, single vs multi-address).

## Toolset naming

The host-facing toolset is one logical name: `agentmail` <!-- contract-allowed: toolset name -->
(`contract/aimail-contract.json: agent_toolset_name`). The SKILL frontmatter
depends on `toolset: agentmail`. <!-- contract-allowed: toolset name --> `MAIL_TOOLS` / `MAIL_BOARD` / `MAIL_ADMIN` are
internal capability groups, not host toolset names.
