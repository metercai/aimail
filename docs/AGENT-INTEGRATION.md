# AIMail Integration Guide — Architecture & Example Deployments

> Status: Revised (2026-09-06)
> Purpose: the first-reference document for any future agent system integrating with AIMail.
> Presentation convention: each topic is developed as goal → method → mechanism → result; it describes only the current state, never the history.
> Authoritative code: `pysdk/` (shared core + platform adapters + MCP server), `cli/` (CLI and scripts), `pysdk/resources/skills/` (SKILL sources), `cli/bin/` (runtime registration tools).

---

## 1. Overall Architecture

### 1.1 Goal

AIMail integrates with any agent system (LLM runtime); the agent gains complete email capability:

| Capability | Delivered as |
|------------|--------------|
| Inbound | Mail is reachable end-to-end through the gateway → bridge → agent receive endpoint: signature verification → shared preprocessing → delivery to the agent |
| Outbound | Agent replies via the `send_mail` tool; the server enforces sender == key.email identity isolation |
| Identity | 1 agent = 1 AIMail address; each agent has its own `api_key`; single source of truth for configuration |
| Tools | 7 email tools (incl. local full-text search via `search_mail`) + board tools fully exposed (in-process registry / platform plugin / shared MCP server) |
| Lifecycle | Agent create/delete auto-registers/deregisters; full supplementary registration at install time |
| Acceptance | Both `aimail ping` (three-stage log loop) and `aimail welcome` (incl. LLM round-trip) pass |

### 1.2 Topology

```
                        Cloud aimail-gateway
   ┌─────────────────────────────────────────────────────────────┐
   │ SMTP receive → sanitize/enrich → inbound queue (pending)    │
   │ HTTP API (send/contacts/...) / A2A Board                    │
   └──────────┬──────────────────────────────────────▲───────────┘
              │ pending polling                      │ HTTP API
   ┌──────────▼─────────────────┐   ┌────────────────┴───────────┐
   │ aimail-bridge (local)      │──►│ agent receive endpoint     │
   │ single process, pulls for  │   │ (local): signature verify  │
   │ multiple systems, forwards │   │ → shared preprocessing →   │
   │ every route-table URL      │   │ deliver to agent           │
   └────────────────────────────┘   └────────────────▲───────────┘
                                                     │ send_mail outbound
                                                     ▼
                                    Cloud HTTP API → SMTP delivery
```

### 1.3 Layered Responsibilities

| Layer | Location | Responsibility |
|-------|----------|----------------|
| Shared core | `pysdk/aimail_base.py`, `aimail_tools.py`, `aimail_board.py`, `aimail_mcp_server.py` | Inbound preprocessing chain, ping/pong, address derivation, registration/deregistration chain, email-tool implementations, board tools |
| Platform adapters | `pysdk/{platform}/` (hermes/openclaw/deer-flow) + platform-side TS plugins (dsh/pi/openclaw, see §4.4/§4.5/§4.2) | Config source, persona switch, identity injection, tool registration, receive endpoint |
| Runtime | `cli/bin/register_agent.py`, `cli/bin/deregister_agent.py` | Agent lifecycle (registration/deregistration chain entry points) |
| CLI layer | `cli/aimail` (15 subcommands; repo-root `./aimail` symlink) + ops scripts `cli/{check_status,send_welcome,repair,setup_system,deploy_bridge,ping_test}.py`; API client `pysdk/gateway_api.py` | Install / check / test / uninstall / ops |
| Install source | `pysdk/resources/skills/SKILL.md` + `DESCRIPTION.md` | Generic email skill (byte-exact copy, zero rewriting) |

**Iron rules**:
- Shared code only ever lives at the top level of `pysdk/`; platform adapters must not import across platforms — they do exactly three things: platform implementation, injection-point assignment, registration.
- Configuration has a single source of truth (see 1.4); env overrides, directory scanning, and cross-system borrowing are forbidden.
- All platforms and all inbound paths call the same `process_inbound_mail` (after signature verification).

### 1.4 Configuration Single Source of Truth

| Config | Level | Facts |
|--------|-------|-------|
| Pointer file (profile/.agentmail etc.) | System identity | system_id + email ownership |
| `agentmail.json` (systems/{sid}/{addr}/) | Address level | All address facts (incl. webhook_url/webhook_secret pair) |
| `aimail_gateway.json` (systems/{sid}/) | System level | All system facts (incl. webhook_host tri-state; the old name `agentmail_gateway.json` auto-migrates on first access) |

