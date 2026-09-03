# AIMail SDK for TypeScript

AIMail (agent mail) SDK for TypeScript: a shared framework-agnostic core plus
ready-made platform adapters that give any AI agent a real mailbox — inbound
email delivered into the agent's session, and 12 plain tools for sending
mail, managing contacts, keeping thread notes, and working on A2A boards.

This SDK lives in the [metercai/aimail](https://github.com/metercai/aimail)
monorepo under `tssdk/` (CLI in `cli/`, Python SDK in `pysdk/`, bridge in
`bridge/` — one repo for the whole AIMail runtime). The npm packages are the
published surface of this tree.

| Package | npm | Purpose |
|---|---|---|
| `@aimail/mail-core` | [![npm](https://img.shields.io/npm/v/@aimail/mail-core)](https://www.npmjs.com/package/@aimail/mail-core) | Framework-agnostic core: gateway HTTP client, 12 tool functions, inbound preprocess chain, HMAC verification, `MAIL_TOOLS` semantic registry. Zero dependencies. |
| `@aimail/mail` | [![npm](https://img.shields.io/npm/v/@aimail/mail)](https://www.npmjs.com/package/@aimail/mail) | Platform-neutral config resolution: session id / email / recipient → `agentmail.json` → `AgentConfig`. |
| `dsh-aimail` | [![npm](https://img.shields.io/npm/v/dsh-aimail)](https://www.npmjs.com/package/dsh-aimail) | dsh (deepseek-harness) plugin. |
| `openclaw-aimail` | [![npm](https://img.shields.io/npm/v/openclaw-aimail)](https://www.npmjs.com/package/openclaw-aimail) | OpenClaw plugin. |
| `pi-aimail` | [![npm](https://img.shields.io/npm/v/pi-aimail)](https://www.npmjs.com/package/pi-aimail) | pi (earendil-works/pi-coding-agent) extension. |

All adapters iterate the **same** `MAIL_TOOLS` array from `@aimail/mail-core`
— the 12 tool names, descriptions, and parameter text are defined exactly
once, so every platform surfaces an identical tool surface.

## How it fits together

```
                    ┌─────────────────────────────┐
   agentmail.json   │      @aimail/mail-core      │
   (per-address     │  gateway client · 12 tools  │
    bindings,       │  inbound chain · HMAC       │
    sole identity   │  MAIL_TOOLS registry        │
    source)         └──────────────┬──────────────┘
                                   │
              ┌────────────────────┼────────────────────┐
              │ @aimail/mail       │                    │
              │ config resolution  │                    │
              ▼                    ▼                    ▼
        dsh-aimail           openclaw-aimail         pi-aimail
        (cordis plugin,      (definePluginEntry,     (registerTool +
         node:http inbound)   gateway HTTP route)    local HTTP listener)
```

Inbound delivery: the [aimail-bridge](#) (push/pull proxy for the AIMail
gateway) forwards mail to each platform's endpoint. All platforms use the
same path — `POST /aimail/inbound` (HMAC-verified) — only the port differs.

## Packages

### dsh-aimail (dsh plugin)

```bash
# install (idempotent)
dsh plugin --profile web add dsh-aimail

# uninstall (idempotent)
dsh plugin --profile web remove dsh-aimail
```

Prerequisite: an AIMail binding for the dsh session (run `aimail install`
from the aimail repository's `cli/`).

What it mounts onto the profile: the mail host service, the inbound endpoint,
the 12 mail/board tools, and an email-agent persona.

### openclaw-aimail (OpenClaw plugin)

```bash
openclaw plugins install openclaw-aimail
```

Prerequisite: an AIMail binding for the OpenClaw agent (pointer file
`~/.openclaw/.agentmail` with `{system_id, email}`).

What it provides: the 12 mail/board tools (bare names), an in-gateway inbound
HTTP route (`/aimail/inbound`, HMAC verified), and
`openclaw aimail register|deregister|status` commands.

### pi-aimail (pi extension)

```bash
pi install npm:pi-aimail
```

Prerequisite: an AIMail binding for the pi agent (pointer file
`~/.pi/.agentmail` with `{system_id, email}`).

What it provides: the 12 mail/board tools via `pi.registerTool`, and a local
inbound listener (`POST /aimail/inbound` on `127.0.0.1:9101`, HMAC verified)
that bridges into the running session via `sendUserMessage`.

## What the tools do

- **Mail** — `send_mail` (send, optionally with attachments and threading via
  `message_id`).
- **Contacts** — `manage_contacts` (whitelist), `contact_profile` /
  `set_contact_profile` (per-contact context).
- **Notes** — `email_summary` / `set_email_summary` (thread notes).
- **Boards (A2A)** — `board_status`, `board_task_list`, `board_task_show`,
  `board_heartbeat`, `board_members`, `set_public_whoami`. Board gateway
  endpoints auto-register from `[A2A]` mails, so agents discover and join
  boards purely through mail.

## Development

```bash
pnpm install
pnpm test        # vitest: preprocess chain, HMAC, MAIL_TOOLS parity, adapters
pnpm exec tsc -b packages/mail-core packages/mail packages/dsh-aimail packages/openclaw-aimail packages/pi-aimail
```

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — the AIMail agent
  runtime (Python): CLI, gateway config, bridge provisioning, and the
  `agentmail.json` binding model this SDK consumes.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service, address & activation APIs, and the
  board endpoints the SDK client talks to.

See [docs/platform-adapter-guide.md](docs/platform-adapter-guide.md) for how
to build an adapter for a new agent platform.
