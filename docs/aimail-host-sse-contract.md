# AIMail Host SSE Activation Contract v1

> Status: normative profile for Agent hosts that use **SSE pull only**.
> Reference implementation: DSH (`tssdk/packages/agent-aimail/src/dsh/`).
> Contract constants: [`contract/aimail-contract.json`](../contract/aimail-contract.json).
> Detailed DSH design: [`docs/dsh-mail-architecture-v2.md`](dsh-mail-architecture-v2.md).

This document extracts the DSH SSE onboarding/activation closed loop into a
host-neutral contract. Other Agent systems SHOULD implement the same contract
while keeping their own host runtime, UI, session model and persistence
implementation. “MUST/SHOULD/MAY” are normative.

The contract has one goal:

> A non-CLI Agent can redeem a one-time activation code, bind a mailbox to a
> stable workspace, receive mail through one SSE stream, and reply from the
> same workspace — without ever exposing a gateway admin/domain/system key to
> the model conversation.

---

## 1. Terms

| Term | Meaning |
|---|---|
| Gateway | AIMail base/advanced HTTP service; authoritative for systems, domains, keys, activation codes and pending mail. |
| Host | An Agent product that runs tools and sessions (DSH, OpenClaw, Pi, …). |
| Adapter | Host-side implementation of this contract; normally a plugin/bundle. |
| Workspace | Durable, user-visible project/working directory. It owns mailbox routing; it is **not** a session. |
| Session | A disposable Agent conversation/turn context. It is a child of a workspace. |
| Binding | Per-address host configuration: mailbox identity, gateway URL, agent key, manager, workspace. |
| Activation code | One-time `sys-`/`addr-` code claimed from the gateway's `/apply` page. |
| Default worker | `agent@domain` or `agent.<system_name>@shared` created by system onboarding. |
| Manager / safety officer | Human reviewer for the mailbox; receives welcome cc and approves persona. |

---

## 2. Normative invariants

An SSE-profile adapter **MUST** satisfy all of these:

1. **SSE pull only.** No local inbound HTTP listener, webhook endpoint, HMAC
   inbound verifier, bridge key or bridge process is required for mail delivery.
2. **Codes, not keys, in the dialog.** The Agent conversation may contain a
   one-time system/address activation code and public gateway URL. It MUST NOT
   contain an admin key, domain key, raw system key or bridge key.
3. **Workspace is stable; session is disposable.** A mailbox binds to a
   workspace. A session MUST NOT be persisted as the mailbox's routing key.
4. **One address, one leaf.** Configuration, raw mail, attachments, meta, thread
   summaries, search index and the address ledger live under that address's
   directory. System-level config/resources are shared at the parent.
5. **Welcome is ordinary inbound mail.** Welcome and manager messages MUST use
   the same pending/SSE path as any other inbound mail. They MUST NOT depend on
   a webhook URL.
6. **Reply by thread, not by session drift.** Inbound replies are matched to a
   previous session by thread headers and local meta, then followed up in that
   session. A new thread creates a new session in the *binding's* workspace.
7. **Startup must not overwrite user intent.** A restart/ensure pass MUST NOT
   replace a workspace already chosen by an address-binding prompt with a
   default workspace.
8. **Acknowledge only after consumption.** A delivered pending item is acked
   only after the host has accepted it into a session/turn. Paused, unbound or
   failed items remain pending; delivery is at-least-once and must be
   idempotent by message/thread.
9. **No secrets in logs.** Raw keys, code values and full auth headers MUST NOT
   appear in logs or tool results.

---

## 3. Host capability contract

The host MUST expose equivalent capabilities, regardless of naming:

| Capability | Contract requirement |
|---|---|
| Tool registration | Register the grouped tool surface in §4; tool names SHOULD match exactly, or publish a mapping. |
| Workspace registry | List/open/create a workspace and return a stable `workspace_id` + absolute `workspace_path`. |
| Session creation | Create a session with an explicit `cwd` = binding workspace; optionally resume a session. |
| Message wakeup | Queue a user-role message into a live/new session and wake the Agent loop. |
| File persistence | Atomic writes with restrictive permissions for config/meta. |
| SSE consumer | Consume the gateway pending stream and ack successfully handled deliveries. |
| Secret storage | Bindings/keys stored outside prompts and outside normal logs. |