---

## 2. Shared Core (Must-Read for Integration)

### 2.1 Injection Points (assigned by the adapter layer after importing the shared core)

| Injection point | Meaning |
|-----------------|---------|
| `_CONFIG_LOADER` | Agent config loader `() -> Optional[dict]` |
| `_PROFILE_DIR_RESOLVER` | Agent directory `() -> Optional[str]` |
| `_PERSONAS_PROVIDER` | Personas config (platforms without persona capability set `PERSONA_SUPPORTED=False`) |
| `_SOUL_PROVIDER` / `_SKILLS_PROVIDER` | SOUL/skills for board context |
| `_BOARD_GATEWAY_SINK` | Board gateway registration callback |
| `PERSONA_SUPPORTED` | Capability switch (False → normalized base address) |
| `_AGENT_IDENTITY_OVERRIDE` (tools) | Outbound identity header X-AIMail-Agent = "platform/ver" |
| `_PERSONA_NAME_PROVIDER` (tools) | Current persona name |

### 2.2 Single Inbound Entry Point (middle-chain iron rule)

All platforms and all inbound paths (push direct delivery / bridge pull forwarding) call the same function:

```
process_inbound_mail(payload, headers)
  1. preprocess_mail_payload()   # identity → persona → enrichment → attachment storage → persistence
  2. handle_ping_pong()          # ping/pong interception (pong returned only after the full chain completes)
     → intercepted: returns None → receiver swallows with HTTP 200, agent not triggered
```

- ping/pong interception happens at the last moment before the agent is invoked: pong is only replied when the entire chain is healthy (maximizes end-to-end validation).
- When not intercepted, the receiver hands the raw body (not the enrichment output) to the agent runtime.
- `send_pong` resolves configuration via `_CONFIG_LOADER` and sends through `send_mail`; logs go to the unified `~/.aimail/logs/aimail.{cleaned_addr}.log`.

### 2.3 Address Derivation (uniform across all systems)

```
email_for_agent(agent_id, domain, system_name, default_aliases)
```

- Default-name normalization: each system's own default name → `agent` (Hermes `("default",)`, OpenClaw `("main",)`; neither replaces the other).
- Illegal-character sanitization: `.` and every other non-atext-no-dot character → `_` (unconditional); an empty result → `agent`.
- Shared domain: `{base}.{system_name}@{domain}`; standalone domain: `{base}@{domain}` (determined by the `shared-*` prefix of system_id).

### 2.4 Registration/Deregistration Chain (shared, idempotent)

```
register_agent_email(client, system_id, email, webhook_url, webhook_secret,
                     manager_address) -> {"api_key", "activation_code"}
deregister_agent_email(client, system_id, email, manager_address) -> {api_key, domain, whitelist}
```

- The registration parameter `webhook_url` is resolved by `resolve_register_webhook_url(gw, local_webhook_url)` per the webhook_host tri-state (§3.4); what is written to agentmail.json is always the local endpoint.
- After registration **always call** `register_bridge_route(system_id, email, gw, local_webhook_url)` (POST bridge /api/v1/routes, idempotent upsert) — otherwise the bridge pulls but has no route, and inbound is broken.
- The manager whitelist and domain_addr_meta are auto-created by the gateway's register_address; the Python side does not add them.
- client must be `aimail_tools._GatewayClient` (full method set).

### 2.5 Identity Model

- **1 agent = 1 AIMail address**; each agent has its own api_key (gateway send.rs enforces sender == key.email_address).
- **System identity has a single source: the pointer file**: Hermes `profiles/{name}/.aimail`, OpenClaw `~/.openclaw/.agentmail` (JSON: system_id + email).
- Single config filename: `aimail_gateway.json` (unified on both the read and write sides; the old name `agentmail_gateway.json` auto-migrates on first access — no compatibility alias).

---

## 3. Inbound Pipeline and Configuration Conventions

### 3.1 Inbound Pipeline

```
Cloud receive → gateway inbound queue → bridge pull (2s polling of /pending)
  → look up the route table aimail_routes.toml (email → full receive-endpoint URL)
  → transparent forwarding (byte-exact body + header whitelist X-AIMail-Email / X-AIMail-Timestamp / X-Webhook-Signature)
  → receive endpoint: HMAC signature verification (webhook_secret) → process_inbound_mail
  → ping/pong interception (three-stage logs) → deliver to the agent when not intercepted
```

