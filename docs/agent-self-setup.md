# AIMail address self-setup — activate your own mailbox

**Scope: the agent-level activation code only.** Your user forwards you an
activation message; you activate your own mailbox from inside your agent
runtime, turn on the pull that brings the mail into your own inbound chain, and
reply with the `agentmail` skill. No CLI, no system-level credentials, no admin
key.

The **system-level** forms are out of scope here: creating/reusing an AIMail
*system* (shared-domain system identifier, dedicated domain,
`AIMAIL_PRODUCT_CODE`), self-hosting the gateway, and the bridge/push delivery
those use. Those are operator actions that need the CLI and the bridge in the
local environment, documented in the repository README (the four install
scenarios) and `docs/agent-integration.md`. Do not ask for a product code and
do not run `aimail install` for your own mailbox.

## 0. Which SDK you load — language is the only split

There are exactly two integration paths, told apart by **language**, never by
agent product:

- **Python** — load the `pysdk`: `pip install aimailsdk`, then `import aimail`
  once (it puts the SDK modules on `sys.path`; every other import works after
  it). Every Python host uses it; a platform adapter wraps the same functions
  and calls them for you.
- **TypeScript** — load `@aimail/mail-core`, the generic TS core:
  `npm install @aimail/mail-core`. If your host has a matching adapter
  package, that package wraps this core; a host without one uses the core
  directly.

Nothing else has to be installed for the agent-level path — no CLI, no bridge,
no adapter. The SDK download is the only fetch step.

The generic TypeScript core covers **agent-level** integration only. Adding a
system, a dedicated domain, a system key or push delivery is the system-level
form above and needs the CLI plus the bridge; the agent-level path never
requires either.

## 1. What you receive

A forwarded message in this shape:

```txt
Activate my AIMail mailbox <your-address> using the activation code
<shared_a-xxxxxxxx-...>. My safety officer (manager) is
<applicant-address>. AIMail gateway is at <https://gateway>.
```

Four values: your **address**, the **activation code** (`shared_a-…`), your
**safety officer** (the applicant address, already verified by email), and the
**gateway URL**. The code is single-use and expires (default 24h). The mailbox
you get is valid for the shared domain's `validity_days` (e.g. 15 days).

**Your safety officer is configured for you at activation** — the gateway
records it from the verified applicant address as part of the activation call.
There is no agent-side step for it: the value in the pasted line is
informational. Do not try to set or change it yourself; only the gateway
administrator can change it later.

## 2. Activate and persist — one call, no CLI

TypeScript — from `@aimail/mail-core` (the generic core; a host adapter wraps
it):

```ts
import { GatewayClient, activateAddressCodePersist } from "@aimail/mail-core"

const client = new GatewayClient(gatewayUrl, "")   // public endpoint: no key yet
const res = await activateAddressCodePersist(client, code, address, {
  gatewayUrl,                     // persisted into your config
  agentId: "<you>",               // optional: defaults to the address local-part
  platformHome: process.env.HOME, // optional: where the .agentmail pointer goes
})
// res.raw_key / res.system_id / res.email_address / res.expires_at / res.config_path
```

Python — from the `pysdk`:

```python
import aimail  # noqa: F401 — puts the installed SDK modules on sys.path
from aimail_tools import _GatewayClient

client = _GatewayClient(gateway_url, "")            # public endpoint: no key yet
res = client.activate_address_code_persist(code, address,
                                          agent_id="<you>", profile_dir=None)
```

Under the hood both call `POST /api/v1/activate-address-code` and then persist:

- your **own agent key** (never a system admin key) → `agentmail.json`,
  mode `0600`, register-isomorphic fields (`agent_id`, `email`, `gateway_url`,
  `domain`, `system_id`, `api_key`, `expires_at`);
- the **manager (safety officer) the gateway recorded** for this address,
  returned by the same activation call and stored as `manager_address` — it is
  the recipient of the contact-approval flow, and the gateway remains the
  source of truth for it;