A host without a workspace model MAY map workspace to a durable project/root
directory, but MUST still provide a stable identifier/path and the same
“workspace, not session” semantics.

---

## 4. Tool surface

The Adapter MUST expose these semantic groups. Grouping is part of the contract;
the implementation may keep them in one entry or several.

### 4.1 `MAIL_TOOLS` (7)

```text
send_mail
manage_contacts
contact_profile
set_contact_profile
email_summary
set_email_summary
search_mail
```

### 4.2 `MAIL_BOARD` (7)

```text
board_status
board_task_list
board_task_show
board_heartbeat
board_members
board_roles
role_calibrator
```

### 4.3 `MAIL_ADMIN` (2)

```text
aimail_address
aimail_system
```

`aimail_address` is the only address lifecycle tool: `bind | pause | resume`.
It MUST reject system codes with a `wrong_tool`-style error.

`aimail_system` is the only system-onboarding tool: `bind | status`.
It MUST reject address codes.

The three groups together are the complete Agent-facing set (15 spec-backed
tools + the host-only system tool where applicable). Hosts that do not support
system onboarding MAY omit `aimail_system`, but MUST NOT use
`aimail_address` as a substitute.

---

## 5. Activation flows

### 5.1 System onboarding (`aimail_system bind`)

**Input** (no email, no manager address required from the user):

```text
gateway_url: <public gateway>
code:        <sys-… claimed on /apply>
system_name: <only when the code is unbound and the gateway is shared-domain>
domain:      <only when the code is unbound and the gateway has dedicated domains>
manager:     <optional system default; not the code recipient>
```

**Sequence:**

1. `POST /api/v1/public/activation-code/preflight {code}`; do not consume.
2. Validate `code_type` is system/product; validate the code's bound
   `system_name`/`domain` against the input; `system_name` XOR `domain` only when
   the code is unbound.
3. `POST /api/v1/activate-system` with the code and binding inputs.
4. Persist the system config and raw system key with restrictive permissions.
5. Mint/select a domain key; use it as the host's system/domain key; keep any
   other domain keys in the system config.
6. Create the default worker (`agent@domain` or `agent.<system_name>@shared`).
7. Bind the default worker to the **host's default workspace**.
8. Write the platform pointer (if the host uses one) and open the SSE system
   stream.

If a system already exists for the same gateway, a code-less call returns the
existing status; a code call follows the gateway's renewal/rotation result.

### 5.2 Address activation (`aimail_address bind`)

**Input:**

```text
action:     bind
email:      <mailbox address>
code:       <addr-… from /apply>  (optional when a system is onboarded and the host may use the domain key)
gateway_url:<required only when no local system exists>
```

`manager` MUST be rejected: the address manager comes from the gateway/onboarding,
not from the Agent dialog.

**Workspace rule (critical):**

1. Resolve the **current session's workspace** at bind time; open/create it in
   the host workspace registry.
2. Write `workspace_id` + `workspace_path` into the address binding.
3. Do not write `session_id` into the binding.

**Sequence:**

1. If a local system exists, select B1 (local-domain) and validate the email's
   domain/system; otherwise select B2 (shared-domain, no local system) and require
   both `gateway_url` and `code`.
2. With a code: preflight, validate code type/scope/email binding, then activate
   (`/api/v1/activate-address-code`; base may expose `/api/v1/activate-address`).
3. Without a code: only when a system exists; register through the gateway using
   the domain key and bind the workspace.
4. Persist the binding atomically; open/reconcile the address stream.
5. Return the effective workspace and mode (`code`, `register`, `link`).

A code-less bind is a **workspace link** update, not a new mailbox registration.

### 5.3 Pause / resume

- `pause` sets the local binding paused and stops acking/delivering.
- `resume` re-enables and performs one pending drain.
- Unbind is intentionally not part of this profile; termination is a gateway
  lifecycle decision.

---

## 6. Binding and data layout

