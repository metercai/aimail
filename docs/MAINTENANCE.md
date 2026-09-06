# AIMail Installation & Maintenance Guide

> Applies to the `aimail` CLI (repo `cli/`) — the single on-machine tool for
> installing, operating and maintaining the AIMail link between an agent
> platform and the amail gateway. Brand rule: **aimail** is the external
> name (CLI, gateway, config file); **agentmail** is the agent-internal name
> (tools/skills/`agentmail.json`).

---

## Contents

1. [Purpose & Scope](#1-purpose--scope)
2. [Architecture & Local Layout](#2-architecture--local-layout)
3. [Installation](#3-installation)
4. [Maintenance Workflow (stats → check → repair)](#4-maintenance-workflow-stats--check--repair)
5. [What the Tooling Achieves](#5-what-the-tooling-achieves)
6. [Quick Reference](#6-quick-reference)
7. [Troubleshooting](#7-troubleshooting)
8. [Machine Migration](#8-machine-migration)
9. [Contracts & Single Source of Truth](#9-contracts--single-source-of-truth)

---

## 1. Purpose & Scope

### What the CLI is

`aimail` is the **local-machine infrastructure tool** for the AIMail stack.
It never runs remotely: every subcommand operates on the machine you are on.
It is the single write path for integration resources (activation, domains,
binding, routes) so that local state and the gateway stay consistent.

### Three-layer operating model

| Layer | Tool | Runs | Scope |
|-------|------|------|-------|
| Machine prep | bootstrap (automatic) | any machine | home dir / gateway decision / bridge in place (done during bootstrap) |
| System integration (per system) | `aimail install` | per platform root | activate/reuse a system, bind the platform, deploy the bridge entry |
| Agent runtime SDK | platform package | agent host | `pysdk` (python, hermes/deerflow) or `tssdk` (openclaw/pi/dsh); self-check → auto-bind |

The CLI itself carries **no runtime resources**: `cli + one SDK + config file`
is a complete integration. The CLI delegates platform patching to the SDKs
(`python -m aimail.install install --type hermes|deerflow`).

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

### Directory tree

```
~/.aimail/
├── systems/{system_id}/
│   ├── aimail_gateway.json     # gateway connection config (canonical name
│   │                           #   since 2026-09-04; legacy agentmail_gateway.json
│   │                           #   is auto-migrated on first read)
│   ├── board/                  # system-level A2A role prompts (fallback)
│   └── {agent_addr}/           # per-address dir (keyed by cleaned email)
│       ├── agentmail.json      # agent config — 9 mandatory fields (see §9)
│       └── role_prompt/        # address-level role prompts (priority)
├── logs/
│   ├── aimail-bridge.log       # bridge runtime log
│   └── aimail.{addr}.log       # per-agent processing log (NOT under mail/)
├── bridge/
│   ├── aimail_bridge.toml      # bridge config (pull.systems list)
│   ├── aimail_routes.toml      # route table: email → local inbound endpoint
│   ├── bin/aimail-bridge       # bridge binary
│   └── bridge.pid
├── mail/{addr}/{yyyymm}/in-*.json   # inbound snapshots (debugging)
├── .system_raw_key/{sid}_admin.key  # raw admin key (integration only)
└── .env                            # machine-level env (bootstrapped installs)
```

Platform root pointer (`.agentmail`, contains `{system_id, email}`):
`~/.hermes/.agentmail` or `profiles/*/.agentmail` (hermes) ·
`~/.openclaw/.agentmail` (openclaw) · `~/.pi/.agentmail` (pi) ·
`~/.dsh/.agentmail` (dsh) · `~/.deer-flow/.agentmail` (deerflow).

### Network model

Agent side is always **push**. If the gateway URL resolves to the local
machine (`127.0.0.1`/`localhost`/local IP), the registration chain connects
directly — no bridge. Otherwise the local `aimail-bridge` pulls pending
mail from the gateway (mode=`pull`) and routes it to local inbound
endpoints via `aimail_routes.toml`. Whether a bridge is needed is a
machine-level decision made during bootstrap (install reuses its result).

### Three authoritative config files

| File | Content | Written by |
|------|---------|-----------|
| `systems/{sid}/aimail_gateway.json` | gateway_url, admin_key, system_id, system_name, manager_address, system_home, domain, webhook_host | `install`/`reset` → setup_system.py; `repair` backfills `system_home`/`webhook_host` |
| `systems/{sid}/{addr}/agentmail.json` | 9 fields: email, gateway_url, domain, system_id, system_name, manager_address, api_key, webhook_url, webhook_secret | registration chain (register_profiles/register_agent/bind_agent) |
| `bridge/aimail_bridge.toml` + `aimail_routes.toml` | pull systems + route table | deploy_bridge.py; `aimail bridge --system-id` |

`system_home` in the gateway config is the **only** source of the platform
label shown by `stats` (feature-detected from the directory, never guessed).

---

## 3. Installation

### Step 0 — machine environment (5-minute path, no file editing needed)

```bash
# host installed → export values (take effect immediately):
export AIMAIL_URL=https://aimail.token.tm
export AIMAIL_MANAGER_ADDRESS=you@example.com
export AIMAIL_ADMIN_KEY=<key>          # reuse path  OR
export AIMAIL_PRODUCT_CODE=<code>      # new-system path (+ AIMAIL_SYSTEM_NAME)

# bootstrap (installs toolkit under ~/.aimail, symlink ~/.local/bin/aimail,
# persists the exported AIMAIL_* values into ~/.aimail/.env):
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

### Step 1 — Machine prep (automatic via bootstrap)

`curl|bash bootstrap` creates `~/.aimail/{systems,logs,bridge}` (0700),
checks free disk and resolves the network structure: local gateway →
direct-push (no bridge); remote gateway → deploys the bridge binary +
skeleton config (idempotent).

> The legacy `aimail init` subcommand is no longer registered (the
> function remains); bootstrap handles the machine prep and `aimail
> install` deploys the bridge at first-system activation — skip straight
> to Step 2 when installing.

### Step 2 — `aimail install` (per platform, repeatable, idempotent)

```bash
aimail install --home <platform-root> [--system-id <sid>]
              [--product-code <code> | --admin-key <key>]
              [--manager <addr>] [--domain <domain>] [--system-name <name>]
```

- **New system** (`--product-code`): activates on the gateway (server-side
  code claim is atomic; a repeated run with the same code fails cleanly
  before any local write).
- **Existing system** (`--admin-key` or stored config): resets/re-persists
  local connection config **without** re-activation — never consumes a code
  twice.
- Runs the full chain: system activation/reuse → domain ensure → bridge
  deploy (merge entry, **reuse existing bridge api_key**) → platform
  binding (hermes: SDK patch+profiles+skills; openclaw/pi: agent
  registration + pointer; dsh: plugin; deerflow: SDK reconcile + patch).
- Install is safe to re-run: every step is presence-checked or
  merge-by-system_id; a repeated run does not mint orphan credentials.

### Step 3 — platform-side binding

Hermes/openclaw/pi/deerflow are bound during `install`. For dsh, sessions
bind lazily: `dsh-aimail` auto-binds on first use (one session ⇔ one
address, existence-guarded); manual equivalent:
`python3 cli/dsh/bind_agent.py [--session-id …] [--preset mail]`.

### Step 4 — verify

```bash
aimail check --system-id <sid>     # full health exam (see §4)
aimail ping --system-id <sid>      # ping → pong round trip (authoritative:
                                   #   agent-side log events)
aimail welcome --system-id <sid>   # welcome e2e (API mode) — sent by the
                                   #   gateway system sender (noreply@{gateway domain})
```

`.env`/`export` priority: **CLI flag > shell env > `~/.aimail/.env` >
repo `.env` > built-in default**. `.env` is auto-loaded; repeated values
never need to be re-typed.

---

## 4. Maintenance Workflow (stats → check → repair)

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

| Layer | Level | What is examined |
|-------|-------|------------------|
| Config files | L0 | `aimail_gateway.json` completeness (gateway_url/admin_key/`system_home`/pointer) · `aimail_bridge.toml` structure (mode, pull entries, admin_key match vs gateway.json) · `agentmail.json` 9-field completeness + internal consistency (system_id=sid, gateway_url same, domain=email suffix) |
| Gateway / Bridge | L1/L2 | gateway health + SMTP :25 + whoami scope; bridge process + pull path + routes coverage (every agent email must have a route entry) |
| Platform runtime resources | L2r | hermes: webhook.py `PREPROCESS_REGISTRY` + profiles.py `AmailGateway` patch markers, toolsets, skills, board/role_prompt/common.md · openclaw: plugin installed + skills · deerflow: app.py `aimail_inbound` anchors · pi: pointer match |
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

## 5. What the Tooling Achieves

Capability summary: **stats** tags health by facts (a missing `system_home`
shows `[?]`, an absent pointer is flagged); **check** catches genuine issues
such as declared webhooks that are dead, missing routes, and bridge
pull-entry admin_key drift; **repair** fixes everything locally fixable and
honestly preserves host-side FAILs; a repeated **install** mints no orphan
bridge key and never double-activates. Net effect: **stats points → check
pinpoints → repair fixes → re-check confirms**, with every remaining red
item being a genuine host-side action.

---

## 6. Quick Reference

Subcommands grouped by scenario (`aimail --help` shows this):

```
setup      init  install  uninstall  reset
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
use logrotate with the patterns from this repo's older docs if needed.

---

## 7. Troubleshooting

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

**Fix:** openclaw: `openclaw plugins install npm-pack:<openclaw-aimail.tgz>
--force` + restart the gateway; hermes: re-run the SDK install
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

## 8. Machine Migration

The gateway stores mail/storage; a machine keeps local config + snapshots
+ bridge only.

```bash
# 1. OLD machine — collect credentials:
ls ~/.aimail/.system_raw_key/          # {sid}_admin.key
ls ~/.aimail/systems/{sid}/            # aimail_gateway.json + agents

# 2. NEW machine — machine environment once:
git clone https://github.com/metercai/aimail.git && cd aimail
cp docs/.env.example .env              # AIMAIL_URL + AIMAIL_MANAGER_ADDRESS
# (no init step; bootstrap prepped the machine, install deploys the bridge)

# 3. Restore credentials:
mkdir -p ~/.aimail/.system_raw_key && cp <old>/{sid}_admin.key ~/.aimail/.system_raw_key/
export AIMAIL_ADMIN_KEY=$(cat ~/.aimail/.system_raw_key/{sid}_admin.key)

# 4. Reuse the system (no new activation):
aimail install --home <platform-root> --system-id <sid>

# 5. Verify:
aimail check --system-id <sid> && aimail welcome --system-id <sid>
```

`install` never activates twice when an admin key is present (reuse path),
so a migrated machine consumes no activation code.

---

## 9. Contracts & Single Source of Truth

Python CLI code references these contracts; the **TS SDK (`tssdk/`) is the
single source of truth** — never redefine, only reference.

**Inbound endpoints** (per platform, `POST`): openclaw `:18789/aimail/inbound`
· pi `:9101/aimail/inbound` · dsh `:9099/aimail/inbound` · deerflow
`:8001/aimail/inbound` · hermes `:8646/webhooks/aimail-inbound` (port from
profile config). The local inbound URL is what `agentmail.json` stores as
`webhook_url` and what the bridge route table targets.

**`aimail_gateway.json`** (renamed from `agentmail_gateway.json` on
2026-09-04 to align with the gateway name; legacy name auto-migrates on
first read): `gateway_url`, `admin_key`, `system_id`, `system_name`,
`manager_address`, `domain`, `system_home`, `webhook_host`,
`save_raw_snapshots` (always written, defaults to `true`),
`default_agent_name` (optional-value field, written by `address default`).

**`agentmail.json`** 9 mandatory fields: `email`, `gateway_url`, `domain`,
`system_id`, `system_name`, `manager_address`, `api_key`, `webhook_url`
(local inbound endpoint — the only trusted source for the bridge route),
`webhook_secret`.

**Address semantics** (shared domains): agent address =
`{agent}.{system_name}@{shared-domain}` (e.g. `agent.xianlin@aimail.token.tm`,
`pi.xianlin@…`) derived via `email_for_agent`; the system identifier
(`system_name`) is globally unique per shared domain (pickup occupancy +
activation UNIQUE + address UNIQUE triple guard) — two different systems
can never share an identifier on the same shared domain. Non-shared
systems address as `{agent}@{bare-domain}` and may own several bare
domains (any of them can carry a renewal pickup).
