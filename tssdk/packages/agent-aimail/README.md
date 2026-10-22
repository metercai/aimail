# agent-aimail

AIMail's single TypeScript SDK package. One package serves all Node.js agent
hosts: the shared core (gateway client, 15 mail tools, inbound preprocess
chain, HMAC verification, auto-bind) plus a host adapter for each supported
platform.

## Install (host-native, no CLI required)

Each host installs the same package; the package identifies its host
environment on startup and branches:

```bash
# OpenClaw (gateway plugin)
openclaw plugins install agent-aimail --force --accept-capabilities

# dsh (deepseek-harness, self-mounting bundle)
dsh plugin --profile web add agent-aimail

# pi (earendil-works/pi-coding-agent, extension)
pi install npm:agent-aimail
```

`aimail install` (the CLI) drives the same commands and registers the
agent afterwards.

## Host identification

On startup the package resolves which host is loading it (host SDK
resolvability + config dir), asserts the entry being executed matches that
host, then runs the host's activation path (board resources → skill release
→ tool registration → inbound route → eager bind). Adding a new host type =
one registry entry + one `src/<host>/` module; the shared core is never
copied.

## Layout

- `src/core/` — host-agnostic core (identity, auto-bind, tools, inbound, board, skill)
- `src/mail/` — config resolution (`agentmail.json` → `AgentConfig`)
- `src/openclaw/` — OpenClaw plugin entry (`dist/openclaw/index.js`)
- `src/dsh/` — dsh self-mounting entries (`dist/dsh/{mail-service,tools,inbound}.js`)
- `src/pi/` — pi extension entry (`dist/pi/index.js`)
- `src/hosts/` — host registry + detection + activation assertion
- `resources/` — board role prompts + agent skill (single source, staged at pack)

## Exports

- `agent-aimail` — host identification entry (detect + registry)
- `agent-aimail/core` — shared core API
- `agent-aimail/mail` — config resolvers
- `agent-aimail/{openclaw,dsh,pi}` — host entries