The root is the host's AIMail home (default `~/.aimail`, overridable by
`AIMAIL_HOME`). File *names* are defined by the contract manifest
(`binding_file`, `pointer_file`); this document intentionally does not duplicate
those literals.

```text
<aimail_home>/
└── systems/<system_id>/
    ├── <system config>                 # gateway_url, admin/domain key, domains,
    │                                   # manager, system_home, save_raw_snapshots
    ├── <system raw key>                # only during system onboarding
    ├── board/role_prompt/              # shared board resources
    └── <clean_addr>/
        ├── <binding file>              # per-address config (see §6.1)
        ├── <address ledger>            # inbound/outbound event log
        └── mail/
            ├── YYYYMM/in-*.json        # inbound raw snapshot
            ├── YYYYMM/out-*.json       # outbound raw snapshot
            ├── YYYYMM/attch/<mid>/     # attachment copies / text extracts
            ├── meta/<first2>/<mid>.json
            ├── threads/<first2>/<tid>.json
            └── .search/index.db        # FTS index
```

`<clean_addr>` is the address with characters outside `[\w.-]` replaced by `_`
(for example `agent@l3.local` → `agent_l3.local`).

### 6.1 Address binding fields

Required for an SSE binding:

| Field | Meaning |
|---|---|
| `email` | Mailbox address. |
| `gateway_url` | Gateway base URL. |
| `domain` | Mail domain. |
| `system_id` | Owning system directory key. |
| `api_key` | Agent-scoped key (address-scoped stream). |
| `manager_address` | Safety officer / welcome cc target. |
| `workspace_id` | Stable host workspace id. |
| `workspace_path` | Canonical absolute workspace path. |

Optional: `system_name`, `expires_at`, `paused`, `preset`, `workspace_title`.

Forbidden as routing state: `session_id` (or any equivalent per-session key).
A legacy value MAY be ignored/cleared; it MUST NOT determine delivery workspace.

Push-profile fields (webhook URL/secret, HMAC headers) are **not part of this
profile**. If a legacy binding contains them, the SSE adapter MUST ignore them
and MUST NOT use them to route welcome/manager mail.

### 6.2 Permissions

- address/system directories: 0700
- binding/system config/pointer files: 0600
- raw snapshots/attachments: 0600/0700

---

## 7. SSE delivery contract

### 7.1 Stream

```text
GET /api/v1/admin/pending/stream
```

- One stream slot per key; a second concurrent connection may receive `409
  occupied`.
- The stream carries pending deliveries, not a second mail store.
- Heartbeat/ping frames keep the connection alive; a long silence is a dead
  link and must be reconnected with backoff.
- Optional long-poll fallback: `GET /api/v1/admin/pending` may be used as a
  compatibility fallback, but SSE is the primary transport.

Authentication is the gateway's normal API-key signature scheme:
`X-Api-Identity` + timestamp/signature headers; the identity is the binding's
system id (system stream) or email (address stream). Credentials never appear
in the prompt.

### 7.2 Ack

```text
POST /api/v1/admin/pending/ack  { ids: [...] }
```

- Ack only after the host accepts the payload into a session (or intentionally
  discards a ping/duplicate).
- Do not ack on binding miss, workspace miss, paused, or Agent creation failure.
- Deduplicate/ack duplicates idempotently by message id/thread.
- Delivery is at-least-once; the host must tolerate replay after reconnect.

### 7.0 Shared-stream invariant

The SSE endpoint is a **gateway-level multiplexer**, not a per-mailbox or
per-workspace connection.

- All bindings with the **same `gateway_url` and the same credential scope**
  MUST share one SSE consumer/connection.
- One connection carries deliveries for multiple addresses; each delivery is
  routed by recipient → binding → workspace → session.
- A host MUST NOT open one SSE stream per mailbox, per workspace or per session.
- Changing `gateway_url` (or credential scope) is the only reason to open a
  second stream for a different gateway.
- The same rule applies after reconnect and after address bind/pause/resume:
  reuse/reconcile the existing gateway stream; do not stack duplicate streams.