- the identity pointer `.agentmail` (so later runs resolve which mailbox is
  yours);
- a create-if-absent system connection file for the hosting system
  (`shared_addr_<domain>`), created automatically on first activation.

## 3. Complete the loop: pull the mail in, then reply

An agent-level activation registers **no push target** for you, so the gateway
holds your mail in your inbox until you take it: that is **pull mode**. An empty
`webhook_url` is legal — not a defect — and the mail is still delivered; a value
there is the push form that system-level deployments use. Pull mode is opened
only by an agent-level activation code, and the binding it produces carries a
`system_id` starting with `shared_addr_`, which is the agent-level marker.

### 3.1 Start the pull entry for your own mailbox

If your host ships an adapter, the adapter starts the pull entry while it
initialises and stops it on dispose/shutdown, and it delivers each pulled mail
into **the same inbound chain push uses** — a pulled mail reaches you exactly
like a pushed one.

When you embed the SDK yourself, start it: one call, one polling loop per
agent-level binding, delivering into your own inbound entry.

TypeScript (`@aimail/mail-core`):

```ts
import { startAgentPullEntries, stopAgentPullEntries } from "@aimail/mail-core"

const handles = await startAgentPullEntries({
  // your inbound chain — the same entry push uses
  onEmail: (cfg, mail) =>
    processInboundMail(
      mail.body as Record<string, unknown>,
      mail.headers as Record<string, string>,
    ),
})

// on host shutdown:
stopAgentPullEntries(handles)
```

Python:

```python
import aimail  # noqa: F401 — puts the installed SDK modules on sys.path
from aimail_base import process_inbound_mail
from aimail_base import start_agent_pull_entries, stop_agent_pull_entries

# on_email(cfg, mail) hands the pulled mail to the entry push uses
handles = start_agent_pull_entries(
    on_email=lambda cfg, mail: process_inbound_mail(mail, mail.get("headers") or {}),
)

# on host shutdown:
stop_agent_pull_entries(handles)
```

Activating through the SDK's `activate_address_code` entry already hands the
just-activated address to the shared core (`set_agent_context`), so the
module-level mail tools work right after activation. If you drive the raw
client instead (`_GatewayClient`), call `set_agent_context` once with the
address local-part and `system_id`; without it those tools answer "aimail not
configured for this profile".

To drive it by hand instead, the underlying calls are `start_polling` (Python,
a blocking loop to run in a worker thread) plus `pull_list` and `pull_ack`
(both SDKs) for a single round.

### 3.2 Where a pulled mail lands

A pulled mail goes to the inbound handler **you** passed (onEmail in
TypeScript, on_email in Python) — the very same entry push targets, so
nothing about your mail handling changes between the two modes. Which local
endpoint an adapter exposes is an adapter fact, not part of this flow; the
per-platform endpoints are listed in `docs/agent-integration.md`.

### 3.3 Reply with the agentmail skill protocol

A pulled mail arrives in your inbound chain as an ordinary conversation turn, and
from there the `agentmail` skill governs everything: its six rounds are
understand → contextualize → execute → decide → reply or forward → remember.
Outbound goes through `send_mail` (reply on the same `message_id` so threading
holds), and `set_email_summary` at the end persists the thread state the next
turn starts from.

Verification of the whole loop: ask your user to send you one mail; when your
reply reaches their address, the agent-level path is proven end to end —
activate → persist → pull → inbound → reply.

## 4. First run: identity card and signature

Two different things with two different owners. Both are agent-side actions —
no CLI and no admin key involved.

**Identity card** — the text a stranger's WHOAMI query gets back. You write it
yourself; your own agent key is enough:

```ts
import { setPublicWhoami } from "@aimail/mail-core"

await setPublicWhoami({ systemId, email }, {
  text: "One or two lines: who I am, what I do",
})
```

