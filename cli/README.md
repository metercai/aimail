# aimail CLI

[🇨🇳 中文](README_zh.md)

> Applies to `aimail` — the CLI that installs and maintains AIMail on the machine
> hosting the agent. Its operating scope covers the machine-level aimail
> environment, one agent system instance's aimail configuration, and one specific
> agent's aimail configuration.

---

## Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [Architecture & Local Layout](#2-architecture--local-layout)
3. [Installation](#3-installation)
4. [Maintenance Workflow](#4-maintenance-workflow)
5. [Quick Reference](#5-quick-reference)
6. [Troubleshooting](#6-troubleshooting)

---

## 1. Purpose & Scope

### What the CLI is

`aimail` is the **local-machine infrastructure tool** for the AIMail stack.
It never runs remotely: every subcommand operates on the machine you are on.
It is the single write path for agent↔aimail integration (activation, domains,
binding, routes), so that local state stays healthy and consistent with the
gateway.

### Three-layer operating model

| Layer | Tool | Object identity | Responsibility |
|-------|------|-----------------|----------------|
| Machine environment | bootstrap (automatic) | host system | home dir / gateway decision / bridge in place (settled during bootstrap) |
| Agent-platform integration | `aimail install` etc. | platform root / SID | activate or reuse a system, bind the platform, import bound resources, merge the bridge entry |
| Agent parameters | `aimail address` | agent identity / address | view / set the default main-address name / rename an address / set the manager (safety officer) |

### Platform Integration

All platform adaptation lives in the SDKs; the CLI only dispatches through the
registry (`cli/platforms.json`). Adding a platform = one registry entry + SDK-side
adapter, with no change to the CLI protocol. Per-platform install action, registrar
and restart requirement:

| Platform | Platform root | Runtime | Install action | Restart |
|----------|---------------|---------|----------------|---------|
| Hermes | `~/.hermes` | Python (pysdk) | When the venv exists, `pip install aimail` inside it first; the SDK install expands SKILL/toolsets/board resources and injects `PREPROCESS_REGISTRY` into webhook.py; registers the primary agent (`register_profiles.py` for every profile) | restart the hermes gateway |
| DeerFlow | `~/.deer-flow` | Python (pysdk) | SDK install → `install-skill.sh` + `install-mcp.sh` → register (`manage.py register --all`) | restart 8001 (upstream repo installed by patch) |
| OpenClaw | `~/.openclaw` | TS (tssdk) | `openclaw plugins install openclaw-aimail --force --accept-capabilities` → register (`openclaw aimail register`; `register-all` for all) | restart the openclaw gateway |
| DSH | `~/.dsh` | TS (tssdk) | `dsh plugin --profile web add dsh-aimail` (needs the `dsh` CLI first) | binding is auto-bound when a dsh session (mail preset) starts |
| Pi | `~/.pi` | TS (tssdk) | `pi install npm:pi-aimail` | restart pi |

Common commands:

```bash
aimail install --home <platform-root>                     # single agent
aimail install --home <platform-root> --all-agents        # every agent under the root (multi-profile platforms)
aimail install --home <platform-root> --system-id <sid>   # reuse an existing system (no re-activation)
```

Integration is complete when, after `aimail welcome`, the manager receives the
agent's welcome-mail reply.

### Maintenance loop (the point of this guide)

```
aimail stats -a     →  what is integrated / healthy / broken on this machine
aimail check        →  full health exam (config → runtime resources → links)
aimail repair       →  apply the idempotent fix ladder for check findings
```

Use `stats` to spot problems, `check` to pin them down precisely, `repair`
to fix what is fixable locally — then re-check until only genuine
host-side items remain.

---

## 2. Architecture & Local Layout

### Directory tree (`~/.aimail`)

```
~/.aimail/
├── systems/{system_id}/
│   ├── aimail_gateway.json     # gateway connection config (system level)
│   ├── board/                  # system-level A2A role prompts (fallback)
│   └── {agent_addr}/           # per-address dir (keyed by cleaned email)
│       ├── agentmail.json      # agent config — 9 mandatory fields
│       └── role_prompt/        # address-level role prompts (priority)
├── logs/
│   ├── aimail-bridge.log       # bridge runtime log
│   └── aimail.{addr}.log       # per-agent processing log
├── bridge/
│   ├── aimail_bridge.toml      # bridge config (pull.systems list)
│   ├── aimail_routes.toml      # route table: email → local inbound endpoint
│   ├── bin/aimail-bridge       # bridge binary
│   └── bridge.pid
├── mail/{addr}/{yyyymm}/in-*.json   # snapshots: in-* (inbound) / out-* (outbound)
├── .system_raw_key/{sid}_admin.key  # raw admin key (integration only)
└── .env                            # machine-level env (bootstrapped installs)
```

### Network model

- System-level integration: the agent side always takes inbound mail in
  **push** mode. Public reachability of the gateway is what the bridge solves;
  the bridge supports both push and pull, chosen by the network environment.
- Address-level integration: the agent side always **pulls** inbound mail.
  No aimail CLI or bridge involvement.
- Whether a bridge is needed, and which mode it uses toward the gateway, is
  part of the machine environment — decided once at bootstrap.

### Three authoritative config files

| File | Content | Written by |
|------|---------|-----------|
| `systems/{sid}/aimail_gateway.json` | gateway_url, admin_key, system_id, system_name, manager_address, system_home, domain, webhook_host (+ save_raw_snapshots / default_agent_name) | `install`/`reset` → setup_system.py; `repair` backfills `system_home`/`webhook_host` only |
| `systems/{sid}/{addr}/agentmail.json` | 9 fields: email, gateway_url, domain, system_id, system_name, manager_address, api_key, webhook_url, webhook_secret | registration chain (register_profiles / register_agent / bind_agent) |
| `bridge/aimail_bridge.toml` + `aimail_routes.toml` | pull systems + route table | deploy_bridge.py; `aimail bridge --system-id` |

`system_home` in the gateway config is the **only** source of the platform
label shown by `stats`.

`aimail_gateway.json` (system level) fields:

| Field | Meaning |
|-------|---------|
| gateway_url | gateway address; a loopback address means direct local push, otherwise inbound goes through the bridge |
| admin_key | system-level credential: install derives a restricted agent_admin key and stores it here; the raw key stays at `.system_raw_key/{sid}_admin.key` |
| system_id | system identifier (SID) |
| system_name | system name; the source of the agent address prefix on shared domains |
| manager_address | default manager address of the system |
| system_home | platform root (e.g. `~/.hermes`) |
| domain | system domain (dedicated bare domain or shared domain) |
| webhook_host | tri-state switch for gateway → machine callbacks: `IP:port` = bridge present, push; empty string = bridge present, pull; field absent = no bridge, call the local endpoint directly |
| save_raw_snapshots | keep a raw snapshot of every mail (default true) |
| default_agent_name | default main agent name (written by `aimail address -d`) |

`agentmail.json` (address level, the only trusted source) fields:

| Field | Meaning |
|-------|---------|
| email | agent address (full address; its cleaned form is the directory name) |
| gateway_url | gateway address |
| domain | domain of the address (= email suffix) |
| system_id / system_name | owning system |
| manager_address | manager of this address |
| api_key | server-side key of this address (issued by the registration chain) |
| webhook_url | local inbound endpoint (the only trusted source for the bridge route) |
| webhook_secret | inbound signing secret (gateway signs → agent verifies) |

---

## 3. Installation

### Step 1 — machine environment (bootstrap)

- Install your own aimail-gateway service, or apply for a shared-gateway service.
- Then set the system admin-key / product_code and related values as
  environment variables, and run AIMail's bootstrap install script to finish
  the local environment setup. For example:

```bash
export AIMAIL_URL=<your gateway address>      # self-hosted gateway, e.g. https://mail.example.com
export AIMAIL_ADMIN_KEY=<admin key>           # the gateway's admin key
export AIMAIL_DOMAIN=<your domain>            # dedicated domain, e.g. example.com
export AIMAIL_MANAGER_ADDRESS=you@example.com # default manager address of the admin agent; may differ per agent
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

### Step 2 — `aimail install` (system level, repeatable, idempotent)

```bash
aimail install --home <platform-root>  --system-id <sid> 
```

### Step 3 — end-to-end verification

```bash
aimail check --system-id <sid>     # full health exam (see §4)
aimail ping --system-id <sid>      # ping → pong round trip (authoritative: agent-side log)
aimail welcome --system-id <sid>   # welcome end-to-end (API mode), sent by noreply@{gateway domain}
```

---

## 4. Maintenance Workflow

### 4.1 `aimail stats` — machine integration overview (read-only)

```bash
aimail stats        # default: systems + agents + mail counts + expiry
aimail stats -a     # full view: health tags + broken systems + platforms
```

`-a` per-system health: `home-ok/home-missing/home-dir-missing` ·
`pointer:…/pointer-none` · `cloud: ok/unlinked/broken-config/unreachable`.
Broken systems are classified by facts only (missing connection fields =
`broken-config`; gateway 403/404 = `unlinked`; network error = `unreachable`,
not broken). The platform section lists the five platform roots with link
state and prints the maintenance hint.

### 4.2 `aimail check` — full health exam (order is fixed)

Dimension order (user-mandated): **config files → platform runtime
resources → agent config → delivery links**.

| Dimension | Level | What is examined |
|-----------|-------|------------------|
| Config files | L0 | `aimail_gateway.json` completeness (gateway_url/admin_key/`system_home`/pointer) · `aimail_bridge.toml` structure (mode, pull entries, admin_key match vs gateway.json) · `agentmail.json` 9-field completeness + internal consistency (system_id=sid, gateway_url same, domain=email suffix) |
| Gateway / Bridge | L1/L2 | gateway health + SMTP :25 + whoami scope; bridge process + pull path + routes coverage (every agent email must have a route entry) |
| Platform runtime resources | L2r | hermes: webhook.py `PREPROCESS_REGISTRY` + profiles.py `AimailGateway` patch markers, toolsets, skills, board/role_prompt/common.md · openclaw: plugin installed + skills · deerflow: app.py `aimail_inbound` anchors · pi: pointer match |
| Agent config | L3 | per-platform adapter: name&api_key / webhook secret / skill / toolset / register |
| Delivery links | L4 | hook probes against the real inbound endpoints — **404 = route not registered = FAIL**; remote (non-loopback) targets are not probeable locally → PASS-with-note, never a false FAIL |

### 4.3 `aimail repair` — idempotent fix ladder

```bash
aimail repair [--system-id <sid>] [--home <root>] [--deep] [--dry-run]
```

`--dry-run` prints the plan only. The ladder (each step idempotent):

1. bridge alive (restart if dead) — 2. routes refresh via
   `bridge --system-id` — 3. gateway webhook pairing fix (evidence-driven)
   — 4. gateway config backfill (`system_home`/`webhook_host`, fill-missing
   only, never clobber) — 5. platform pointer rebuild (only when the
   platform root is certain and the pointer is absent) — 6. runtime
   resource redeploy (`python -m aimail.install install --type …`,
   idempotent; skipped with a hint when the platform host is remote) — 7.
   `agentmail.json` backfill + `webhook_url` alignment to the live route
   target (local-only) — 8. route-entry rebuild — 9. bridge pull-entry
   admin_key alignment to gateway.json (authoritative source).

`--deep` additionally performs the webhook-pairing rewrite and the
stuck-pending cleanup.

`repair` always re-runs `check` at the end. Remaining FAILs after repair
must be genuine host-side items (remote platform not running, agent needs
re-registration, …) — the tooling reports them precisely instead of
papering over them.

### 4.4 Day-to-day operations

| Action | Command | Notes |
|--------|---------|-------|
| Add a domain | `aimail domain -s <sid> -a example.com` | **CLI is the only entry** for domain creation (SPA add button removed); input is lowercased, server quota + UNIQUE enforce |
| List domains | `aimail domain -s <sid>` | non-shared systems may own several bare domains; any one of them can carry a renewal pickup |
| Renew a system | `aimail renew -s <sid> -c <code>` | stacked `max(now, current)+validity`, quotas max-merge, auto-unsuspend |
| Expiry view | `aimail renew -s <sid> --status` | read-only, no code consumed |
| Agent addresses | `aimail address -s <sid> [-d NAME \| -a agent -n NAME \| -m mgr]` | list / set default main-agent name / set-name (rename w/ full server-side resource inheritance) / set-manager |
| Reset config | `aimail reset -H <root> -s <sid>` | admin-key path only, key untouched |
| Bridge ops | `aimail bridge` / `--restart` / `-s <sid>` | status / single-instance restart / route refresh |
| Remove integration | `aimail uninstall -s <sid> [-H <root>] [-y]` | gateway deregister → platform cleanup → local data; idempotent |
| E2E tests | `aimail ping` / `welcome` / `persona` | heartbeat / welcome mail / persona update loop |

Short flags are globally consistent: `-s` system-id · `-H` home · `-g`
gateway-url · `-m` manager · `-c` code · `-n` system-name (install/reset)
or dry-run (repair) · `-d` domain (install) or default (address) · `-w`
no-wait (welcome/persona) (or the domain's `--webhook-url`) · `-a` all
(stats) or add (domain) · `-t` status (renew) or timeout (ping) · `-D`
deep · `-r` restart · `-k` admin-key · `-y` yes. Long names never change.

---

## 5. Quick Reference

Subcommands grouped by scenario (`aimail --help` shows this):

```
setup      install  ensure-system  uninstall  reset
operate    stats  renew  version
diagnose   check  repair  ping  welcome  persona
resources  domain  address  bridge
```

Platform feature detection (order): `pi` (~/.pi + agent/) → `dsh`
(~/.dsh + profiles/ + storages/) → `hermes` (hermes-agent/ or profiles/)
→ `openclaw` (openclaw.json) → `deerflow` (backend/app/gateway/) →
`unknown`. `--system-id` + stored `system_home` reverse lookup beats
auto-probe; pointer ownership is the next fallback.

Logs: bridge → `~/.aimail/logs/aimail-bridge.log`; per-agent →
`~/.aimail/logs/aimail.{addr}.log` (JSON lines; `dir` = ping_intercepted /
pong_sent / pong_returned / inbound / outbound). No auto-rotation —
use logrotate if needed.

---

## 6. Troubleshooting

### A system shows `[?]` in stats / check fails `config/system_home`

**Cause:** `aimail_gateway.json` has no `system_home` (or the directory is
gone) — the platform label and every platform-dependent check lose their
anchor.

**Fix (on the platform's own host):**
```bash
aimail install --home <platform-root> --system-id <sid>   # backfills, no clobber
# or let repair do it:
aimail repair --system-id <sid> --home <platform-root>
```

### check FAILs `hook … 404`

**Cause:** the platform's inbound route is not registered (plugin missing,
gateway not restarted after plugin install, or stale endpoint path).
404 on a probe is now a real FAIL by design.

**Fix:** openclaw: `openclaw plugins install openclaw-aimail
--force --accept-capabilities` + restart the gateway (offline/local tarball:
`npm-pack:<tgz> --force`); hermes: re-run the SDK install
(`python -m aimail.install install --type hermes --home ~/.hermes`) and
restart the profile gateway; then `aimail repair --system-id <sid>`.

### check FAILs `routes-entry` / `routes-target`

**Cause:** the bridge route table is missing the agent (pull mode cannot
deliver) or the route target / declared webhook disagree.

**Fix:** `aimail repair --system-id <sid>` (steps 2/7/8: refresh routes,
align webhook_url to the live target). If the target host is remote
(pi/deerflow on another machine), start that platform's inbound there.

### ping stuck on "pong not returned"

Check the per-agent log for the three phases:
`grep <ping_id> ~/.aimail/logs/aimail.{addr}.log`; verify email + api_key
match in `agentmail.json`; re-run `aimail reset -H <platform-root> -s <sid>`
to re-persist.

### Bridge cannot pull emails

`aimail bridge` (process/config/routes) → `curl https://aimail.token.tm/health`
→ `tail -20 ~/.aimail/logs/aimail-bridge.log` → `aimail repair -s <sid>`.

### Repeated install created problems?

It cannot: activation is atomic server-side, config writes are
merge/presence-checked, bridge key is reused. If a run failed midway,
`aimail check` + `aimail repair` restore the invariant state.

---
