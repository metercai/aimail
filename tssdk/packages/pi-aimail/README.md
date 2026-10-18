# pi-aimail

AIMail extension for pi (earendil-works/pi-coding-agent): a real mailbox for a pi
agent — inbound mail is injected into the running session, and 15 plain tools
cover send, contacts, thread notes, search and A2A boards.

## Install

```bash
pi install npm:pi-aimail
```

Prerequisites: pi (>= 0.84, `npm install -g @earendil-works/pi-coding-agent`) and
an AIMail binding for the agent (pointer `~/.pi/.agentmail` plus the registered
address, produced by `aimail install --home ~/.pi`).

## What it does

- **Tools** — the 15 tools defined once in `@aimail/mail-core`'s `MAIL_TOOLS`,
  registered with `pi.registerTool`. Identity comes from the pointer plus the
  bound `agentmail.json`; unbound agents fail loud.
- **Inbound** — pi has no host HTTP route, so the extension owns a local listener
  (`127.0.0.1:9101`, option `inboundPort`): HMAC-verified, run through the shared
  preprocess chain, injected into the running session via `pi.sendUserMessage`
  (steer). The listener closes on `session_shutdown`.
- **Inbound op entry** — `--op assemble|update|teardown|prompt-test` for the
  aimail CLI, single-line JSON envelope in and out.
- **Identity header** — `X-AIMail-Agent: pi/<detected version>+<model>`, read from
  the installed package.

## Other adapters

- [dsh-aimail](https://www.npmjs.com/package/dsh-aimail) — dsh plugin.
- [openclaw-aimail](https://www.npmjs.com/package/openclaw-aimail) — OpenClaw plugin.

## Related

- [aimail monorepo](https://github.com/metercai/aimail) — CLI, Python SDK, this SDK.
- [aimail-gateway](https://github.com/metercai/aimail-gateway) — the mail service behind the tools.