```python
client.agent_state_put("public_whoami", "One or two lines: who I am, what I do")
```

Read it back with `GET /api/v1/agent-state/public_whoami` to confirm it landed.

**Signature** — appended by the gateway to every mail you send; never write it
into a body yourself. It is **not self-service**: your manager (safety officer)
owns it. Ask, then wait for their reply:

```txt
Please approve my persona/signature. Reply with exactly:

approve persona
persona: <two or three lines describing me>
signature: <the exact text to append to my mail>
```

The gateway consumes that reply — it is not delivered to you, in pull mode
either — and writes agent_signature / agent_persona into the address metadata.
Confirm with `GET /api/v1/whoami`: it returns manager_address, agent_signature
and agent_persona. Changing either value later needs another manager approval;
the gateway administrator is the only other path.

First run, end to end: install → activate → identity card → signature → pull →
reply.

## 5. Contract face (agent-internal names)

Single source of truth: `contract/aimail-contract.json`, mirrored one-for-one by
`pysdk/aimail_contract.py` and `tssdk/packages/mail-core/src/contract.ts`. Every
name below is a contract value, not a free choice.

| What | Value |
| ------------------------------- | ------------------------ |
| Skill and toolset registration name | `agentmail` |
| Per-address binding file | `agentmail.json` |
| System pointer file | `.agentmail` |
| Agent-level binding system prefix | `shared_addr_` |

The inbound paths and listener ports a host exposes belong to the host (or to
its adapter) and are listed in `docs/agent-integration.md`; the agent-level
flow above needs none of them, because the pull delivers to the handler you
pass in.

**Name boundary:** everything inside the agent is `agentmail` — that is the
skill/toolset name the host looks up, and the name you use with yourself.
`aimail` is the product / repository / CLI brand (the `~/.aimail` directory, the
`AIMAIL_*` environment variables). Do not mix the two: a name from one side does
not exist on the other.

## 6. Pull contract

- **Scope is the key's own mailbox.** Your agent-level key serves exactly one
  address — its own. The `emails` field in the pull request exists for shape
  compatibility with the bridge and is ignored by the gateway: your scope *is*
  the range.
- **Ack ownership is enforced server-side.** An ack deletes only rows belonging
  to your own address; ids that belong to another address affect 0 rows.
- **A failure never loses mail.** If your inbound handler throws, that delivery
  is not acked and is re-pulled on the next round; deliveries that already
  succeeded are not repeated. Dedup key is the delivery id.
- **Interval and batch size.** `AIMAIL_PULL_INTERVAL_MS` sets the round interval
  (default 30000 ms, floor 1000 ms); `AIMAIL_PULL_LIMIT` sets the batch (default
  20, gateway cap 200).
- **It can be turned off and stopped.** `AIMAIL_PULL=0|false|off|no` disables
  polling; `stopAgentPullEntries` (TypeScript) and `stop_agent_pull_entries`
  (Python) stop the loops — idempotent, effective immediately, and a polling
  loop is never the handle that keeps your host process alive.
- **Key expiry and renewal.** `expires_at` is on your binding. Before it lapses,
  ask your user to re-apply: you receive a new code and repeat §2, and renewal
  keeps the same address. Mail that arrived while you were down stays pending on
  the gateway and is pulled in on the next round.

## 7. Rules

- The activation code is **single-use**; a failed attempt costs it. Do not
  print it or your key — the key lands `0600` by itself.
- Your **safety officer (manager)** is recorded by the gateway at activation
  from the verified applicant address. Never attempt to set or change it —
  there is no agent-side or self-service path; changes belong to the gateway
  administrator.
- Never request or use a system admin key, and never run CLI install commands
  for your own mailbox: that is the system-level path, reserved for the
  operator.
- If activation fails with `code already claimed` / expired, ask for a fresh
  code; if it fails with a network error, retry the same call once.
