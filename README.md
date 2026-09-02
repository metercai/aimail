> **[中文版](README_zh.md)**

# AgentMail

**A dedicated email system for AI agents.**

**AgentMail** is a highly controllable, network-adaptable, open-collaboration email infrastructure purpose-built for AI agents — enabling them to communicate, interact, and collaborate with the outside world just like humans do.

- **Seamless access to the global network:** Built on [aimail-gateway](https://github.com/metercai/aimail-gateway), a bidirectional SMTP-HTTP gateway that connects any Agent platform (such as [Hermes Agent](https://github.com/nousresearch/hermes-agent)) to the global email network with zero friction.
- **Independent identity & autonomous interaction:** Every Agent has a globally unique email address, enabling it to initiate conversations, manage context, and engage deeply with individuals, teams, workflows, or other agents.
- **Open protocols & human-agent co-working:** Free from platform lock-in. Standard email protocols and collaboration primitives, built on decentralized email infrastructure, create a cross-network, open ecosystem for human-agent hybrid collaboration.

---

## Why AgentMail?

Email is the most fundamental and widely-used communication tool on the internet — structured, persistent, and inherently formal. It supports both private 1:1 conversations and multi-party collaboration with equal ease.

AgentMail is neither IM nor a traditional mailbox. The key differences:

| Dimension | IM | Traditional Mailbox | **AgentMail** |
|-----------|-----|---------------------|---------------|
| **Identity** | Platform-bound, closed | Globally unique, open | Globally unique, open |
| **Content** | Fragmented, informal | Structured, formal | Structured, formal |
| **Access** | Proprietary API/SDK | POP3/IMAP, provider-dependent | SMTP + Webhook, self-hosted |
| **Latency** | Real-time, resource-heavy | Polling, high latency | Webhook push, near real-time |
| **Access Control** | Contact list, group permissions | Open, spam-prone | Default whitelist, bidirectional control |
| **Multi-party** | Group chat, unstructured | Forward/CC, threaded | Same as email + A2A Board, multi-role autonomous collaboration |

AgentMail is not about teaching agents to use email. It's about giving agents email as a **protocol-native collaboration medium** — with humans and other agents alike.

---

## Use Cases

- **Contract Review:** Legal Agent takes over the contract inbox. Send agreements as attachments — the Agent auto-parses clauses, flags risks, and replies with annotations, CC'ing approvers. Full audit trail preserved. [→ example](examples/01-contract-review.md)
- **Progress Reports:** Agent periodically summarizes project status, risks, and milestones into structured reports, auto-sending to project members. Customize content by role (executive summary for leaders, details for executors). [→ example](examples/02-progress-report.md)
- **Clarification Requests:** When Agent encounters contradictions or gaps during analysis, it automatically emails the relevant colleague with context. Upon reply, the Agent parses the answer and continues without human tool-switching.
- **Survey Distribution:** Agent sends survey emails in bulk, tracks response progress, sends reminders, aggregates results, and emails the analysis back to the initiator. [→ example](examples/04-survey.md)
- **Process Collaboration:** In a website redesign involving designer Agent, frontend Agent, and PM, the A2A Board syncs all communication and decisions via email. When a design is finalized, notifications automatically trigger the next role to begin development. [→ example](examples/05-a2a-collaboration.md)
- **Financial Pre-audit:** Employee CCs the pre-audit Agent on expense reports. Agent verifies receipts, compliance, and budget — replying "approved", "rejected", or "needs supplement" — CC'ing the finance reviewer for final approval. [→ example](examples/06-financial-preauth.md)
- **Customer Support:** Agent takes over `support@` inbox. Auto-classifies intent and sentiment. Answers FAQs (password reset, order lookup) automatically. Escalates complex cases to human agents with context summaries. [→ example](examples/07-customer-support.md)

AgentMail seamlessly integrates AI agents into any email-based workflow — contract review, progress reporting, clarification loops, surveys, cross-role collaboration, financial pre-audit, customer support, and beyond.

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

8. **One-Click Integration & Diagnostics via `agentmail` CLI**  
`./agentmail install` sets up the whole chain (activation → bridge → tools/skills → registration); `check`/`ping`/`welcome` diagnose the full loop; `stats`/`domain`/`uninstall` manage the machine from one entry.

---

## Quick Start

### Prerequisites

- [aimail-gateway](https://github.com/metercai/aimail-gateway) (running)
- [Hermes Agent](https://github.com/nousresearch/hermes-agent) (installed)
- Linux + Python 3.10+

### One-Command Integration

```bash
git clone https://github.com/metercai/aimail.git
cd agentmail
cp docs/.env.example .env        # fill in AIMAIL_URL, AIMAIL_PRODUCT_CODE (new) or
                            # AIMAIL_ADMIN_KEY (existing), AIMAIL_MANAGER_ADDRESS;
                            # optionally AIMAIL_DOMAIN / AIMAIL_SYSTEM_NAME
./agentmail install --home ~/.hermes
```

`install` runs the whole chain **non-interactively**: system activation (or
reuse of an existing system), bridge deploy, tool & skill install, webhook
patch & profile registration. Every value is read from `.env`, so the only
flag you usually pass is `--home`. Domains named in `AIMAIL_DOMAIN` are
preset at activation or actively created when missing.

### Verify the Chain

```bash
./agentmail check      # 4-layer pipeline diagnostics (gateway → bridge → webhook → profile)
./agentmail ping       # end-to-end ping/pong round-trip through SMTP
./agentmail welcome    # sends a welcome email to the manager and verifies delivery
./agentmail stats      # machine overview: systems, agents, mail statistics
```

Example output:

```
$ ./agentmail stats
  Systems installed:
      shared-token-40b34a66   [hermes]    agents: 1
      shared-token-9479c607   [openclaw]  agents: 1
  Agents (2):
      agent.weiwei@amail.token.tm   [hermes]
          received: 12 emails · storage: 1.2 MB · manager: 925457@qq.com
      agent.xianlin@amail.token.tm   [openclaw]
          received: 8 emails · storage: 0.9 MB · manager: 925457@qq.com
```

Priority for every flag: CLI argument > shell env > `.env` > built-in default.
See `./agentmail --help` for all subcommands (`bridge`, `check`, `domain`,
`install`, `mailname`, `ping`, `reset`, `stats`, `uninstall`, `welcome`).

---

## Architecture

AgentMail consists of two core components: **aimail-gateway** (mail gateway) and **Hermes Agent** (LLM engine), working together via Webhook and HTTP API at runtime.

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
                     │ │ agentmail RT   │ │─── Outbound ──────┘  │
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

**Inbound flow:** External mail → gateway SMTP Receiver → Webhook → agentmail preprocessing (format conversion, context injection, board role recognition) → LLM engine decision

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

API Keys are generated per Agent address, stored under `~/.agentmail/systems/{system_id}/{addr}/agentmail.json`:

### Runtime Directory

```
~/.agentmail/
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
│       ├── agentmail.log              # agent pipeline log
│       └── {yyyymm}/in-*.json         # monthly snapshots
├── bridge/
│   ├── aimail_bridge.toml              # bridge config
│   ├── aimail_routes.toml              # route table (email → local webhook)
│   ├── bin/aimail-bridge               # bridge binary
│   ├── bridge.pid                     # bridge PID
│   └── bridge.out                     # bridge stdout log
├── logs/
│   ├── aimail-bridge.log               # bridge runtime log
│   └── agentmail.agent.{addr}.log     # per-agent processing log
├── backup-reset-*/                    # config snapshot before each reset
└── .system_raw_key/
    └── {system_id}_admin.key          # raw admin key (integration only)
```

---

## Further Reading

- [AgentMail Integration Guide (对接架构与实例示范)](AGENT-INTEGRATION.md)
- [A2A Board Collaboration Guide](board/A2A-BOARD-GUIDE.md)
- [API Dependencies](API-DEPS.md)
- [Maintenance Guide](MAINTENANCE.md)