**Three entry points keep the route table maintained** (routes are complete at all times):
1. Registration chain: after a new agent's address is registered, `register_bridge_route` is always called (§2.4);
2. CLI `aimail bridge`: full refresh (operational safety net);
3. Install-time sync: platform install flows register everything (§4 examples).

### 3.2 Receive Endpoints (webhook_url = the only trusted source in agentmail.json)

| Platform | Endpoint | Preprocessing location |
|----------|----------|------------------------|
| Hermes | `http://127.0.0.1:{port}/webhooks/aimail-inbound` | In-process (preprocessor) |
| OpenClaw | `http://127.0.0.1:18789/aimail/inbound` | OpenClaw gateway plugin endpoint (registered by openclaw-aimail inbound.ts; `gateway.port` defaults to 18789) → signature verify → TS `processInboundMail` → agent turn |
| DeerFlow | `http://127.0.0.1:8001/aimail/inbound` | In-process (8001 router) → start_run |

### 3.3 agentmail.json Fields (address level, the only trusted source)

Common 9 fields: `email` / `gateway_url` / `domain` / `system_id` / `system_name` / `manager_address` / `api_key` / `webhook_url` / `webhook_secret`.
Platform-specific: `agent_id` (OpenClaw/DeerFlow), `assistant_id` (DeerFlow).
Field semantics follow MAINTENANCE §2/§9 and the code contract.

### 3.4 aimail_gateway.json Fields (system level)

`gateway_url` / `admin_key` / `system_id` / `system_name` / `save_raw_snapshots` / `domain` / `manager_address` / `webhook_host` / `system_home` / `default_agent_name` (OpenClaw).

**webhook_host tri-state** (set at install time; determines the address registration parameter):

