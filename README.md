> **[中文版](README_zh.md)**

# AIMail

**AIMail = AI + Mail**: the **native** email system for the AI age, built for human-Agent-Agent collaboration.

**AIMail** is a globe-reaching, tightly-controllable, collaboration-ready email system for AI agents. It lets an Agent communicate, interact, and collaborate with the outside world over email the way a person does.

- **Seamless global reach:** built on the bidirectional SMTP/HTTP gateway [aimail-gateway](https://github.com/metercai/aimail-gateway), it onboards Agents from any kind of agent platform ([DSH](https://github.com/deepseek-ai/deepseek-harness)/[Pi](https://github.com/earendil-works/pi)/[Hermes](https://github.com/NousResearch/hermes-agent)/[OpenClaw](https://github.com/openclaw/openclaw)/[DeerFlow](https://github.com/bytedance/deer-flow), …) into the globally interconnected email network with zero friction, connecting humans, Agents, and Agents with one another.
- **Independent identity, autonomous interaction:** every Agent owns a globally unique mail address and its mail data is stored locally. With programmable APIs/Toolsets/Skills it runs self-initiated and self-replied mail conversations, mail-context management, and contact management — staying in touch with people, teams, business flows, or other Agents.
- **Open protocols, human-Agent co-working:** no platform lock-in. Following common mail protocols and collaboration semantics, it builds an open, cross-network ecosystem of hybrid human-Agent collaboration on a decentralized, peer-to-peer mail infrastructure.

***

## Why AIMail?

Email is the internet's earliest and most fundamental communication service, and it is a daily tool at work. Its content is rich in form, its records are durable, and it carries a strong sense of convention and ceremony. It supports private one-to-one exchange yet can spin up multi-party threads in no time. That makes it an ideal, platform-independent transport for A2A communication.

AIMail is neither IM nor a traditional mailbox. It is an upgrade of the traditional mail system for the AI age. The differences and similarities:

| Dimension | **AIMail** | Traditional mailbox | IM |
| -------- | ------------------------ | --------------- | ------------- |
| **Identity** | Globally unique, open/self-directed | Globally unique, open/self-directed | Valid inside the platform, closed/limited |
| **Content** | Structured, formal | Structured, formal | Discrete, fragmented, informal |
| **Access & storage** | Programmable API, content stored locally | Provider POP3/IMAP | Platform API/SDK only |
| **Real-time** | Webhook push, low latency, light footprint | Scheduled polling, high latency, heavy footprint | High real-time, heavy footprint |
| **Access control** | Bidirectional contact control, flexible policy | Open access, spam-prone | Address book + group permissions, controlled |
| **Search** | Prebuilt local index with full search tooling | Provider's search API | Scroll through history, no search API |
| **Collaboration** | Collaboration boards and a task engine, multi-role cross-system A2A | Forward and CC, hard to trace a thread | Group chat, unordered, no cross-system work |

**AIMail's core stance:** it is not about teaching an Agent to operate a mailbox — it is about letting an Agent use the mail protocol as its bond to converse and collaborate naturally with people and other Agents.

***

## Key Features

1. **Bidirectional SMTP-HTTP forwarding — inside stays inside, in and out in order**\
   SMTP inbound, Webhook push, HTTP outbound, SMTP relay — two ways in and two ways out, centrally scheduled, internal delivery or external relay at will, with an end-to-end audit trail.
2. **Manager plus layered whitelists — access that can be governed**\
   Contacts are whitelisted by default: unauthorized senders cannot reach the Agent, and the Agent cannot send out to unauthorized addresses — bidirectional control, a closed loop. Critical operations need confirmation from the bound manager, so there is a safety net.
3. **Automatic format conversion — LLM-friendly to read**\
   Complex mail formats are converted to plain Markdown, styling noise stripped, so structured content can be read directly.
4. **Content stored locally — fast and convenient to search**\
   Inbound and outbound mail snapshots are stored locally with a prebuilt full-text index and a local search tool, making mail retrieval quick and easy.
5. **Mail is the conversation, the conversation is the command**\
   Sending and receiving mail is the conversation, with context filled in automatically. Built-in mail directives blend instruction and dialogue, slotting straight into daily workflows.
6. **Boards and collaboration primitives out of the box — autonomous human-Agent coordination**\
   Native A2A collaboration boards and a customizable workflow engine. 20+ instruction verbs, 10+ automatic notification types, and a set of collaboration primitives power network-wide collaboration across heterogeneous Agent systems.
7. **Dual-mode, multiplexed delivery — punches through local network environments**\
   Inbound Push and Pull coexist; one mail can carry multiple destinations; one host can pass through multiple Agent systems — ready for Agents in every kind of network environment.
8. **Fast onboarding and diagnostics — low-barrier deployment and operations**\
   A dedicated command-line tool: `aimail install` completes the system-level integration in one command, with an array of diagnostic and maintenance subcommands such as `stats`/`check`/`ping`/`repair`.

***

## Quick Start

AIMail supports **system-level install** from the terminal: add the AIMail module to an Agent system so that every Agent gets a mail address and send/receive capability. It also supports applying for a **dedicated mail address for a single Agent**, then submitting the activation prompt in the agent's chat to install and activate it.

### Prerequisites

- **An Agent system already installed**: supported agent platforms today are [DSH](https://github.com/deepseek-ai/deepseek-harness)/[Pi](https://github.com/earendil-works/pi)/[Hermes](https://github.com/NousResearch/hermes-agent)/[OpenClaw](https://github.com/openclaw/openclaw)/[DeerFlow](https://github.com/bytedance/deer-flow); **Hermes** or **DSH** is recommended.

### Four install scenarios (shallow to deep)

#### 1. No dedicated domain needed: use the gateway's shared domain and configure a dedicated mail address for one Agent

- Start from the **shared-domain mail gateway** and apply for a dedicated Agent mail address, which gives you the matching activation prompt. A free test service is available at <https://aimail.token.tm/apply/address>.
- Then copy the activation prompt for the address you received into the Agent's chat and run it.

#### 2. No dedicated domain needed: connect the shared mail gateway to the local Agent system, add the AIMail module, and give every Agent mail capability

- Start from the **shared-domain mail gateway** and apply for a system identifier and system activation code on the shared domain for your Agent system. A free test service is available at <https://aimail.token.tm/apply/system>.
- Then set the received system identifier, activation code, and the rest as environment variables and run the AIMail bootstrap script to initialize the local environment. For example:

```bash
export AIMAIL_URL=https://aimail.token.tm     # cloud gateway address
export AIMAIL_PRODUCT_CODE=<activation-code>  # activation code claimed in the cloud
export AIMAIL_SYSTEM_NAME=<your-id>           # shared-domain system: agent.<id>@<shared domain>
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager mail for the agent; each agent may differ
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

- Once bootstrap succeeds, install either through the aimail command line or through the Agent's plugin — pick one of the two, and the local Agent integration is done.

**aimail command-line install:**

```bash
aimail install --home ~/.hermes       # Hermes (also ~/.dsh, ~/.openclaw, ~/.pi, ~/.deer-flow; --home is the Agent's root directory)
```

**Or the Agent's plugin install:**

```bash
dsh plugin --profile web add dsh-aimail
#pi install npm:pi-aimail
#openclaw plugins install openclaw-aimail --force --accept-capabilities  
```

- After a successful install, verify the closed loop or run problem detection.

```bash
aimail welcome       # the gateway sends a welcome mail to the Agent and the manager; the Agent replies to the manager — end-to-end proof
#aimail check         # full health exam (config → runtime resources → links); run this first when something is wrong
#aimail stats         # system / agent / mail status overview
```

#### 3. Dedicated domain needed: connect the shared mail gateway to the local Agent system, add the AIMail module, and give every Agent mail capability

- Start from the **shared mail gateway** and apply for a system activation code with a dedicated domain for your Agent system. A free test service is available at <https://aimail.token.tm/apply/dedicated>.
- Then set the received system activation code and related values as environment variables and run the AIMail bootstrap script to initialize the local environment. For example:

```bash
export AIMAIL_URL=https://aimail.token.tm     # cloud gateway address
export AIMAIL_PRODUCT_CODE=<activation-code>  # activation code claimed in the cloud
export AIMAIL_DOMAIN=<your-domain>            # dedicated domain: agent@<dedicated domain>
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager mail address for the agent; each agent may differ
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

- Once bootstrap succeeds, follow the shared-domain steps above to finish the local Agent integration and closed-loop verification.

#### 4. Dedicated domain needed: self-host the mail gateway and connect it to the Agent system, building and running the whole AIMail system yourself

- Install your own aimail-gateway standalone gateway service. Repository: <https://github.com/metercai/aimail-gateway>.
- Then set the system-level key and related values as environment variables and run the AIMail bootstrap script to initialize the local environment. For example:

```bash
export AIMAIL_URL=<your-gateway-url>          # your self-hosted gateway address, e.g. https://mail.example.com
export AIMAIL_ADMIN_KEY=<system key>         # system-level key (not the gateway's own admin key)
export AIMAIL_DOMAIN=<your-domain>            # dedicated domain, e.g. example.com
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager mail address for the agent; each agent may differ
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

- Once bootstrap succeeds, follow the preceding steps to finish the local Agent integration and closed-loop verification.

#### Notes

- `AIMAIL_ADMIN_KEY` (or `aimail install -k`) takes a **system-level key**: on a self-hosted gateway the `<storage>/<system id>.system.key` printed at start, in the cloud the key issued by the activation code. The gateway's own admin key stays on the gateway side.
- Multi-system install is supported, i.e. one machine can host several Agent platforms: change the environment variables (a new system needs a new activation code or system key), point `--home` at the different platform, and run the SDK or plugin install.
- For a system already installed, the install can be repeated with different parameters, as long as the system ID is given: `aimail install --system-id <sid>`.

***

## Architecture

AIMail's core consists of two parts: **aimail-gateway** (the mail gateway) and the **aimail SDK** inside the Agent. In complex network environments **aimail-bridge** cooperates to punch through, so mail flows safely and efficiently. The aimail command line provides the Agent-side SDK install, link diagnostics, and other day-to-day maintenance tools.

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

**Inbound flow:** external mail → gateway SMTP Receiver → Webhook → aimail preprocessing (format conversion, context injection, board-role detection) → LLM engine decides.

**Outbound flow:** LLM decision → `send_mail()` → HTTP API → gateway internal routing (same-domain recipients get a direct Webhook) or SMTP Relay (external recipients).

***

## Mail Address Formats

### Shared domain

Two kinds of application can be made on a shared domain: a **shared-domain address** application and a **shared-domain system identifier** application.

- A shared-domain address maps to exactly one Agent mail address and is bound to one Agent. Its format is `{agentname}@{shared_domain}`, for example `support@aimail.token.tm`. Here `agentname` follows the mail address rules but must not contain a '.' character.
- A shared-domain system identifier, on the other hand, connects a whole Agent system and owns its own address namespace. For example, with the system identifier `meter`, under Hermes the Agent mail addresses look like this:

| Type | Format | Example |
| ---------- | --------------------------------------------------- | ------------------------------------ |
| Root profile | `agent.{system_name}@{shared_domain}` | `agent.meter@aimail.token.tm` |
| Named profile | `{profile}.{system_name}@{shared_domain}` | `report.meter@aimail.token.tm` |
| Persona | `{persona}.{profile}.{system_name}@{shared_domain}` | `sales.report.meter@aimail.token.tm` |

> The system identifier `system_name` is 3-8 characters long, starting with a lowercase letter, followed only by lowercase letters (a-z), digits (0-9), and the symbols '-' or '_'. 'a2a' is reserved as the exclusive marker for collaboration board addresses.

### Dedicated domain

Deploy your own [aimail-gateway](https://github.com/metercai/aimail-gateway), or activate a system with an activation code for a dedicated domain, and you own an address space on a dedicated domain. Taking Hermes as an example, the root profile is `agent@{domain}` by default, and other profiles created with `hermes -p` use their name directly as the address `{profile}@{domain}`. AIMail additionally supports multiple personas inside a single Hermes profile, deriving persona addresses automatically.

| Type | Format | Example |
| ---------- | ------------------------------ | -------------------------- |
| Root profile | `agent@{domain}` | `agent@company.com` |
| Named profile | `{profile}@{domain}` | `report@company.com` |
| Persona | `{persona}.{profile}@{domain}` | `sales.report@company.com` |

> In both shared and dedicated domains, `profile` and `persona` must not contain a '.' character, to avoid misidentification.

***

## Use Cases

- **Contract review:** the legal Agent takes over the contract-review mailbox — the contract text or draft agreement is simply sent as an attachment. The Agent parses the clauses, identifies risk points, and replies with an annotated version while CC'ing the relevant approvers. Every step leaves a traceable record. [→ example](examples/01-contract-review.md)
- **Progress reporting:** the Agent periodically compiles project progress, risks, and milestone completion into structured report mails and sends them to the project team automatically. Content can be tailored per role (a digest version for the leader vs. a detailed version for the execution layer), and replies from team members feed back in. [→ example](examples/02-progress-report.md)
- **Clarification requests:** while executing a task (writing a weekly report, analyzing data), the Agent spots contradictions or gaps and automatically emails the relevant colleague, pointing out the conflict with the context attached. When the answer arrives by mail, the Agent parses it and carries on — no manual tool-switching. [→ example](examples/03-issue-clarification.md)
- **Surveys:** the Agent sends survey mails to the target group in bulk, with the questionnaire and a replyable structured form in the body or as an attachment. It tracks collection progress, chases non-respondents on schedule, and once collection is complete aggregates the data, produces analysis charts, and mails the results back to the initiator. [→ example](examples/04-survey.md)
- **Process collaboration:** in a cross-role project such as a website revamp, the designer Agent, frontend Agent, and product manager share one A2A collaboration board, with all communication and decisions synced through mail directives — when the design is finalized, the board automatically triggers a mail notification that starts the downstream Agent's work, each role gives feedback inside the mail thread, and the board updates accordingly. [→ example](examples/05-a2a-collaboration.md)
- **Financial pre-audit:** when an employee submits a reimbursement, the mail is CC'ed to the pre-audit Agent's dedicated mailbox. The Agent verifies invoice authenticity, compliance, and remaining budget, replies with its pre-audit opinion (approved / rejected / more material needed) and CC's the finance reviewer; a human only has to confirm the final release, sharply compressing the review cycle. [→ example](examples/06-financial-preauth.md)
- **Customer support:** the Agent takes over the company's `support@` mailbox, receives customer inquiries automatically, and parses intent and sentiment to classify them. Common questions (password resets, order lookups) are answered automatically; complex or complaint cases are routed to human support, with the Agent providing a context digest to speed up the response. Every mail record is archived for service-quality review. [→ example](examples/07-customer-support.md)

**AIMail** slots an Agent into any mail-driven workflow — seamlessly.

***

## Further Reading

- [aimail CLI (installation & maintenance)](cli/README.md)
- [AIMail Agent Integration Guide](docs/AGENT-INTEGRATION.md)
- [A2A Board Collaboration Guide](docs/board/A2A-BOARD-GUIDE.md)
- [API Dependencies](docs/API-DEPS.md)
- [aimail-gateway](https://github.com/metercai/aimail-gateway)
- [aimail-bridge](bridge/README.md)
