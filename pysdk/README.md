# AIMail SDK for Python

**AIMail = AI + Mail**: the **native** email system for the AI age, built for human-Agent-Agent collaboration.

**AIMail** is a globe-reaching, tightly-controllable, collaboration-ready email system for AI agents. It lets an Agent communicate, interact, and collaborate with the outside world over email the way a person does.

AIMail comprises three components: **aimail-gateway**, **aimail SDK**, and **aimail CLI**. The aimail Python SDK provides the gateway client, 15 mail/contact/note/board/identity tools, the inbound preprocessing chain, and an MCP server. The aimail CLI provides Agent-side SDK installation, link diagnostics, and other routine maintenance.

## How hosts use it

Two entry points, both called by the aimail CLI:

- **Install / uninstall** — `python -m aimail.install install|uninstall --type
  hermes|deerflow --home <platform-root> [--system-id SID]` (the CLI calls the
  same functions through the python door).
- **Ops door** — `python -m aimail.sdk_ops --op <assemble|update|teardown|
  prompt-test|…> --args '<json>'`, one JSON envelope in and out. Register,
  teardown and prompt work all go through this door.

Host adapters never import the package path directly: each entry calls
`_aimail_bootstrap()`, which resolves the core directory in every layout
(site-packages, bundler copy, repo checkout).

## Related

- [aimail](https://github.com/metercai/aimail) — CLI (`cli/`), TypeScript SDK (`tssdk/`), bridge.
- [aimail-gateway](https://github.com/metercai/aimail-gateway) — the mail service this SDK talks to.

Building an adapter for a new platform: see
[docs/agent-adapter-playbook.md](https://github.com/metercai/aimail/blob/main/docs/agent-adapter-playbook.md).