Scope caveat: a gateway key is scoped (system/domain vs address). Where a
system/domain key covers all target addresses, one stream is mandatory. If a
B2 deployment only has per-address agent keys, those keys cannot observe each
other; per-address streams are a **fallback until the gateway provides a
covering scope key**, and even then the host keeps one router and never opens a
stream per workspace/session.

### 7.2b Read-on-boot arming

A host MUST arm streams for **existing on-disk bindings** during startup, not
only after an in-process `aimail_address`/`aimail_system` call. A headless/out-of-
process activator may write the binding before the host starts; requiring a
second in-host activation is a contract violation.

- DSH reconciles the hub at `mail-hub` start and re-arms once after startup.
- Pi/OpenClaw read the pointer/bindings at startup and arm their pull loops.
- Startup MUST log an "SSE armed/reconcile" evidence line (no credentials).
- If no binding exists, startup still succeeds with zero streams.

### 7.3 Stream selection

| Host state | Stream |
|---|---|
| System onboarded, domain key available | one system stream (`identity=system_id`) |
| No system, shared-domain address | one address stream per binding (`identity=email`) |

A system stream may carry mail for multiple addresses; route each delivery by
recipient binding. Never ack/steal mail whose recipient has no local binding.

---

## 8. Inbound routing and session semantics

For each pending delivery:

1. Resolve recipient → address binding.
2. If no binding, paused, or binding is degraded → do not ack; leave pending.
3. Resolve the stable workspace from `workspace_id`/`workspace_path`; if the
   path is missing, create/open the host default workspace and persist it
   **only when the binding has no workspace**.
4. Compute the thread root from, in order:
   `references[0]` → `in_reply_to` → `message_id`.
5. Read local meta for the recipient/root to find the owning session.
6. If a live/resumable session exists in the same workspace → follow up it.
7. Otherwise create a **new disposable session** with `cwd = workspace_path`.
8. Deliver the processed inbound message into that session; stamp local meta with
   session id/workspace path; ack.
9. When the turn is done, the session may be disposed; the binding and workspace
   remain. Do not persist the session id as the mailbox routing key.

Consequences:

- Welcome (first inbound) creates a session in the address's binding workspace.
- The manager's reply/approval has a thread root pointing at the welcome; it is
  followed up to the **same session/workspace**.
- A restart/binding ensure pass does not move the mailbox to another workspace.

---

## 9. Welcome / manager message contract

- Welcome is generated by the gateway as a normal inbound message. It must be
  queued in the pending stream when the binding has no push endpoint.
- The welcome body's three labelled lines (`persona:`, `signature:`,
  `current_time:`) and the `[CMD] approve persona` manager command are part of
  the semantic contract; adapters must not rewrite them.
- The manager's cc/reply is normal inbound mail and follows §8.
- The host must not require a local listener to receive welcome or manager mail.
- The first welcome may be replayed after reconnect; deduplicate by message id
  and do not create a second session/ack twice.

---

## 10. Status and observability

Adapters SHOULD expose a host-local status object:

```json
{
  "mode": "sse",
  "connected": true,
  "streams": ["system:<system_id>"],
  "lastEventAt": "<ISO-8601>",
  "lastError": ""
}
```

Status MUST NOT contain keys, codes or full message bodies. The address ledger
and raw snapshots are the durable audit surface; status is operational only.

---

## 11. Conformance matrix

An adapter is conformant when it can demonstrate all of these (automated where
possible):

| ID | Scenario | Acceptance |
|---|---|---|
| C1 | System code, shared domain | Config + default worker + domain key written; no admin/domain key appears in the prompt/log. |
| C2 | System code, dedicated/independent gateway | Same as C1 with the bound domain; no CLI needed. |
| C3 | Address B2, no system | Address code activates; binding has gateway URL + agent key + current workspace. |
| C4 | Address B1, system exists | Code/no-code path binds the current workspace; wrong code type/domain is rejected. |
| C5 | Address activation from a session | Binding `workspace_path` equals that session's workspace; restart preserves it. |
| C6 | Welcome delivery | No webhook/local listener; welcome arrives via pending/SSE and creates a session in the bound workspace. |
| C7 | Manager reply | Approval follows the welcome thread into the same session/workspace. |
| C8 | Pause/resume | Paused mail is not acked; resume drains pending once. |
| C9 | Ack/restart | Reconnect/replay is idempotent; unhandled items remain pending; no duplicate session/ack. |
| C10 | Boundary/security | No admin/domain/system/bridge key in dialog; 0600/0700 files; no secrets in logs. |
| C11 | Shared SSE | Same gateway + credential scope reuses one stream; multiple addresses route to their own workspace/session; no per-workspace/session stream. |

