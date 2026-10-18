# @aimail/mail-core

Framework-agnostic AIMail core for TypeScript: gateway client, tools, the full
inbound preprocess chain, and `agentmail.json` read/write. Import it directly
from any TS agent runtime (dsh, OpenClaw, pi, or your own).

[![npm](https://img.shields.io/npm/v/@aimail/mail-core)](https://www.npmjs.com/package/@aimail/mail-core)

## Install

```bash
pnpm add @aimail/mail-core
```

Point `AIMAIL_HOME` at the aimail home (default `~/.aimail`), where per-address
`agentmail.json` bindings live.

## What it does

- `GatewayClient` — authenticated gateway HTTP client (send / upload / download /
  whitelist / contacts / agent state / threads / boards); outbound requests carry
  the `X-AIMail-Agent` header and the v1 HMAC signature.
- Tools — send, contacts, contact profile, email summary, search, and the board
  API, exposed through `MAIL_TOOLS`: names, descriptions, TypeBox parameter
  shapes and handlers in one array.
- Inbound — `processInboundMail` runs the shared preprocess chain (recipient and
  sender enrichment, persona normalization, DM/mention detection, attachment
  download, inbound logging) and intercepts ping/pong probes;
  `verifySignature` checks webhook HMACs.
- Config — `loadConfigByEmail` / `loadConfigByAgentId` / `loadConfigBySessionId`,
  `saveAgentConfig`, `updateAgentConfig`. Per-address `agentmail.json` is the
  only identity source.
- Identity — `setAgentIdentity` / `setAgentModel` set `X-AIMail-Agent`
  (`{platform}/{version}+{model}`).

## Usage

```ts
import { sendMail, processInboundMail, verifySignature, MAIL_TOOLS } from '@aimail/mail-core'

const result = await sendMail({ systemId, email }, { to, subject, body })

if (verifySignature(rawBody, sig, webhookSecret)) {
  const enriched = await processInboundMail(payload, headers, { systemId, email })
  // null = ping/pong already answered in place; otherwise hand it to the agent
}

for (const tool of MAIL_TOOLS) { /* bind name / description / params / handler */ }
```

## Related

- [@aimail/mail](../mail/README.md) — config resolution helpers.
- [aimail monorepo](https://github.com/metercai/aimail) — CLI, Python SDK, adapters.
