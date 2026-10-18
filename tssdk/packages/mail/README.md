# @aimail/mail

Platform-neutral AIMail config resolution for TypeScript: session id / email /
recipient → `agentmail.json` → `AgentConfig`. Pure functions, no framework
imports, no host wiring.

[![npm](https://img.shields.io/npm/v/@aimail/mail)](https://www.npmjs.com/package/@aimail/mail)

## Install

```bash
pnpm add @aimail/mail
```

## What it does

Three lookups over the per-address `agentmail.json` bindings under
`$AIMAIL_HOME` (default `~/.aimail`):

- `resolveBySessionId(sessionId)` — session-file match first, then `agent_id`
  field match (dsh session uuids, OpenClaw/pi agent ids).
- `resolveByEmail(email)` — exact registered-address match.
- `resolveByRecipient(recipient)` — exact match, then persona-strip fallback
  (recipient local part ends with `.<registered local part>`), mirroring the
  Python `route_agent_for_email` semantics for role aliases.

`ResolveOptions.systemId` narrows the scan to one system; otherwise
`AIMAIL_SYSTEM_ID` is used, and failing that all systems are scanned
(`systems/{system_id}/{address_dir}/agentmail.json`).

Unbound resolutions throw (`no aimail binding for …`) instead of guessing an
identity.

## Related

- [@aimail/mail-core](../mail-core/README.md) — core, tools, inbound chain.
- [aimail monorepo](https://github.com/metercai/aimail) — CLI, Python SDK, adapters.
