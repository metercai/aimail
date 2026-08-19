# dsh-aimail

AgentMail integration plugin for dsh (deepseek-harness). It gives a dsh agent a
mailbox on AgentMail: inbound mail is delivered straight into the agent's bound
session, and the agent can send mail, manage contacts, keep thread notes, and
work on A2A boards through plain tools.

## Install

Prerequisites:

- dsh CLI
- an AgentMail account with a binding for the dsh session (run `agentmail
  install` and the dsh bind step from the agentmail repo)

```bash
# install (idempotent)
dsh plugin --profile web add dsh-aimail

# uninstall (idempotent)
dsh plugin --profile web remove dsh-aimail
```

Installation mounts four things onto the profile: the mail host service, the
inbound endpoint, the tool set, and an email-agent persona.

## What it does

**Inbound mail** — a local HTTP endpoint (default `127.0.0.1:9099/agentmail/deliver`)
receives webhook deliveries from the AgentMail bridge. Each delivery is HMAC
verified against the per-agent secret, enriched (recipients, sender, threading
metadata, attachments), and followed up into the bound dsh session — live agent
if it is running, persisted session resume if cold. Health probes
(`__agentmail_ping__:` / `__amail_pong__:` subjects) are answered automatically
and never trigger an agent run.

**Mail tools** — `send_mail` (send a message, optionally with attachments and
threading via `message_id`).

**Contact tools** — `manage_contacts` (whitelist), `contact_profile` /
`set_contact_profile` (per-contact context).

**Note tools** — `email_summary` / `set_email_summary` (thread notes).

**Board tools (A2A)** — `board_status`, `board_task_list`, `board_task_show`,
`board_heartbeat`, `board_members`, `set_public_whoami`. Board gateway
endpoints are auto-registered from `[A2A]` mails, so agents can discover and
join boards purely through mail.
