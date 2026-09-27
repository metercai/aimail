# AIMail address self-setup — activate your own mailbox

**Scope: the agent-level activation code only.** Your user forwards you an
activation message; you activate your own mailbox from inside your agent
runtime, turn on the pull that brings the mail into your own inbound chain, and
reply with the `agentmail` skill. No CLI, no system-level credentials, no admin
key.

The **system-level** forms are out of scope here: creating/reusing an AIMail
*system* (shared-domain system identifier, dedicated domain,
`AIMAIL_PRODUCT_CODE`), self-hosting the gateway, and the bridge/push delivery
those use. Those are operator actions, documented in the repository README (the
four install scenarios), `docs/AGENT-INTEGRATION.md` and `cli/README.md`. Do not
ask for a product code and do not run `aimail install` for your own mailbox.

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

TypeScript platforms (openclaw / pi / dsh) — from `@aimail/mail-core`:

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

Python platforms (hermes / deer-flow) — from the pysdk:

```python
from aimail_tools import _GatewayClient

client = _GatewayClient(gateway_url, "")            # public endpoint: no key yet
res = client.activate_address_code_persist(code, address,
                                          agent_id="<you>", profile_dir=None)
```

Under the hood both call `POST /api/v1/activate-address-code` and then persist:

- your **own agent key** (never a system admin key) → `agentmail.json`,
  mode `0600`, register-isomorphic fields (`agent_id`, `email`, `gateway_url`,
  `domain`, `system_id`, `api_key`, `expires_at`);
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

The pull entry starts by itself on every adapted platform (Hermes, OpenClaw,
DSH, Pi, DeerFlow): the adapter starts it while it initialises and stops it on
dispose/shutdown, and it delivers each pulled mail into **the same inbound chain
push uses** — a pulled mail reaches you exactly like a pushed one.

When you embed the SDK in a host that has no adapter yet, start it yourself: one
call, one polling loop per agent-level binding, delivering into your own inbound
entry.

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
from aimail_base import process_inbound_mail
from aimail_base import start_agent_pull_entries, stop_agent_pull_entries

# on_email(cfg, mail) hands the pulled mail to the entry push uses
handles = start_agent_pull_entries(
    on_email=lambda cfg, mail: process_inbound_mail(mail, mail.get("headers") or {}),
)

# on host shutdown:
stop_agent_pull_entries(handles)
```

To drive it by hand instead, the underlying calls are `start_polling` (Python,
a blocking loop to run in a worker thread) plus `pull_list` and `pull_ack`
(both SDKs) for a single round.

### 3.2 Where a pulled mail lands (inbound entry per platform)

The pull hands the mail to the platform's own inbound entry — the very same one
push targets, so nothing about your mail handling changes between the two
modes.

| Platform | Inbound entry | Kind |
| -------- | ---------------------------------------------------- | -------------------------- |
| Hermes | `/webhooks/aimail-inbound` | Long-running app route |
| OpenClaw | `/aimail/inbound` on the host gateway (port 18789) | Inside the host process |
| DSH | `/aimail/inbound` on port 9099 | Inside the host process |
| Pi | `/aimail/inbound` on port 9101 | Inside the host process |
| DeerFlow | `/aimail/inbound` on port 8001 | Long-running app route |

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

## 4. Contract face (agent-internal names)

Single source of truth: `contract/aimail-contract.json`, mirrored one-for-one by
`pysdk/aimail_contract.py` and `tssdk/packages/mail-core/src/contract.ts`. Every
name below is a contract value, not a free choice.

| What | Value |
| ------------------------------- | ------------------------ |
| Inbound path (all but Hermes) | `/aimail/inbound` |
| Inbound path (Hermes, the only exception) | `/webhooks/aimail-inbound` |
| Skill and toolset registration name | `agentmail` |
| Per-address binding file | `agentmail.json` |
| System pointer file | `.agentmail` |
| Agent-level binding system prefix | `shared_addr_` |

Ports are configurable (defaults 9099 for DSH, 9101 for Pi, 8001 for DeerFlow,
18789 = the OpenClaw host gateway's own port); **paths are not**. Hermes is the
single path exception because its inbound is a route of the Hermes gateway
itself.

**Name boundary:** everything inside the agent is `agentmail` — that is the
skill/toolset name the host looks up, and the name you use with yourself.
`aimail` is the product / repository / CLI brand (the `~/.aimail` directory, the
`AIMAIL_*` environment variables). Do not mix the two: a name from one side does
not exist on the other.

## 5. Pull contract

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

## 6. Rules

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
