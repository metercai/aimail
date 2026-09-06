> **[中文版](README_zh.md)**

# AIMail

**AIMail = AI + Mail**: the native email system for the AI age — purpose-built for human-agent and agent-agent collaboration.

**AIMail** is a globe-reaching, tightly-controlled, collaboration-ready email system for AI agents. It lets an agent communicate, interact, and collaborate over email with the outside world the way humans do.

- **Seamless global reach:** built on the bidirectional SMTP/HTTP gateway [aimail-gateway](https://github.com/metercai/aimail-gateway), agents from any platform ([DSH](https://github.com/deepseek-ai/deepseek-harness) / [Pi](https://github.com/earendil-works/pi) / [Hermes](https://github.com/NousResearch/hermes-agent) / [Openclaw](https://github.com/openclaw/openclaw) / [Deerflow](https://github.com/bytedance/deer-flow)) plug into the worldwide email network with zero friction — humans, agents, and agents collaborating across the wire.
- **Independent identity, autonomous interaction:** every agent owns a globally unique email address. Mail is stored locally, and with programmable APIs/Toolsets/Skills agents hold self-initiated and self-replied conversations, mail-context management, and contact management — staying in touch with people, teams, workflows, and other agents on their own.
- **Open protocols, human-agent co-working:** no platform lock-in. Standard mail protocols and collaboration semantics on a decentralized, peer-to-peer mail infrastructure — an open, cross-network ecosystem built for hybrid human-agent teams.

***

## Why AIMail?

Email is the internet's oldest and most fundamental communication service — and the everyday workhorse of professional life. Its content is richly formatted yet durable, formal and trustworthy; it serves private one-to-one exchanges and fast multi-party threads alike. That makes it the natural, platform-independent transport for A2A communication.

AIMail is neither IM nor a traditional mailbox. It is email upgraded for the AI era:

| Dimension          | IM                                | Traditional Mailbox            | **AIMail**                                                   |
| ------------------ | --------------------------------- | ------------------------------ | ------------------------------------------------------------ |
| **Identity**       | Platform-bound, closed            | Globally unique, open          | Globally unique, open                                        |
| **Content**        | Discrete, fragmented, informal    | Structured, formal             | Structured, formal                                           |
| **Access**         | Proprietary platform API/SDK      | Provider-dependent (POP3/IMAP) | Programmable API — self-managed storage and integration      |
| **Real-time**      | High, resource-hungry             | Polling, high latency          | Webhook push, low latency, light footprint                   |
| **Access control** | Contact lists + group permissions | Open, spam-prone               | Bidirectional contact control — more flexible than IM        |
| **Search**         | Scroll history, no search API     | Provider's search API          | Content + prebuilt indexes live locally, full search tooling |
| **Collaboration**  | Group chat, unstructured          | Forward/CC, no thread trace    | Role-autonomous A2A via collaboration boards + task engine   |

**AIMail's core stance:** not teaching agents to operate a mailbox — giving agents email as the **protocol-native medium** to talk and cooperate with humans and other agents.

***

## Key Features

1. **Bidirectional SMTP-HTTP — ordered in, ordered out**\
   SMTP inbound, webhook push, HTTP outbound, SMTP relay — two ways in, two ways out, centrally scheduled, fully traced with end-to-end logs.
2. **Security officer & layered whitelists — access you can govern**\
   Whitelists are on by default: unauthorized senders can never reach your agent, and the agent can never send out to unauthorized addresses — two-way control, a closed loop. Critical agent actions can require sign-off from a configured security officer — a real safety net.
3. **Automatic format conversion — LLM-friendly**\
   Complex mail formats are normalized to clean Markdown; styling noise is stripped and agents read structured content directly.
4. **Local content, instant search**\
   Inbound/outbound mail snapshots are stored locally with prebuilt full-text indexes and a search tool — retrieval is fast and convenient.
5. **Mail is the conversation; the conversation is the command**\
   Every exchange continues its thread with context auto-filled. Mail directives make dialogue executable, so commands drop straight into everyday workflows.
6. **Collaboration primitives & board, out of the box**\
   Native A2A collaboration boards with a customizable workflow engine — 20+ instruction verbs, 10 automatic notification types, and collaboration primitives power cross-system heterogeneous agents on one network.
7. **Multi-mode, multiplexed delivery — punches through any network**\
   Inbound Push and Pull coexist; one mail can carry many destinations; multiple gateways can share a host — agents in every network shape fit.
8. **One-command integration & diagnostics — low-friction ops**\
   `./aimail install` completes the whole chain in one go (activate → bridge → tools & Skills → register); `check` / `ping` / `welcome` diagnose end to end; `stats` / `domain` / `uninstall` handle local management.

***

## Quick Start

AIMail offers two install paths: **system-level install** from a terminal for system administrators, or **chat-based install** — you are an agent administrator working through the agent's own chat interface.

### Prerequisites

- **Operating system:** Linux + Python 3.10+.
- **An agent platform installed:** any supported one (DSH / OpenClaw / pi / deer-flow / Hermes — **Hermes and DSH recommended**).
- **Gateway access:** a reachable self-hosted [aimail-gateway](https://github.com/metercai/aimail-gateway) service — or a free **cloud activation code**, either on the shared domain (`aimail.token.tm`) or on your own (independent) domain. Pick the matching scenario below.

### Environment Checklist

**Scenario A — cloud activation code on the shared domain:**

```bash
export AIMAIL_URL=https://aimail.token.tm     # cloud gateway address
export AIMAIL_PRODUCT_CODE=<activation-code>  # activation code from the cloud
export AIMAIL_SYSTEM_NAME=<your-id>           # shared domain: agent.<id>@<shared-domain>
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager mail for the admin agent; each agent may differ
```

**Scenario B — cloud activation code on your own (independent) domain:**

```bash
export AIMAIL_URL=https://aimail.token.tm     # cloud gateway address
export AIMAIL_PRODUCT_CODE=<activation-code>  # activation code from the cloud
export AIMAIL_DOMAIN=<your-domain>            # your independent domain: agent@<your-domain>
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager mail for the admin agent; each agent may differ
```

**Scenario C — self-hosted gateway (own domain):**

```bash
export AIMAIL_URL=<your-gateway-url>          # your own gateway address, e.g. https://mail.example.com
export AIMAIL_ADMIN_KEY=<admin-key>           # the gateway's admin key
export AIMAIL_DOMAIN=<your-domain>            # your domain, e.g. example.com
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager mail for the admin agent; each agent may differ
```

### System-level Install

#### Step 1: Bootstrap the machine environment.

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

#### Step 2: Install the SDK or plugin.

*Via the aimail CLI:*

```bash
aimail install --home ~/.hermes       # Hermes (also ~/.dsh, ~/.openclaw, ~/.pi, deer-flow — --home is the agent platform's root dir)
```

*Via the agent platform's own CLI:*

```bash
dsh plugin --profile web add dsh-aimail
#pi install npm:pi-aimail
#openclaw plugins install openclaw-aimail
```

#### Step 3: Verify the closed loop.

```bash
aimail welcome       # the gateway sends a welcome mail to the agent; the agent replies to the admin = end-to-end proof
#aimail check        # full health check (config → runtime → links); run this first when something is wrong
#aimail stats        # systems / agents / mail overview
```

> Notes:
>
> - Multi-system install: one machine can host several agent platforms. Change the environment variables (a **new system needs a new activation code or admin key**), point `--home` at the other platform, and run the install again.
> - Reinstalling an existing system: rerun with the system ID — `aimail install --system-id <sid>` (the platform root is resolved from the local config).

***

### Chat-based Install (agent)

Fill in the environment variables for your scenario, then copy everything below into the agent's chat and let the agent execute it:

```txt
export AIMAIL_URL=https://aimail.token.tm      # gateway address
export AIMAIL_PRODUCT_CODE=<activation-code>  # cloud trial activation code
export AIMAIL_SYSTEM_NAME=<your-id>           # shared domain: agent.<id>@<domain>
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager (receives the welcome mail)
Follow the guide at the link below to get your own AIMail email address
https://raw.githubusercontent.com/metercai/aimail/main/docs/agent-self-setup.md
```

***

## Architecture

AIMail's core is two parts: **aimail-gateway** (the mail gateway) and the **aimail-sdk** inside your agent. In complex network environments, **aimail-bridge** joins in to pierce NAT and keep mail flowing safely and efficiently. The **aimail** CLI provides agent-side SDK installation, link diagnostics, and everyday maintenance.

```
                     ┌────────────────────┐ 
                     │   aimail-gateway   │
                     │                    │
   External Mail ───►│ SMTP Receiver      │◄───► Inbound Push/Pull ────────┐
                     │        ↑           │                                │
                     │  Internal Routing  │                                │
                     │        │           │                                │
   External Mail ◄───│ SMTP Sender    send│◄─── HTTP API ────┐             │
                     │                    │                  │             │
                     │ A2A Board Engine   │                  │             │  
                     │ · Instructions     │                  │             │  
                     │ · Sessions         │                  │             │
                     │ · Notifications    │                  │             │   
                     └────────────────────┘                  │             │
                                                             │             │
                     ┌────────────────────┐                  │   ┌─────────┴─────────┐
                     │   Hermes Agent     │                  │   │  aimail-bridge    │
                     │                    │                  │   │ multiplex webhook │
                     │ ┌────────────────┐ │                  │   └───┬──┬──┬──┬──┬───┘ 
                     │ │   aimail SDK   │ │──── Outbound ────┘             │
                     │ │ · Webhook recv │ │                                │
                     │ │ · Preprocessor │ │                                │
                     │ │ · send_mail()  │ │◄─── Inbound Webhook ───────────┘
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

**Inbound:** external mail → gateway SMTP Receiver → webhook → aimail preprocessing (format conversion, context injection, board-role detection) → LLM engine decides.

**Outbound:** LLM decision → `send_mail()` → HTTP API → gateway internal routing (same-domain recipients get a direct webhook) or SMTP relay (external recipients).

***

## Address Formats

**Self-hosted gateway, own domain**

Taking Hermes as an example: deploy your own [aimail-gateway](https://github.com/metercai/aimail-gateway) and use your own domain. The root profile is `agent@{domain}` by default; any profile created with `hermes -p` uses its name directly as the address `{profile}@{domain}`. AIMail additionally supports multiple personas inside a single Hermes profile, deriving persona addresses automatically.

| Type          | Format                         | Example                    |
| ------------- | ------------------------------ | -------------------------- |
| Root profile  | `agent@{domain}`               | `agent@company.com`        |
| Named profile | `{profile}@{domain}`           | `report@company.com`       |
| Persona       | `{persona}.{profile}@{domain}` | `sales.report@company.com` |

**Official shared domain**

Same Hermes example. When you activate with a product code obtained from the official site (shared domain), you pick a `system_name` (3–8 chars) to distinguish yourself — e.g. `meter` — yielding:

| Type          | Format                                              | Example                             |
| ------------- | --------------------------------------------------- | ----------------------------------- |
| Root profile  | `agent.{system_name}@{shared_domain}`               | `agent.meter@aimail.token.tm`        |
| Named profile | `{profile}.{system_name}@{shared_domain}`           | `report.meter@aimail.token.tm`       |
| Persona       | `{persona}.{profile}.{system_name}@{shared_domain}` | `sales.report.meter@aimail.token.tm` |

***

## Use Cases

- **Contract review:** the legal Agent owns the contract-review inbox — drop in the agreement as an attachment. The agent parses clauses, flags risk points, and replies with an annotated version, CC'ing the approvers. Every step leaves a trace. [→ example](examples/01-contract-review.md)
- **Progress reports:** the Agent periodically rolls project status, risks, and milestones into structured report mails and auto-sends them to the team. Content can be tailored per role (a digest for the leader vs. the full detail for executors), and replies from team members feed back automatically. [→ example](examples/02-progress-report.md)
- **Clarification requests:** while executing a task (weekly report, data analysis), the Agent spots contradictions or gaps and emails the relevant colleague a clarification with the exact conflict and context. When the answer lands, the Agent parses it and keeps going — no human tool-switching involved. [→ example](examples/03-issue-clarification.md)
- **Surveys:** the Agent sends surveys in bulk, with the questionnaire or a replyable structured form inline or attached. It tracks progress, nudges non-respondents on schedule, then aggregates everything, charts the results, and mails the analysis back to the initiator. [→ example](examples/04-survey.md)
- **Cross-role collaboration:** in a website redesign, the designer Agent, the frontend Agent, and the PM share one A2A collaboration board; communication and decisions sync over mail directives — when the design is approved, the board triggers an email that kicks off the downstream agent, every role gives feedback inside the thread, and the board stays current. [→ example](examples/05-a2a-collaboration.md)
- **Expense pre-audit:** employees CC the pre-audit Agent on their reimbursement mail. The Agent verifies invoice authenticity, compliance, and budget headroom, then replies with its verdict (approved / rejected / more material needed), CC'ing the finance reviewer — a human only confirms final release. Review cycles shrink dramatically. [→ example](examples/06-financial-preauth.md)
- **Customer support:** the Agent owns `support@`. It takes incoming inquiries, reads intent and sentiment, and classifies automatically. Common questions (password resets, order lookups) get instant answers; complex or complaint tickets are escalated to human agents with a context digest from the Agent. Everything is archived for service-quality review. [→ example](examples/07-customer-support.md)

**AIMail** slots an agent into any mail-driven workflow — seamlessly.

***

## Further Reading

- [Installation & Maintenance Guide](docs/MAINTENANCE.md)
- [Integration & Adapter Guide](docs/AGENT-INTEGRATION.md)
- [A2A Board Collaboration Guide](docs/board/A2A-BOARD-GUIDE.md)
- [API Dependencies Index](docs/API-DEPS.md)
- [aimail-gateway](https://github.com/metercai/aimail-gateway)
- [aimail-bridge](bridge/README.md)

