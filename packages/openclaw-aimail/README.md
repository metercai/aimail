# openclaw-aimail

AIMail plugin for OpenClaw. It gives an OpenClaw agent a mailbox on AIMail:
inbound email is delivered into the agent's main session, and the agent can
send mail, manage contacts, keep thread notes, and work on A2A boards through
12 plain tools.

## Install

Prerequisites:

- OpenClaw (>= 2026.7.1)
- an AIMail binding for the OpenClaw agent (pointer file
  `~/.openclaw/.agentmail` with `{system_id, email}`, plus the registered
  address on the gateway)

```bash
openclaw plugins install openclaw-aimail
```

Then register an address for the agent:

```bash
openclaw aimail register --email agent@your.domain
```

## What it does

**Tools** — the same 12 bare-name tools as every other adapter: `send_mail`,
`manage_contacts`, `contact_profile`, `set_contact_profile`, `email_summary`,
`set_email_summary`, `board_status`, `board_task_list`, `board_task_show`,
`board_heartbeat`, `board_members`, `set_public_whoami`. Identity comes from
the `~/.openclaw/.agentmail` pointer plus the bound `agentmail.json`; unbound
agents fail loud.

**Inbound mail** — an in-gateway HTTP route (`/aimail/inbound`, no new
port). Each bridge delivery is HMAC verified against the per-agent secret,
routed by recipient (exact address, then persona-alias fallback), enriched by
the shared preprocess chain (13 steps + ping/pong intercept), and delivered
to the agent's main session via the gateway's internal hooks endpoint. Health
probes (`__agentmail_ping__:` / `__amail_pong__:` subjects) are answered
automatically and never trigger an agent run.

**Commands** — `openclaw aimail register|deregister|status`:
idempotent 4-step registration chain and 3-step deregistration chain against
existing gateway admin APIs, plus a status report.

**Identity header** — outbound mail carries
`X-AIMail-Agent: openclaw/<detected host version>+<primary model>` (detected
from the installed OpenClaw package and the runtime's active model; never
guessed).

## Uninstall

```bash
openclaw plugins uninstall openclaw-aimail
```

## Development

```bash
pnpm exec vitest run packages/openclaw-aimail
pnpm exec tsc -p packages/openclaw-aimail/tsconfig.json --noEmit   # typecheck
```
