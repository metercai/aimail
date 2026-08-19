# aimail-sdk-ts

AgentMail SDK for TypeScript: shared mail core plus platform integration
plugins for dsh (deepseek-harness) and OpenClaw.

| Package | Description |
|---|---|
| `@aimail/mail-core` | Framework-agnostic core: gateway client, 12 tool functions, inbound preprocess chain (13 steps + ping/pong), HMAC verification, MAIL_TOOLS semantic registry. Zero dependencies. |
| `@aimail/mail` | Platform-neutral config resolution (session id / email / recipient → `agentmail.json` → `AgentConfig`). |
| `dsh-aimail` | AgentMail plugin for dsh. |
| `openclaw-aimail` | AgentMail plugin for OpenClaw. |

Both adapters iterate the **same** `MAIL_TOOLS` array from `mail-core` — the 12
tool names, descriptions, and parameter text are defined exactly once.

## Packages

### dsh-aimail (dsh plugin)

```bash
# install (idempotent)
dsh plugin --profile web add dsh-aimail

# uninstall (idempotent)
dsh plugin --profile web remove dsh-aimail
```

Prerequisite: an AgentMail account with a binding for the dsh session (run
`agentmail install` from the agentmail repo).

What it mounts onto the profile: the mail host service, the inbound endpoint,
the 12 mail/board tools, and an email-agent persona.

### openclaw-aimail (OpenClaw plugin)

```bash
openclaw plugins install openclaw-aimail
```

Prerequisite: an AgentMail binding for the OpenClaw agent
(`openclaw aimail register` after the plugin is installed).

What it provides: the 12 mail/board tools (bare names), an in-gateway inbound
HTTP route (`/agentmail/deliver`, HMAC verified), and
`openclaw aimail register|deregister|status` commands.

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
pnpm exec tsc -b packages/mail-core packages/mail packages/agentmail packages/openclaw-aimail
```
