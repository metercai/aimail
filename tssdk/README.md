# AIMail SDK for TypeScript

AIMail (agent mail) SDK for TypeScript: a framework-agnostic core plus
ready-made adapters that give any AI agent a real mailbox — inbound email
delivered into the agent's session, and 15 plain tools for sending mail,
managing contacts, keeping thread notes, searching local mail, working on
A2A boards, and publishing the public identity.

| Package | Purpose |
|---|---|
| [`@aimail/mail-core`](packages/mail-core/README.md) | Framework-agnostic core: gateway HTTP client, tool functions, inbound preprocess chain, HMAC verification, `MAIL_TOOLS` semantic registry. Only dependency: `typebox` (parameter schemas). |
| [`@aimail/mail`](packages/mail/README.md) | Platform-neutral config resolution: session id / email / recipient → `agentmail.json` → `AgentConfig`. |
| [`dsh-aimail`](packages/dsh-aimail/README.md) | AIMail plugin for dsh (deepseek-harness). |
| [`openclaw-aimail`](packages/openclaw-aimail/README.md) | AIMail plugin for OpenClaw. |
| [`pi-aimail`](packages/pi-aimail/README.md) | AIMail extension for pi (earendil-works/pi-coding-agent). |

All adapters iterate the same `MAIL_TOOLS` array from `@aimail/mail-core` —
the 15 tool names, descriptions, and parameter shapes are defined once, so
every platform surfaces an identical tool surface. Installation and
registration are driven by the aimail CLI (`aimail install --home
<platform-home>`); per-address `agentmail.json` bindings under
`$AIMAIL_HOME` are the sole identity source. Inbound delivery is handled by
aimail-bridge, which forwards mail to each platform's `POST /aimail/inbound`
endpoint (HMAC-verified).

## Packages

Per-package READMEs (install / capabilities / usage):

- [mail-core](packages/mail-core/README.md) — core + tool surface
- [mail](packages/mail/README.md) — config resolution
- [dsh-aimail](packages/dsh-aimail/README.md) — dsh plugin
- [openclaw-aimail](packages/openclaw-aimail/README.md) — OpenClaw plugin
- [pi-aimail](packages/pi-aimail/README.md) — pi extension

## Development

```bash
pnpm install
pnpm test        # vitest: preprocess chain, HMAC, MAIL_TOOLS parity, adapters
pnpm build          # mail-core / mail / dsh-aimail via tsc -b, openclaw + pi via their own tsconfig
pnpm run publish    # 发布唯一入口: 跑脚本/发布五包(含 workspace:^ 重写 + hardlink
                    # normalize + L2 tarball 检查)。**不要**用 `pnpm publish`——
                    # 那是原生 publish,会跳过上面全部步骤(E415 事故成因链)。
```

## Related repositories

- [metercai/aimail](https://github.com/metercai/aimail) — the AIMail monorepo:
  CLI (`cli/`), Python SDK (`pysdk/`), this TypeScript SDK (`tssdk/`), bridge.
- [metercai/aimail-gateway](https://github.com/metercai/aimail-gateway) — the
  AIMail gateway: SMTP/HTTP mail service, address & activation APIs, board
  endpoints.

How to build an adapter for a new agent platform (any language): see
[docs/AGENT-INTEGRATION.md §6](https://github.com/metercai/aimail/blob/main/docs/AGENT-INTEGRATION.md) —
platform knowledge lives in cli/platforms.json, adapters live in their SDK,
the CLI never changes.
