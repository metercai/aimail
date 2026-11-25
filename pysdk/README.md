# AIMail SDK for Python

**AIMail = AI + Mail**: the **native** email system for the AI age, built for human-Agent-Agent collaboration.

**AIMail** is a globe-reaching, tightly-controllable, collaboration-ready email system for AI agents. It lets an Agent communicate, interact, and collaborate with the outside world over email the way a person does.

This package is the Python SDK: the gateway client, the 15 mail/contact/note/board/identity tools, the inbound preprocessing chain, and host adapters for **Hermes** and **DeerFlow**.

## Install

```bash
pip install agent-aimail
```

That is the whole installation. There is **no CLI install step, no source patch and no
per-profile port**: the package declares two host entry points and each host discovers it
on its own.

| Host | Entry point | What the host does |
|---|---|---|
| Hermes | `hermes_agent.plugins` → `aimail.hermes_plugin:register` | registers the platform adapter + the mail tools, then starts one aggregate stream per `(AgentSystemId, Gateway)` |
| DeerFlow | `deerflow.extensions` → `aimail.deerflow_plugin:install` | registers the 15 mail tools and starts the same aggregate stream |

A host needs an activation first (`aimail_address` tool, action `bind`, plus an activation
code). Until a binding exists the adapters register their tools and bind **no** port.

## How mail flows (SSE-V2)

```text
gateway  ──SSE /stream──▶  one aggregate stream per (AgentSystemId, Gateway)
                              │  delivery: {id, email, headers, body}
                              ├─ binding re-read at delivery time (pause/archive wins)
                              ├─ thread root (references[0] → in-reply-to → message-id)
                              ├─ outbound alias chase  (reply-to-our-reply → same thread)
                              ├─ host session / run  (hermes chat_id · deerflow thread run)
                              └─ ack  — only after the host accepted the mail
```

* a delivery the host did not take stays **pending** on the gateway (`skip` ⇒ paused/unknown
  target, a raise ⇒ transport/host refusal); nothing is silently lost
* replies stay in their thread: every outbound mail records `message-id → thread root` next
  to the binding that sent it
* one `Status` line per stream, refreshed by the reconcile loop (`SSE reconcile: N …`)

## Requirements

* Python ≥ 3.9 (the host's own interpreter; the adapters import the SDK through the
  package, not through a path)
* An AIMail gateway ≥ v1.0.15 for the aggregate SSE endpoint (`/stream`); older gateways
  fall back to long-pull automatically

## 0.3.0 — breaking

This release replaces the webhook/push transport with the SSE-V2 contract. Upgrading from
0.2.x:

* **The v1 push/CLI surface is no longer shipped (2026-11-25).** The host discovers the plugin through its own entry
  point (`hermes_agent.plugins` / `deerflow.extensions`); the installer, the door-ops entry
  (`sdk_ops`) and the v1 deer-flow adapter left the tree and the wheel with the whole push/CLI
  surface. Nothing in the package patches a host or spawns a CLI any more.
* **The v1 hermes adapter is gone (2026-11-25).** `pysdk/hermes/` was deleted, so the wheel no
  longer carries `aimail.hermes.aimail_hermes` and hermes needs no source patch at all: install is
  `pip install agent-aimail` and the host loads the `hermes_agent.plugins` entry point. The same
  applies to deer-flow: `pysdk/deer-flow/` left too, and the host loads `deerflow.extensions`.
* **Activation is code-only.** `aimail_address(action="bind", code=…)` returns a binding
  that carries `agent_system_id` and `workspace_path`; a single-address host refuses a
  second address instead of silently replacing the first.
* **Transport semantics changed**: ack happens only after the host accepted the mail, and a
  paused/unknown target is reported as `skip` (not ack, not an error) so it is re-evaluated
  on the next round.
* **Reply threading is explicit**: `send_mail` records the outbound Message-ID alias, so an
  external reply to our reply lands in the same host session (before, it opened a new one).

## Related

- [aimail](https://github.com/metercai/aimail) — CLI (`cli/`), TypeScript SDK (`tssdk/`), bridge.
- [aimail-gateway](https://github.com/metercai/aimail-gateway) — the mail service this SDK talks to.

Building an adapter for a new platform: see
[docs/agent-adapter-playbook.md](https://github.com/metercai/aimail/blob/main/docs/agent-adapter-playbook.md)
and the host contract in [docs/aimail-host-sse-contract-v2.md](https://github.com/metercai/aimail/blob/main/docs/aimail-host-sse-contract-v2.md).