| State | Meaning | Registration parameter webhook_url |
|-------|---------|------------------------------------|
| Valid IP:port | Has bridge, push mode | webhook_host (bridge's public entry; the cloud pushes directly) |
| Explicitly empty "" | Has bridge, pull mode | Empty (the cloud never calls back; the bridge pulls) |
| Option absent | No bridge | agentmail.json's webhook_url (local endpoint) |

### 3.5 ping/pong Contract

- Prefixes: `__aimail_ping__:` / `__amail_pong__:` (gateway send.rs P0 exact match; if the two ends disagree, the pong never loops back).
- Three-stage events: `ping_intercepted → pong_sent → pong_returned`, written to `~/.aimail/logs/aimail.{cleaned_addr}.log` (the sole authoritative verdict for ping_test).

---

## 4. Example Deployments (Five Platforms in Production)

> TS adapter index: the `dsh-aimail` / `openclaw-aimail` / `pi-aimail`
> implementations, and the full how-to for adapting a new TS platform
> (MAIL_TOOLS iteration, identity, inbound chain, package shape), live in
> `tssdk/docs/platform-adapter-guide.md` in this repo; `tssdk/README.md`
> is the SDK overview.

### 4.1 Hermes

| Component | Location |
|-----------|----------|
| Adapter layer | `pysdk/hermes/aimail_hermes.py` (injection-point assignment + registration block; helpers pysdk/hermes/{patch_webhook,toolsets,register_profiles,ensure_config}.py) |
| Tool registration | 7 email (incl. search_mail) + 4 board tools → `registry.register` (executed at import time) |
| Inbound | Webhook preprocessor: `register_preprocessor("aimail_gateway", core.process_inbound_mail)` (in-process) |
| Lifecycle | `profile_created/deleted` hooks (event bus) |
| Deployment | `aimail install --home ~/.hermes` (current install path, replacing install-tools.sh): pysdk/install.py expands SKILL → profiles/*/skills/agentmail + toolsets.py patches platform_toolsets + board resources; supplementary registration = pysdk/hermes/register_profiles.py (full) + profile_created/deleted event hooks |
| Key pitfalls | Each profile has its own webhook port, one inbound and one outbound; the webhook session requires `platform_toolsets.webhook` to include aimail (otherwise no send_mail) |

### 4.2 OpenClaw

| Component | Location |
|-----------|----------|
| Adapter layer | tssdk `openclaw-aimail` plugin (identity = `~/.openclaw/.agentmail` pointer + agentmail.json as the single source of truth; outbound X-AIMail-Agent = `openclaw/{ver}`) |
| Tools | 13 bare-name email/board tools registered in-process by the plugin (MAIL_TOOLS as the single semantic source; not MCP) |
| Inbound endpoint | **Gateway-plugin HTTP route** `POST http://127.0.0.1:18789/aimail/inbound` (`openclaw.json gateway.port` defaults to 18789; auth=plugin, same target for bridge/direct push): HMAC signature verify → TS `processInboundMail` → agent turn (`subagent.run` primary / `gateway.request` fallback; multiple agents routed via sessionKey) |
| Lifecycle | Plugin register/deregister/status commands; or the CLI registration chain `cli/bin/register_agent.py` (local_webhook_url = 18789/aimail/inbound → bridge route) |
| Deployment | `openclaw plugins install openclaw-aimail` (or via the tssdk package); the Python side only registers/checks (cli/check_status probes the plugin endpoint at L4) |
| Key pitfalls | Call `setAgentIdentity` before inbound processing (TS-side identity injection); logs/event contract aligned verbatim with Python; the 8799 external bridge is retired (§9) |

### 4.3 DeerFlow

| Component | Location |
|-----------|----------|
| Adapter layer | `pysdk/deer-flow/amail_base.py` (`PERSONA_SUPPORTED=False` + identity injection `deerflow/{ver}`) |
| Tools | Shared MCP stdio server `pysdk/amail_mcp_server.py` (installed via cli/deer-flow/install-mcp.sh) |
| Inbound | **In-process preprocessing**: deer-flow `backend/app/gateway/routers/aimail_inbound.py` — `POST /aimail/inbound`: signature verify → process_inbound_mail → ping/pong interception → deliver via `start_run` (thread=uuid5("amail", email), assistant_id read from agentmail.json) |
| Lifecycle | `pysdk/deer-flow/manage.py` (register/reconcile/deregister subcommands; formerly scripts/deer-flow/{register_agent,reconcile,deregister_agent}.py + install-inbound.sh, consolidated here on 2026-09-02); install-time supplementary registration = manage.py reconcile (full) + cli/deer-flow/install-skill.sh / install-mcp.sh |
| Deployment | Shared layout (~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json); inbound install/patch via `pysdk/deer-flow/manage.py install/patch` (bundled install + dual-anchor app.py patch + py_compile check; the upstream repo stays clean; restart 8001 to take effect) |
| Key pitfalls | In-process import of amail_base at 8001 needs sys.path injection (router module level); Pyright false positives (the runtime path is inserted) |

### 4.4 DSH (deepseek-harness, TS plugin platform)

| Component | Location |
|-----------|----------|
| Adapter layer | tssdk `dsh-aimail` plugin (3 subpackages: mail-service / tools / inbound; identity = `~/.dsh/.agentmail` pointer; preset = definition / uuid = instance) |
| Tools | 13 bare-name email/board tools (registered at the preset layer, visible to joined sessions; outbound X-AIMail-Agent = `dsh/{ver}`) |
| Inbound | Host-layer `mail-inbound`: node:http listener (`POST /aimail/inbound`, default port `AIMAIL_INBOUND_PORT`/9099) → HMAC signature verify → TS `processInboundMail` → `followup` wakes the corresponding session |
| Lifecycle | `cli/dsh/bind_agent.py` / `unbind_agent.py` + shared registration chain (register_bridge_route always called after registration) |
| Deployment | `dsh plugin --profile web add dsh-aimail` (the bundle self-mounts via cordis.patch.yml) |
| Key pitfalls | Persona off (`PERSONA_SUPPORTED=False`; dsh-persona is same-named but means something different); multi-session isolation is backed by the gateway's `sender==key.email`; contract aligned verbatim with Python |


### 4.5 pi (TS extension platform)

| Component | Location |
|-----------|----------|
| Adapter layer | tssdk `pi-aimail` extension (identity = `~/.pi/.agentmail` pointer + agentmail.json) |
| Tools | 13 bare-name email/board tools (`pi.registerTool`, TypeBox parameters; outbound X-AIMail-Agent = `pi/{ver}`) |
| Inbound | The extension's own local listener `http://127.0.0.1:9101/aimail/inbound` (default port 9101; bridge push target) → HMAC signature verify → TS `processInboundMail` → `pi.sendUserMessage` (always triggers a turn) |
| Lifecycle | `~/.pi/.agentmail` pointer + shared registration chain (same structure as openclaw); install-time supplementary registration via cli/check_status pi adapter |
| Deployment | Copy/symlink → `~/.pi/agent/extensions/` (or the pi package); board resources expanded idempotently |
| Key pitfalls | pi has no HTTP route registration → the listener port must be reachable (webhook_url = that endpoint, full URL in the bridge route) |

### 4.6 Comparison (reference for choosing a new system; DSH/pi at a glance in §4.4/§4.5)

| Dimension | Hermes | OpenClaw | DeerFlow |
|-----------|--------|----------|----------|
| Inbound model | One inbound, one outbound (each profile its own port, in-process preprocessing) | Gateway-plugin route `/aimail/inbound` (in-process; multiple agents via sessionKey) | In-process preprocessing (8001 router, start_run delivery) |
| Tool exposure | In-process registry | Plugin in-process bare names (13 tools) | MCP stdio server (amail__ prefix) |
| Deployment | Copy-deploy (driven by `aimail install`) | TS plugin (`openclaw plugins install openclaw-aimail`) | Adapter repo-direct; preprocessing lives in the deer-flow repo (patched install + restart) |
| Lifecycle | Event bus (profile_created/deleted) | Plugin register command / CLI registration chain | manage.py reconcile |
| Persona | Full capability (PERSONA_SUPPORTED=True) | None (False) | None (False) |

---

## 5. CLI Contract (cli/aimail)

**Command-name collision warning**: `~/.local/bin/aimail` is the Hermes launcher; this repo's CLI can only be run via the repo-root `./aimail` (symlink → `cli/aimail`). Never add cli/ to the global PATH.

Subcommands (15, grouped into 4 scenarios):

- **setup**: `init` `install` `uninstall` `reset`
- **operate**: `stats` `renew` `version`
- **diagnose**: `check` `repair` `ping` `welcome` `persona`
- **resources**: `domain` `mailname` `bridge`

| Subcommand | Responsibility |
|------------|----------------|
| `bridge` | Maintain the local bridge: no args = status; `--system-id` refreshes routes; `--restart` restarts the single instance |
| `check` | Full-pipeline status check (L1 gateway / L2 bridge / L3 agent config / L4 hook / L5 ping-pong) |
| `domain` | View/create the system domain (list by default / `--add DOMAIN`) |
| `init` | One-time machine initialization: lock the gateway URL, direct or bridge mode |
| `install` | Integrate an agent platform into the AIMail system (activate or reuse an existing system, incl. platform adapter and supplementary registration) |
| `mailname` | View/modify the default main-agent name mapping (hermes default→agent; openclaw main→agent) |
| `persona` | Persona flow: the manager sends 'update persona', the agent replies with a draft |
| `ping` | ping-pong loopback test (trusts only the agent-side three-stage log events) |
| `repair` | Auto-fix per check results (idempotent), then re-check |
| `renew` | Renew the system with a product code, or read-only view of expiry |
| `reset` | Reset connection config (admin-key path; business fields unchanged) |
| `stats` | Local integration status (system/agent/mail statistics, read-only) |
| `uninstall` | Uninstall (gateway deregistration + platform cleanup + local data) |
| `version` | Show the CLI/bootstrap version (upgrade detection) |
| `welcome` | welcome end-to-end test (incl. LLM round-trip; final acceptance, peer to the `ping` test) |

**Platform inference (without --agent-type)**: decided in order by the `--home` directory features — `pi` (`~/.pi` + agent/), `dsh` (`~/.dsh` + profiles/ + storages/; dsh also has profiles/, so it must be checked before hermes), `hermes` (hermes-agent/ or profiles/), `openclaw` (openclaw.json), `deerflow` (backend/app/gateway) → resolve the configured system_home → auto-detect the pointer.

**.env auto-loading**: CLI args > shell env > .env > built-in defaults. .env keys: AIMAIL_URL / AIMAIL_ADMIN_KEY / AIMAIL_PRODUCT_CODE / AIMAIL_MANAGER_ADDRESS / AIMAIL_SYSTEM_NAME / AIMAIL_DOMAIN / AIMAIL_SAVE_SNAPSHOTS / AIMAIL_WEBHOOK_HOST.
install is fully non-interactive: activate → take the server-assigned system_id from the setup_system JSON stdout → preset/create domain → deploy_bridge → platform adapter.

---

## 6. Checklist for Integrating a New Agent System (8 Steps)

1. **Reference the shared layer**: import `pysdk/aimail_base` / `aimail_tools` / `aimail_board` (insert pysdk/ on sys.path); never copy or modify shared code.
2. **Write the adapter layer** `pysdk/<system>/<adapter>.py` (or a platform-side TS plugin): the platform's three things (config source / personas or `PERSONA_SUPPORTED=False` / identity injection `_AGENT_IDENTITY_OVERRIDE = "platform/ver"`) + assign the injection points (§2.1).
3. **Expose tools**: in-process registry (following Hermes), platform plugin (following the DSH/pi/OpenClaw TS plugins), or directly reuse the shared `pysdk/amail_mcp_server.py` (platform-agnostic; just write agentmail.json per the shared layout).
4. **Wire up inbound**: the receive endpoint first injects agent config (the equivalent of set_agent_context) → signature verify → `process_inbound_mail` → deliver the raw body when not intercepted; for inbound pulling reuse aimail-bridge, never write a new poller.
5. **Wire up lifecycle**: with an event bus → hook the events; without → wrap the agents add/delete CLIs to call the shared registration/deregistration chain.
6. **Install the skill**: byte-exact copy of `pysdk/resources/skills/SKILL.md` (+ DESCRIPTION.md), zero rewriting.
7. **Register with the CLI**: add the adapter to the cli/check_status.py `PLATFORMS` registry (four functions: detect / list_agents / check_config / check_hook); add a branch in the install/uninstall platform-adapter section (incl. install-time supplementary registration).
8. **Acceptance (two-test iron rule)**: `aimail check` all green → `aimail ping` closes the three-stage loop → `aimail welcome`: the manager receives a Re: reply (headed by `X-AIMail-Agent: {platform}/{version}`).

---

## 7. Security Model

- **Least-privilege keys**: each agent has its own api_key; SMTP auth.local authentication accepts only the agent's own key (no admin_key fallback); ping_test's pending polling uses the system-scope admin_key.
- **Pointer file is the single source**: system identity = pointer file; no scanning, no env overrides, no cross-system borrowing.
- **Security-argument iron rule**: analyze the attack surface and the leverage; a mitigation that only adds complexity without shrinking the attack surface is a pseudo-optimization.
- **Outbound custom-header whitelist**: X-AIMail-Agent / X-Board-Members / X-AIMail-AutoReply pass through outbound; X-Board-ID/Role are internal-forward only; _persona.* is internal-only.

---

## 8. Integration Troubleshooting Quick Reference

| Symptom | Root cause |
|---------|------------|
| ping never gets a pong | Prefix mismatch (PONG_PREFIX must be `__amail_pong__:`); or the receive endpoint skipped the final process_inbound_mail step |
| Inbound broken (new agent) | register_bridge_route not called after registration (no route-table entry) |
| Webhook session receives but can't reply | Profile `platform_toolsets.webhook` lacks aimail; or the routed skills are empty |
| Logs land in aimail.default.log | Standalone process didn't set_agent_context / didn't export AIMAIL_AGENT_EMAIL |
| Bridge retries 401 forever | webhook_secret inconsistent with the receive-endpoint config (the value written at registration) |
| Inbound enrichment skipped | Receive endpoint called process_inbound_mail without injecting agent config first |
| check reports system missing | Pointer file lacks system_id; or the wrong home was read (profile layouts need --agent-home) |
| MCP connection hangs | Wrong framing: the MCP SDK uses newline JSON, not Content-Length |
| Agent replies with the wrong platform identity | Adapter layer didn't inject _AGENT_IDENTITY_OVERRIDE (directory detection misjudged) |

---

## 9. Retired / Do Not Use

- **amail-poll.py**: deleted. Inbound pulling is unified through aimail-bridge (single process, multiple systems).
- **amail_deerflow_bridge.py** (8798): retired. DeerFlow inbound is 8001 in-process preprocessing.
- **amail_openclaw_bridge.py** (8799 / hook external preprocessing process): retired. OpenClaw inbound is the gateway plugin endpoint `http://127.0.0.1:18789/aimail/inbound` (openclaw-aimail plugin, consistent with the cli/check_status comment and cli/bin/register_agent.py).
- **integrate.sh / uninstall.sh / bridge-ctl.sh / install-tools.sh**: replaced by `aimail install/uninstall/bridge` (install-tools.sh is also replaced by the pysdk/hermes/toolsets.py toolset patch).
- **agentmail_gateway.json** (old name, before 2026-09-04): read/write unified on `aimail_gateway.json`; the old name auto-migrates on first access, no compatibility alias.
- **--agent-type argument**: platforms are inferred from facts; manual specification is forbidden.
- **mode / bridge_port config options**: the webhook_host tri-state expresses push/pull; the receive-endpoint port lives in webhook_url.
- **docs/ directory**: the official documentation directory (versioned, maintained with the repo); authoritative interface wording lives in MAINTENANCE.md and README.md.
