# pi-aimail

AIMail extension for pi (earendil-works/pi-coding-agent). It gives a pi agent
a mailbox on AIMail: inbound email is delivered into the running session, and
the agent can send mail, manage contacts, keep thread notes, and work on A2A
boards through 12 plain tools.

## Install

Prerequisites:

- pi (>= 0.84, `npm install -g @earendil-works/pi-coding-agent`)
- an AIMail binding for the pi agent (pointer file `~/.pi/.agentmail` with
  `{system_id, email}`, plus the registered address on the gateway)

```bash
pi install npm:pi-aimail
```

## What it does

**Tools** — the same 12 bare-name tools as every other adapter:
`send_mail`, `manage_contacts`, `contact_profile`, `set_contact_profile`,
`email_summary`, `set_email_summary`, `board_status`, `board_task_list`,
`board_task_show`, `board_heartbeat`, `board_members`, `set_public_whoami`,
registered via `pi.registerTool` with TypeBox parameters. Identity comes from
the `~/.pi/.agentmail` pointer plus the bound `agentmail.json`; unbound
agents fail loud.

**Inbound mail** — pi has no HTTP route registration, so the extension owns a
local listener (`POST /aimail/inbound` on `127.0.0.1:9101`, override with
`inboundPort`). Each bridge delivery is HMAC verified against the per-agent
secret, routed by recipient (exact address, then persona-alias fallback),
enriched by the shared preprocess chain (13 steps + ping/pong intercept), and
injected into the running session via `pi.sendUserMessage` (steer — always
triggers a turn). The listener closes on `session_shutdown`.

**Identity header** — outbound mail carries
`X-AIMail-Agent: pi/<detected host version>+<primary model>` (detected from
the installed pi package; never guessed).

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — the AIMail agent
  runtime (Python): CLI, gateway config, bridge provisioning, and the
  binding & registration flow this plugin integrates with.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service, address & activation APIs, and the
  board endpoints the tools talk to.

## Development

```bash
pnpm exec vitest run packages/pi-aimail
pnpm exec tsc -p packages/pi-aimail/tsconfig.json --noEmit   # typecheck
```
