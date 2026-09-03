> **[中文版](README_zh.md)**

# AIMail

**A dedicated email system for AI agents.**

**AIMail** is a highly controllable, network-adaptable, open-collaboration email infrastructure purpose-built for AI agents — enabling them to communicate, interact, and collaborate with the outside world just like humans do.

- **Seamless access to the global network:** Built on [aimail-gateway](https://github.com/metercai/aimail-gateway), a bidirectional SMTP-HTTP gateway that connects any Agent platform (such as [Hermes Agent](https://github.com/nousresearch/hermes-agent)) to the global email network with zero friction.
- **Independent identity & autonomous interaction:** Every Agent has a globally unique email address, enabling it to initiate conversations, manage context, and engage deeply with individuals, teams, workflows, or other agents.
- **Open protocols & human-agent co-working:** Free from platform lock-in. Standard email protocols and collaboration primitives, built on decentralized email infrastructure, create a cross-network, open ecosystem for human-agent hybrid collaboration.

---

## Why AIMail?

Email is the most fundamental and widely-used communication tool on the internet — structured, persistent, and inherently formal. It supports both private 1:1 conversations and multi-party collaboration with equal ease.

AIMail is neither IM nor a traditional mailbox. The key differences:

| Dimension | IM | Traditional Mailbox | **AIMail** |
|-----------|-----|---------------------|---------------|
| **Identity** | Platform-bound, closed | Globally unique, open | Globally unique, open |
| **Content** | Fragmented, informal | Structured, formal | Structured, formal |
| **Access** | Proprietary API/SDK | POP3/IMAP, provider-dependent | SMTP + Webhook, self-hosted |
| **Latency** | Real-time, resource-heavy | Polling, high latency | Webhook push, near real-time |
| **Access Control** | Contact list, group permissions | Open, spam-prone | Default whitelist, bidirectional control |
| **Multi-party** | Group chat, unstructured | Forward/CC, threaded | Same as email + A2A Board, multi-role autonomous collaboration |

AIMail is not about teaching agents to use email. It's about giving agents email as a **protocol-native collaboration medium** — with humans and other agents alike.

---

## Use Cases

- **Contract Review:** Legal Agent takes over the contract inbox. Send agreements as attachments — the Agent auto-parses clauses, flags risks, and replies with annotations, CC'ing approvers. Full audit trail preserved. [→ example](examples/01-contract-review.md)
- **Progress Reports:** Agent periodically summarizes project status, risks, and milestones into structured reports, auto-sending to project members. Customize content by role (executive summary for leaders, details for executors). [→ example](examples/02-progress-report.md)
- **Clarification Requests:** When Agent encounters contradictions or gaps during analysis, it automatically emails the relevant colleague with context. Upon reply, the Agent parses the answer and continues without human tool-switching.
- **Survey Distribution:** Agent sends survey emails in bulk, tracks response progress, sends reminders, aggregates results, and emails the analysis back to the initiator. [→ example](examples/04-survey.md)
- **Process Collaboration:** In a website redesign involving designer Agent, frontend Agent, and PM, the A2A Board syncs all communication and decisions via email. When a design is finalized, notifications automatically trigger the next role to begin development. [→ example](examples/05-a2a-collaboration.md)
- **Financial Pre-audit:** Employee CCs the pre-audit Agent on expense reports. Agent verifies receipts, compliance, and budget — replying "approved", "rejected", or "needs supplement" — CC'ing the finance reviewer for final approval. [→ example](examples/06-financial-preauth.md)
- **Customer Support:** Agent takes over `support@` inbox. Auto-classifies intent and sentiment. Answers FAQs (password reset, order lookup) automatically. Escalates complex cases to human agents with context summaries. [→ example](examples/07-customer-support.md)

AIMail seamlessly integrates AI agents into any email-based workflow — contract review, progress reporting, clarification loops, surveys, cross-role collaboration, financial pre-audit, customer support, and beyond.

---

## Key Advantages

1. **Dual SMTP-HTTP Relay, Ordered Inbound & Outbound**  
SMTP receive → Webhook push. HTTP send → SMTP relay → Webhook internal delivery. Four lanes, unified scheduling, full-chain logging.

2. **Multi-Layer Security, Default Whitelist**  
Default whitelist prevents unauthorized senders from reaching the Agent, and prevents the Agent from sending to unauthorized recipients. Bidirectional control with security officer confirmation for critical operations.

3. **Auto Markdown Conversion, LLM-Friendly**  
Rich HTML emails are automatically converted to clean Markdown — stripped of styling noise. Agents read structured content directly.

4. **Email is Conversation, Conversation is Instruction**  
Sending and receiving email IS the conversation, with context automatically appended. Multiple types of instruction emails make conversations programmable and executable, seamlessly embedding into daily workflows.

5. **Built-in Collaboration Primitives and Board, Human-Agent Co-working**  
Native A2A collaboration board with customizable workflow engine. 20+ instruction verbs + 10 auto-notification types + collaboration primitives. Supports cross-system, heterogeneous Agent collaboration across the internet.

6. **Multi-Mode Message Delivery, Any Network Environment**  
Webhook Push/Pull dual mode coexists, adapting to diverse Agent types and network conditions.

7. **Multi-Role Agent Addresses, Dynamic Identity Switching**  
One Profile supports multiple Personas (e.g. `sales.bob@domain` / `support.bob@domain`). Sending auto-matches identity; receiving auto-identifies Persona for context switching.

8. **One-Click Integration & Diagnostics via `aimail` CLI**  
`./aimail install` sets up the whole chain (activation → bridge → tools/skills → registration); `check`/`ping`/`welcome` diagnose the full loop; `stats`/`domain`/`uninstall` manage the machine from one entry.

---

## Repository Layout

Single repo hosting the CLI, the Python runtime SDK and the TypeScript SDK
side by side:

- `cli/` — `aimail` maintenance CLI + subcommands + platform installers
  (migration target for a future Rust binary)
- `pysdk/` — Python runtime SDK (pip `aimail`): gateway client, platform
  adapters, board resources; wheel layout mirrors this tree 1:1
- `tssdk/` — TypeScript SDK (npm `@aimail/*`, `dsh-aimail`, `openclaw-aimail`,
  `pi-aimail`), migrated from the former aimail-sdk-ts repo
- `bridge/` — aimail-bridge binary distributions
- `examples/` — mail templates / A2A examples
- `docs/` — guides, board docs, maintenance notes

## Quick Start

### New machine in five steps

Prerequisites: Linux + Python 3.10+ and a reachable
[aimail-gateway](https://github.com/metercai/aimail-gateway) (or an
activation code for the public gateway at `amail.token.tm`).

**1. Install the agent host** — dsh / OpenClaw / pi / deer-flow / Hermes
(their own docs; any host works — the matching SDK adapter is what
matters later).

**2. Set the machine env vars** — copy-paste, edit, run. Two core values
first, the credential second (set only what you need):

```bash
export AIMAIL_URL=https://amail.token.tm        # where the gateway lives
export AIMAIL_MANAGER_ADDRESS=you@example.com   # default manager (receives welcome)
# then one credential (see install):
#   export AIMAIL_ADMIN_KEY=…                  # existing system
#   export AIMAIL_PRODUCT_CODE=…                # new activation
#   export AIMAIL_SYSTEM_NAME=…                 # (shared domain: agent.{name}@…)
```

That is all — run the next step in this same terminal. The bootstrap
below persists these into `~/.aimail/.env` for you, so future terminals
keep working. (To persist them yourself instead: append the same lines
to `~/.bashrc`.)

**3. Bootstrap the CLI and initialize the machine environment** (one
command, installs `aimail` into `~/.local/bin` and runs the machine-level
init — main dir, disk headroom, gateway/bridge decision):

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

**4. Install — via the CLI or the host, both work.** CLI path activates
the system and provisions the platform in one non-interactive command
(`--home` picks the platform: `~/.dsh`, `~/.openclaw`, `~/.pi`, `~/.hermes`,
or a deer-flow backend dir):

```bash
aimail install --home ~/.dsh
```

Host path (environment already initialized): install the SDK adapter
through the host — `openclaw plugins install openclaw-aimail`,
`dsh plugin --profile web add dsh-aimail`, `pi install npm:pi-aimail`, or
`python -m aimail.install --type hermes|deerflow` — the SDK self-checks
the environment (missing env → points at `aimail init`/`install`), releases
its own resources/patches, and auto-binds on first use.

**5. Verify the whole loop with a welcome mail** (sent through the
gateway to the manager, delivery confirmed):

```bash
aimail welcome
```

That is the end-to-end proof that activation → bridge → plugin/Adapter →
binding all work. Diagnostics if anything is off:

```bash
aimail check     # pipeline diagnostics (gateway → bridge → plugin)
aimail ping      # SMTP ping/pong round-trip
aimail stats     # systems / agents / mail overview
```

Priority for every flag: CLI argument > shell env > `~/.aimail/.env` >
built-in default. See `aimail --help` for all subcommands (`bridge`,
`check`, `domain`, `init`, `install`, `mailname`, `ping`, `reset`,
`stats`, `uninstall`, `welcome`, `persona`, `repair`, `version`).

---

## Architecture

AIMail connects two sides: **aimail-gateway** (the mail service: SMTP
ingress/egress, addressing, the activation/domain/admin APIs) and the
**agent host** (Hermes, dsh, OpenClaw, pi, deer-flow …), wired together by
an HTTP/bridge runtime that each platform SDK implements. The diagram
below shows the Hermes wiring as the concrete example:

```
                     ┌────────────────────┐
                     │   aimail-gateway    │
                     │                    │
   External Mail ───►│ SMTP Receiver      │──── Inbound Webhook ─┐
                     │                    │                      │
                     │ SMTP Relay         │◄─── HTTP API ─────┐  │
   External Mail ◄───│ (external delivery)│                   │  │
                     │                    │                   │  │
                     │ Internal Routing   │                   │  │
                     │ (same-domain stays │◄─── HTTP API ─────┤  │
                     │  off public SMTP)  │                   │  │
                     │                    │                   │  │
                     │ A2A Board Engine   │                   │  │
                     │ Instructions       │                   │  │
                     │ Sessions           │                   │  │
                     │ Notifications      │                   │  │
                     └────────────────────┘                   │  │
                                                              │  │
                     ┌────────────────────┐                   │  │
                     │   Hermes Agent     │                   │  │
                     │                    │                   │  │
                     │ ┌────────────────┐ │                   │  │
                     │ │ aimail RT   │ │─── Outbound ──────┘  │
                     │ │ · Webhook recv │ │                      │
                     │ │ · Preprocessor │ │                      │
                     │ │ · send_mail()  │ │◄─── Inbound ─────────┘
                     │ │ · board_* tools│ │
                     │ │ · Whitelist mgr│ │
                     │ └───────┬────────┘ │
                     │         │          │
                     │ ┌───────┴────────┐ │
                     │ │   LLM Engine   │ │
                     │ │ · email→prompt │ │
                     │ │ · context inj. │ │
                     │ │ · cmd execution│ │
                     │ └────────────────┘ │
                     └────────────────────┘
```

**Inbound flow:** External mail → gateway SMTP Receiver → Webhook → aimail preprocessing (format conversion, context injection, board role recognition) → LLM engine decision

**Outbound flow:** LLM decision → `send_mail()` → HTTP API → gateway internal routing (same-domain recipients via Webhook directly) or SMTP Relay (external recipients)

---

## Configuration

### Email Address Format

- Self-Hosted Gateway, Custom Domain

Deploy your own [aimail-gateway](https://github.com/metercai/aimail-gateway) with a custom domain. Root profile defaults to `agent@{domain}`. Additional profiles created via `hermes -p`.

| Type | Format | Example |
|------|--------|---------|
| Root Profile | `agent@{domain}` | `agent@company.com` |
| Named Profile | `{profile}@{domain}` | `report@company.com` |
| Persona | `{persona}.{profile}@{domain}` | `sales.report@company.com` |

- Official Shared Domain

Use an official activation code with a shared domain. Enter `system_name` (3-8 chars) during activation, such as: `meter`.

| Type | Format | Example |
|------|--------|---------|
| Root Profile | `agent.{system_name}@{domain}` | `agent.meter@amail.token.tm` |
| Named Profile | `{profile}.{system_name}@{domain}` | `report.meter@amail.token.tm` |
| Persona | `{persona}.{profile}.{system_name}@{domain}` | `sales.report.meter@amail.token.tm` |

### API Keys and Profiles

API Keys are generated per Agent address, stored under `~/.aimail/systems/{system_id}/{addr}/agentmail.json`:

### Runtime Directory

```
~/.aimail/
├── systems/
│   └── {system_id}/
│       ├── agentmail_gateway.json     # Gateway connection config
│       ├── board/                     # system-level A2A role prompts (fallback)
│       └── {agent_addr}/              # per-address dir (keyed by cleaned email)
│           ├── agentmail.json         # email + api_key
│           ├── board_creds.json       # A2A board credentials (board_id → gateway_url/token)
│           └── role_prompt/           # address-level role prompts (takes priority)
├── mail/
│   └── {agent_addr}/                  # received mail per address
│       ├── aimail.log              # agent pipeline log
│       └── {yyyymm}/in-*.json         # monthly snapshots
├── bridge/
│   ├── aimail_bridge.toml              # bridge config
│   ├── aimail_routes.toml              # route table (email → local webhook)
│   ├── bin/aimail-bridge               # bridge binary
│   ├── bridge.pid                     # bridge PID
│   └── bridge.out                     # bridge stdout log
├── logs/
│   ├── aimail-bridge.log               # bridge runtime log
│   └── aimail.agent.{addr}.log     # per-agent processing log
├── backup-reset-*/                    # config snapshot before each reset
└── .system_raw_key/
    └── {system_id}_admin.key          # raw admin key (integration only)
```

---

## Further Reading

- [AIMail Integration Guide (对接架构与实例示范)](AGENT-INTEGRATION.md)
- [A2A Board Collaboration Guide](board/A2A-BOARD-GUIDE.md)
- [API Dependencies](API-DEPS.md)
- [Maintenance Guide](MAINTENANCE.md)
