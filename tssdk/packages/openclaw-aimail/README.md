# openclaw-aimail

AIMail plugin for OpenClaw: a real mailbox for an OpenClaw agent — inbound mail
lands in the agent's main session, 15 plain tools cover send, contacts, thread
notes, search and A2A boards, and `openclaw aimail` handles registration.

## Install

```bash
openclaw plugins install openclaw-aimail --force --accept-capabilities
openclaw aimail register --email agent@your.domain    # or: register-all
```

Prerequisites: OpenClaw (>= 2026.7.1), and an AIMail binding for the agent
(pointer `~/.openclaw/.agentmail` plus the registered address). For offline or
local tarball installs use `npm-pack:<tgz> --force`.

Uninstall: `openclaw plugins uninstall openclaw-aimail`.

## What it does

- **Tools** — the 15 tools defined once in `@aimail/mail-core`'s `MAIL_TOOLS`.
  Identity comes from the pointer plus the bound `agentmail.json`; unbound agents
  fail loud.
- **Inbound** — in-gateway route `/aimail/inbound` (no extra port): HMAC-verified,
  run through the shared preprocess chain, delivered to the main session.
- **Commands** — `register | register-all | deregister | status`: idempotent
  chains against gateway admin APIs; `register-all` enumerates
  `~/.openclaw/agents/*` and binds `{agent}@{domain}` for each (the pointer stays
  with the main agent).
- **Inbound op entry** — `--op assemble|update|teardown|prompt-test` for the
  aimail CLI, single-line JSON envelope in and out.
- **Identity header** — `X-AIMail-Agent: openclaw/<detected version>+<model>`,
  read from the installed package and the runtime's active model.

## Other adapters

- [dsh-aimail](https://www.npmjs.com/package/dsh-aimail) — dsh plugin.
- [pi-aimail](https://www.npmjs.com/package/pi-aimail) — pi extension.

## Related

- [aimail monorepo](https://github.com/metercai/aimail) — CLI, Python SDK, this SDK.
- [aimail-gateway](https://github.com/metercai/aimail-gateway) — the mail service behind the tools.