A host that passes C1–C10 may call itself **AIMail Host SSE v1 conformant**.

The multi-host tssdk unification plan (review draft) is in
[`docs/tssdk-sse-unification-plan.md`](tssdk-sse-unification-plan.md).

The DSH reference has two L2 profiles during the transition:
[`tests/dsh-acceptance/L2-PROFILES.md`](../tests/dsh-acceptance/L2-PROFILES.md)
— legacy CLI/push L2 and the new SSE L2 (no CLI).

---

## 12. Versioning and compatibility

- This profile is `aimail-host-sse-v1`.
- Contract constants and file names are owned by
  `contract/aimail-contract.json`; host implementations MUST consume them rather
  than re-typing literals in new code.
- Push-based hosts (legacy webhook/bridge) are a separate profile. An SSE host
  MUST NOT be required to implement the push profile, and vice versa.
- Gateway API additions are backwards-compatible; activation code formats and
  error codes must remain stable within v1.
- Tool groups may be renamed in a host UI, but semantic mapping MUST remain:
  `MAIL_TOOLS` (mail), `MAIL_BOARD` (board), `MAIL_ADMIN` (address/system).

---

## 13. Implementation notes (non-normative)

The DSH reference implementation demonstrates:

- System onboarding as a DSH-only `aimail_system` tool, registered with the same
  tool entry as the mail tools so it is visible in the Agent tool list.
- Address binding writing `workspace_id`/`workspace_path` from the current
  session; plugin startup only fills a missing workspace.
- A single SSE hub with system/address streams, at-least-once ack, thread-meta
  session mapping and disposable sessions.
- Fresh-host fallback: create the host's default workspace, then persist it, so
  the first welcome has a stable destination.
- Regression tests for startup workspace preservation and welcome SSE delivery.

---

## Appendix A — Pi single-address profile

Pi exposes project/`cwd` as a de-facto workspace (sessions are stored under
`~/.pi/agent/sessions/<encoded-cwd>/` and each session records its cwd), but its
public extension API has no background session creation or `deliverToSession`.
`newSession` exists only on `ExtensionCommandContext`; `sendUserMessage` targets
the current session.

Therefore Pi is a **single-address host** in this profile:

- `aimail_system` MUST NOT be exposed on Pi (no system-level onboarding).
- One Pi process/workspace MAY have at most one active address.
- The address binding workspace MUST equal the running Pi cwd.
- Pi consumes only its pointer's address; no fan-out over other addresses.
- Multi-address/system onboarding is a DSH/OpenClaw capability, not Pi.
- Pi's inbound delivery remains current-session; thread/session isolation is
  unsupported until Pi provides a background session API.

This is a deliberate degradation, not an SSE transport limitation.

## Appendix B — OpenClaw thread session key

OpenClaw `/hooks/agent` accepts a caller `sessionKey` only when the host enables:

```json
{
  "hooks": {
    "allowRequestSessionKey": true,
    "allowedSessionKeyPrefixes": ["agent:main:aimail:"]
  }
}
```

The adapter sends a thread-stable key:

```text
agent:<agentId>:aimail:<sha256(threadRoot).slice(0,24)>
```

- Same thread → same session; new thread → new session.
- If the flag is disabled or the key does not match an allowed prefix, the
  adapter omits `sessionKey`; OpenClaw creates a fresh hook session per delivery
  (workspace routing still works, thread continuity does not).
- This does not change the binding contract: only workspace is persisted; the
  session key is per-delivery host metadata, never written to the binding.

Risk note: enabling `allowRequestSessionKey` lets hook-token holders choose a
session key (message injection into that session). Restrict prefixes, protect
the hook token, and plan session retention/cleanup.
