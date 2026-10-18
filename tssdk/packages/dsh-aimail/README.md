# dsh-aimail

AIMail plugin for dsh (deepseek-harness): a real mailbox for a dsh agent —
inbound mail lands in the agent's session, and 15 plain tools cover send,
contacts, thread notes, search and A2A boards.

## Install

```bash
dsh plugin --profile web add dsh-aimail --config.minimumReleaseAge=0   # idempotent
dsh plugin --profile web remove dsh-aimail                            # idempotent
```

Prerequisites: dsh with the `web` profile, and an AIMail binding for the
session (produced by `aimail install --home ~/.dsh`).

## What it does

- **Tools** — the 15 tools defined once in `@aimail/mail-core`'s `MAIL_TOOLS`.
  Identity resolves from the session id via `@aimail/mail`; `agentmail.json` is
  the only identity source, and unbound sessions fail loud.
- **Inbound** — profile-scoped `POST /aimail/inbound` (default port 9099,
  `AIMAIL_INBOUND_PORT` to override): HMAC-verified, run through the shared
  preprocess chain, delivered into the dsh session.
- **Inbound op entry** — `--op assemble|update|teardown|prompt-test` for the
  aimail CLI, single-line JSON envelope in and out.
- **Host layout** — the plugin reports dsh's own layout (mail preset, skill
  exposure) to the shared assembler, so the CLI carries no dsh-specific concept.
- **Persona** — mounts an email-agent persona that teaches the agent the mail
  workflow (reply-all semantics, tool choice, thread continuity).

## Other adapters

- [openclaw-aimail](https://www.npmjs.com/package/openclaw-aimail) — OpenClaw plugin.
- [pi-aimail](https://www.npmjs.com/package/pi-aimail) — pi extension.

## Related

- [aimail monorepo](https://github.com/metercai/aimail) — CLI, Python SDK, this SDK.
- [aimail-gateway](https://github.com/metercai/aimail-gateway) — the mail service behind the tools.
