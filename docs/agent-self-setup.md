# AIMail address self-setup (type 3) — activate your own mailbox

**Scope: address-level activation only.** Your user forwards you an activation
message; you activate your own mailbox from inside your agent runtime. No CLI,
no system-level credentials, no admin key.

System-level activation (create/reuse an AIMail *system*, `AIMAIL_PRODUCT_CODE`)
is an **operator action** performed with the `aimail` CLI — it is out of scope
here and is documented in the repository README/deployment guide. Do not ask
for a product code and do not run `aimail install` for your own mailbox.

## 1. What you receive

A forwarded message in this shape:

```
Activate my AIMail mailbox <your-address> using the activation code
<shared_a-xxxxxxxx-...>. AIMail gateway is at <https://gateway>.
```

Three values: your **address**, the **activation code** (`shared_a-…`), the
**gateway URL**. The code is single-use and expires (default 24h). The mailbox
you get is valid for the shared domain's `validity_days` (e.g. 15 days).

## 2. Activate — one call, no CLI

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

## 3. Verify (both directions)

1. **Outbound**: send a mail with the mail tools (`send_mail`) — it must be
   accepted by the gateway.
2. **Inbound**: ask your user to send you a mail at your address, then pull:

   ```python
   client.pull_list(limit=20)     # -> batches[].deliveries[]
   client.pull_ack([...ids])      # ack what you consumed
   ```

   Seeing your own mail proves the full chain (gateway inbound → your key →
   local tools). Use `start_polling()` for continuous delivery.
3. **Expiry**: `expires_at` is on the config. Before it lapses, ask your user
   to re-apply — you receive a new code and repeat step 2 (renewal keeps the
   same address).

## 4. Rules

- The activation code is **single-use**; a failed attempt costs it. Do not
  print it or your key — the key lands `0600` by itself.
- Never request or use a system admin key, and never run CLI install commands
  for your own mailbox: that is the system-level path, reserved for the
  operator.
- If activation fails with `code already claimed` / expired, ask for a fresh
  code; if it fails with a network error, retry the same call once.
