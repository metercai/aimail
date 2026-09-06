# @aimail/mail-core

Framework-agnostic AIMail core for TypeScript: gateway HTTP client, mail /
contact / note / board tool functions, the 13-step inbound preprocess chain,
and `agentmail.json` read/write. Zero dependencies — import it directly from
any TS agent runtime (dsh, OpenClaw, pi, or your own).

[![npm](https://img.shields.io/npm/v/@aimail/mail-core)](https://www.npmjs.com/package/@aimail/mail-core)

## Install

```bash
pnpm add @aimail/mail-core
```

Point `AIMAIL_HOME` at your aimail home directory (default
`~/.aimail`), where per-address `agentmail.json` bindings live.

## What it does

- `GatewayClient` — authenticated HTTP client for the AIMail gateway
  (send / upload / download / whitelist / contacts / agent-state / threads /
  boards). Outbound requests carry the `X-AIMail-Agent` identity header and
  are signed with the v1 HMAC scheme.
- Tool functions — `sendMail`, `manageContacts`, `contactProfile`,
  `setContactProfile`, `emailSummary`, `setEmailSummary`, `searchMail`, plus the board API
  (`boardStatus`, `boardTaskList`, `boardTaskShow`, `boardHeartbeat`,
  `boardMembers`, `setPublicWhoami`).
- Inbound chain — `processInboundMail` runs the full 13-step preprocess
  (recipient/sender enrichment, persona normalization, direct-message and
  mention detection, attachment download, backend-field stripping, inbound
  logging) and intercepts ping/pong health probes; `verifySignature` checks
  webhook HMAC signatures.
- `MAIL_TOOLS` — the semantic registry of all 13 tools (names, descriptions,
  TypeBox parameter shapes, handlers). Adapters iterate this single array so
  every platform surfaces an identical tool surface.
- Config loaders — `loadConfigByEmail`, `loadConfigByAgentId`,
  `loadConfigBySessionId`, `saveAgentConfig`, `updateAgentConfig` (per-address
  `agentmail.json` is the sole identity source).
- Identity — `setAgentIdentity` / `setAgentModel` set the outbound
  `X-AIMail-Agent` header (`{platform}/{version}+{model}`).

## Usage sketch

```ts
import { sendMail, processInboundMail, verifySignature, MAIL_TOOLS } from '@aimail/mail-core'

// send (tool functions take a ToolCtx resolved from agentmail.json)
const result = await sendMail({ systemId, email }, { to, subject, body })

// inbound (HMAC-verified payload from the aimail-bridge)
if (verifySignature(rawBody, sig, webhookSecret)) {
  const enriched = await processInboundMail(payload, headers, { systemId, email })
  if (enriched === null) {
    // ping/pong intercepted — already answered
  } else {
    // deliver enriched JSON into the agent session
  }
}

// adapter registration: iterate the semantic registry
for (const tool of MAIL_TOOLS) { /* bind name/description/params/handler */ }
```

## API notes

- `processInboundMail` returns `null` when the mail was a ping/pong probe
  (the chain answers it in-place); otherwise it returns the enriched payload
  to hand to the agent.
- `verifySignature` compares a timing-safe HMAC-SHA256 of the raw body
  against the `X-Webhook-Signature` header, keyed by the per-address
  `webhook_secret`.
- The 13-step chain is the **inbound contract** (not an optional
  preprocessing layer): every platform adapter must call it before handing
  mail to the agent.

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — the AIMail monorepo:
  CLI (`cli/`), Python SDK (`pysdk/`), TypeScript SDK (`tssdk/`), bridge
  distributions.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service, address & activation APIs, and the
  board endpoints the SDK client talks to.
