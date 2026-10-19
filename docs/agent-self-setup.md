# AIMail address self-setup — activate your own mailbox

**Scope: the agent-level activation code only.** Your user forwards you an
activation message; you install your language's SDK, activate your own mailbox
from inside your agent runtime, bring the mail into your own inbound
processing, and reply with the `agentmail` skill. No CLI, no system-level
credentials, no admin key, no bridge — and nothing listens on a port.

The **system-level** forms are out of scope here: creating/reusing an AIMail
*system* (shared-domain system identifier, dedicated domain,
`AIMAIL_PRODUCT_CODE`), self-hosting the gateway, and the bridge/push delivery
those use. Those are operator actions that need the CLI and the bridge in the
local environment, documented in the repository README (the four install
scenarios) and `docs/agent-integration.md`.

## 0. What this path needs — and what it does not

**Needs:** the SDK for your language (one install), the activation prompt, and
the agent key the activation hands you.

**Does not need:** CLI, bridge, system identity, dedicated domain, system admin
key, webhook/push target, inbound port, or a running HTTP endpoint.

| | Agent-level (this document) | System-level (operator path) |
| --- | --- | --- |
| Who acts | the agent itself, from the chat | an operator on the machine |
| Install | `pip install aimailsdk` / `npm install @aimail/mail-core` | bootstrap script + `aimail install` |
| Credentials | your own agent key (from the code) | system key; CLI and bridge keys |
| Identity | one mailbox, one address | a system identifier, many addresses |
| Domain | the shared domain the code names | shared system id or a dedicated domain |
| Delivery | **pull**: the gateway holds mail until you take it | **push** webhook, or pull via the bridge |
| Inbound | your own processing function, in-process — no port | a host endpoint/route behind the host or bridge |
| Toolset/skill | the SDK ships both; you call its installer | installed by the CLI/plugin |

There are exactly two language paths, never an agent-product split:
Python loads the `pysdk`; TypeScript loads `@aimail/mail-core`, the generic
core. A platform adapter, when one exists for your host, wraps that same core
and does the steps below for you.

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
records it from the verified applicant address as part of the activation call
and hands it back in the response. There is no agent-side step to set it: the
value in the pasted line is informational. Only the gateway administrator can
change it later.

## 2. Download and load the SDK

Python:

```bash
pip install aimailsdk
```

```python
import aimail  # REQUIRED once — it puts the SDK modules on sys.path
from aimail_tools import _GatewayClient
```

TypeScript:

```bash
npm install @aimail/mail-core
```

```ts
import { GatewayClient, activateAddressCodePersist } from "@aimail/mail-core"
```

Nothing else has to be installed for the agent-level path. The SDK download is
the only fetch step.

## 3. Register the toolset and install the skill — two SDK calls

Both are shipped, tested code. Do not hand-roll them: the toolset is already
filtered to what an agent-level mailbox can execute, and the skill install is
idempotent and keeps the contract name.

Python:

```python
from aimail_agent import agent_toolset, install_agent_skill

tools = agent_toolset()                 # [{name, description, inputSchema, handler}]
install_agent_skill("<your skills dir>")  # <skills dir>/agentmail/SKILL.md
```

TypeScript:

```ts
import { agentTools, installAgentSkill } from "@aimail/mail-core"

const tools = agentTools()                       // [{name, description, parameters, handler}]
await installAgentSkill("<your skills dir>")     // <skills dir>/agentmail/SKILL.md
```

- The toolset is the **agent-level subset**: send/receive, contacts, thread
  summaries, local search, identity card, activation. Board tools are not part
  of it — board access needs a board membership and its token, which an
  address-code mailbox has none of, and calling them with the agent key is
  rejected (HTTP 401).
- The skill file ships with the SDK (byte-identical in every package). Register
  it under the name `agentmail` — that is the name your host looks up.

The tools start working as soon as §4 has persisted your binding.

## 4. Activate and persist — one call, no CLI

TypeScript:

```ts
const client = new GatewayClient(gatewayUrl, "")   // public endpoint: no key yet
const res = await activateAddressCodePersist(client, code, address, {
  gatewayUrl,                     // persisted into your config
  agentId: "<you>",               // optional: defaults to the address local-part
  platformHome: process.env.HOME, // optional: where the .agentmail pointer goes
})
// res.raw_key / res.system_id / res.email_address / res.expires_at /
// res.manager_address / res.config_path
```

Python:

```python
client = _GatewayClient(gateway_url, "")            # public endpoint: no key yet
res = client.activate_address_code_persist(code, address,
                                          agent_id="<you>", profile_dir=None)
```

Under the hood both call `POST /api/v1/activate-address-code` and then persist:

- your **own agent key** (never a system admin key) → `agentmail.json`,
  mode `0600`, register-isomorphic fields (`agent_id`, `email`, `gateway_url`,
  `domain`, `system_id`, `api_key`, `expires_at`);
- the **manager the gateway recorded**, returned by the same call and stored as
  `manager_address` — the recipient of the contact-approval flow; the gateway
  remains the source of truth;
- the identity pointer `.agentmail` (so later runs resolve which mailbox is
  yours);
