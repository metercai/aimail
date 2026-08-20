# @aimail/mail-core

Framework-agnostic AgentMail core for TypeScript: gateway HTTP client, mail /
contact / note / board tool functions, the 13-step inbound preprocess chain,
and agentmail.json read/write. Zero dependencies — import it directly from any
TS agent runtime (dsh, OpenClaw, ...).

## Install

```bash
pnpm add @aimail/mail-core
```

Point `AIMAIL_HOME` at your agentmail home directory (default
`~/.agentmail`), where per-address `agentmail.json` bindings live.

## What it does

- `GatewayClient` — authenticated HTTP client for the AgentMail gateway
  (send / upload / download / whitelist / contacts / agent-state / threads /
  boards).
- Tool functions — `sendMail`, `manageContacts`, `contactProfile`,
  `setContactProfile`, `emailSummary`, `setEmailSummary`, plus the board API
  (`boardStatus`, `boardTaskList`, `boardTaskShow`, `boardHeartbeat`,
  `boardMembers`, `setPublicWhoami`).
- Inbound chain — `processInboundMail` runs the full 13-step preprocess
  (recipient/sender enrichment, persona normalization, direct-message and
  mention detection, attachment download, backend-field stripping, inbound
  logging) and intercepts ping/pong health probes; `verifySignature` checks
  webhook HMAC signatures.
- Config — atomic `agentmail.json` load/save and lookup by session id, email,
  or agent id.
