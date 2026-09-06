# @aimail/mail

Platform-neutral AIMail config resolution for TypeScript: session id / email
/ recipient → `agentmail.json` → `AgentConfig`. Pure functions — no framework
imports, no host wiring.

[![npm](https://img.shields.io/npm/v/@aimail/mail)](https://www.npmjs.com/package/@aimail/mail)

## Install

```bash
pnpm add @aimail/mail
```

## What it does

Every adapter needs the same three lookups, sourced from the per-address
`agentmail.json` bindings under `$AIMAIL_HOME` (default `~/.aimail`):

- `resolveBySessionId(sessionId)` — session-file match first, then agent_id
  field match. For dsh session uuids and OpenClaw/pi agent ids.
- `resolveByEmail(email)` — exact registered-address match. Throws when
  unbound.
- `resolveByRecipient(recipient)` — exact match first, then persona-strip
  fallback (recipient local part ends with `.<registered local part>`),
  mirroring the Python `route_agent_for_email` semantics. Inbound routing for
  single-in-multi-out platforms (one gateway hosts many agents; mail can
  arrive at role aliases).
- `resolveConfig()`-style composition is left to each adapter: the identity
  source (pointer file / factory ctx / env) is platform-specific by design.

## Scope narrowing

`ResolveOptions.systemId` narrows the scan to one system; when omitted the
`AIMAIL_SYSTEM_ID` env is used; when that is also empty, all systems under
`$AIMAIL_HOME/systems/` are scanned (two-level traversal:
`systems/{system_id}/{address_dir}/agentmail.json`).

Unbound resolutions throw loudly (`no aimail binding for …`) — callers
surface that to the model/user instead of guessing an identity.

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — the AIMail monorepo:
  CLI (`cli/`), Python SDK (`pysdk/`), TypeScript SDK (`tssdk/`, you are here),
  bridge distributions.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service and the address & activation APIs
  that create the bindings being resolved here.