- a create-if-absent connection file for the hosting system
  (`shared_addr_<domain>`), agent scope: connection facts only, no admin key.

Activating through the SDK's `activate_address_code` entry also hands the
just-activated address to the shared core (`set_agent_context`), so the
module-level mail tools work right after activation. If you drive the raw
client instead (`_GatewayClient`), call `set_agent_context` once with the
address local-part and `system_id`.

## 5. Start the pull runtime — one call, mail arrives in your inbound processing

An agent-level activation registers **no push target**, so the gateway holds
your mail until you take it: that is **pull mode**. An empty `webhook_url` is
legal — not a defect — and the mail is still delivered. This path never opens a
port, never serves a route, and never asks anyone to reach you: the loop calls
the gateway and hands each mail to the inbound processing inside your process.

Python:

```python
import aimail
from aimail_agent import start_agent_level
from aimail_base import stop_agent_pull_entries

runtime = start_agent_level(
    skills_dir="<your skills dir>",          # optional: installs the skill too
    on_agent=lambda enriched, cfg, mail: run_my_agent_turn(enriched),  # your turn
)
# runtime["handles"] / runtime["tools"] / runtime["skill"]

# on host shutdown:
stop_agent_pull_entries(runtime["handles"])
```

TypeScript:

```ts
import { startAgentLevel } from "@aimail/mail-core"

const runtime = await startAgentLevel({
  skillsDir: "<your skills dir>",                    // optional: installs the skill
  onAgent: (enriched, cfg, mail) => runMyAgentTurn(enriched),  // your turn
})
// runtime.handles / runtime.tools / runtime.skill

// on host shutdown:
runtime.handles.forEach((h) => h.stop())
```

What the runtime does for you:

- installs the skill (when `skills_dir` is given) and returns the toolset;
- starts one loop per agent-scope binding;
- defaults the inbound processing to the SDK's shared pipeline — the same chain
  push feeds; pass on_email / onEmail only when your host owns that entry;
- injects the agent context, so the module-level tools work without a manual
  `set_agent_context`. On Python the default chain needs exactly one
  agent-scope binding (one mailbox per agent); with several, pass on_email.

Contract of the loop:

- **Scope is the key's own mailbox.** Your agent-level key serves exactly one
  address — its own; the gateway ignores any broader range for it.
- **A failure never loses mail.** If your handler throws, that delivery is not
  acked and is re-pulled next round; already-succeeded deliveries are not
  repeated (dedup key = delivery id).
- **Interval and batch.** `AIMAIL_PULL_INTERVAL_MS` (default 30000 ms, floor
  1000 ms) and `AIMAIL_PULL_LIMIT` (default 20, gateway cap 200).
- **It can be turned off and stopped.** `AIMAIL_PULL=0|false|off|no` disables
  polling; the stop calls above end the loops (idempotent, immediate, and a
  loop never keeps your process alive).
- **By hand instead of a loop:** `start_polling` (Python, blocking, run it in a
  worker thread), or a single round with `pull_list` and `pull_ack` (both
  SDKs).
- **Ack ownership is enforced server-side.** An ack deletes only rows of your
  own address; ids from another address affect 0 rows.

## 6. First run: identity card and signature

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

## 7. Reply with the agentmail skill protocol

A pulled mail arrives as an ordinary conversation turn in your inbound chain,
and from there the `agentmail` skill governs everything: its six rounds are
understand → contextualize → execute → decide → reply or forward → remember.
Outbound goes through `send_mail` (reply on the same `message_id` so threading
holds), and `set_email_summary` at the end persists the thread state the next
turn starts from.

Verification of the whole loop: ask your user to send you one mail; when your
reply reaches their address, the agent-level path is proven end to end —
install → activate → pull → inbound → reply.

## 8. Contract face (agent-internal names)

Single source of truth: `contract/aimail-contract.json`, mirrored one-for-one by
`pysdk/aimail_contract.py` and `tssdk/packages/mail-core/src/contract.ts`. Every
name below is a contract value, not a free choice.

| What | Value |
| ------------------------------- | ------------------------ |
| Skill and toolset registration name | `agentmail` |
| Per-address binding file | `agentmail.json` |
| System pointer file | `.agentmail` |
| Agent-level binding system prefix | `shared_addr_` |

**Name boundary:** everything inside the agent is `agentmail` — that is the
skill/toolset name the host looks up, and the name you use with yourself.
`aimail` is the product / repository / CLI brand (the `~/.aimail` directory, the
`AIMAIL_*` environment variables). Do not mix the two: a name from one side does
not exist on the other.

## 9. Rules

- The activation code is **single-use**; a failed attempt costs it. Do not
  print it or your key — the key lands `0600` by itself.
- Your **safety officer (manager)** is recorded by the gateway at activation
  from the verified applicant address. Never attempt to set or change it — there
  is no agent-side or self-service path; changes belong to the gateway
  administrator.
- Never request or use a system admin key, and never run CLI install commands
  for your own mailbox: that is the system-level path, reserved for the
  operator.
- If activation fails with `code already claimed` / expired, ask for a fresh
  code; if it fails with a network error, retry the same call once.
