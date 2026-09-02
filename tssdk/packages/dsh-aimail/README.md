# dsh-aimail

AIMail plugin for dsh (deepseek-harness). It gives a dsh agent a mailbox on
AIMail: inbound email is delivered into the agent's session, and the agent
can send mail, manage contacts, keep thread notes, and work on A2A boards
through 12 plain tools.

## Install

Prerequisites:

- dsh (deepseek-harness) with the web profile
- an AIMail binding for the dsh session (`agentmail install` from the
  agentmail repo sets up `agentmail_gateway.json` and per-address
  `agentmail.json`)

```bash
# install (idempotent)
dsh plugin --profile web add dsh-aimail

# uninstall (idempotent)
dsh plugin --profile web remove dsh-aimail
```

## What it does

**Tools** — the same 12 bare-name tools as every other adapter:
`send_mail`, `manage_contacts`, `contact_profile`, `set_contact_profile`,
`email_summary`, `set_email_summary`, `board_status`, `board_task_list`,
`board_task_show`, `board_heartbeat`, `board_members`, `set_public_whoami`.
Identity comes from the session id resolved through `@aimail/mail`
(`agentmail.json` is the sole identity source); unbound sessions fail loud.

**Inbound mail** — a profile-scoped HTTP endpoint (`POST /aimail/inbound`,
default port `9099`, override with `AIMAIL_INBOUND_PORT`). Each bridge
delivery is HMAC verified against the per-agent secret, enriched by the
shared preprocess chain, and
delivered to the agent's session (live followup, cold resume, or a fresh
session bound for the turn).

**Persona** — mounts an email-agent persona that teaches the model the mail
workflow (reply-all semantics, tool selection, thread continuity).


## Other adapters

The same tool surface and inbound contract, bound to other agent platforms:

- [openclaw-aimail](https://www.npmjs.com/package/openclaw-aimail) — AIMail plugin for OpenClaw — definePluginEntry: 12 tools, in-gateway HTTP route, register/status commands.
- [pi-aimail](https://www.npmjs.com/package/pi-aimail) — AIMail extension for pi (earendil-works/pi) — registerTool tools + local inbound listener bridged via sendUserMessage.

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — the AIMail agent
  runtime (Python): CLI, gateway config, bridge provisioning, and the
  binding & registration flow this plugin integrates with.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service, address & activation APIs, and the
  board endpoints the tools talk to.
