# AgentMail Maintenance Guide

---

## Contents

1. [Local Storage](#1-local-storage)
2. [Logs](#2-logs)
3. [Diagnostics (CLI)](#3-diagnostics-cli)
4. [aimail-bridge](#4-aimail-bridge)
5. [Hermes Gateway](#5-hermes-gateway)
6. [Common Issues](#6-common-issues)
7. [CLI Reference](#7-cli-reference)

---

## 1. Local Storage

### Directory Structure

```
~/.agentmail/
├── systems/
│   └── {system_id}/
│       ├── agentmail_gateway.json     # Gateway connection config (gateway_url, admin_key, system_id, domain)
│       ├── board/                     # system-level A2A role prompts (fallback)
│       └── {agent_addr}/              # per-address dir (keyed by cleaned email)
│           ├── agentmail.json         # agent config (email, api_key)
│           ├── board_creds.json       # A2A board credentials (board_id → gateway_url/token)
│           └── role_prompt/           # address-level role prompts (takes priority)
├── mail/
│   └── {agent_addr}/
│       ├── agentmail.log              # agent pipeline log
│       └── {yyyymm}/in-*.json         # monthly inbound snapshots
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

### Key Files

| File | Content | Written By |
|------|---------|------------|
| `systems/{sid}/agentmail_gateway.json` | gateway_url, admin_key, system_id, system_name, manager_address, system_home | `agentmail install` / `reset` → `setup_system.py` |
| `systems/{sid}/{addr}/agentmail.json` | email, api_key, gateway_url, domain, system_id, manager_address | registration chain (`register_profiles.py` / `register_agent.py`) |
| `bridge/aimail_bridge.toml` | mode, addr/pull config | `deploy_bridge.py` |

All config lives under `~/.agentmail/`; no gateway config is kept in the agent home
(pointer files `.agentmail` in profile dirs only reference the system_id).

---

## 2. Logs

### Log Files

| File | Content | Location |
|------|---------|----------|
| **agentmail.log** | Mail pipeline logs (ping/pong, inbound/outbound, preprocessing) | `~/.agentmail/mail/{agent_addr}/agentmail.log` |
| **aimail-bridge.log** | Bridge runtime logs (pull, forward, routing, health) | `~/.agentmail/logs/aimail-bridge.log` |
| **gateway.log** | Hermes gateway log (per profile) | `~/.hermes/gateway.log` (root) or `~/.hermes/profiles/{name}/gateway.log` |

### agentmail.log Format

One JSON object per line:

```json
{"ts":"2026-06-26T07:18:41Z","dir":"ping_intercepted","ping_id":"54deaff9cacc","from":"925457@qq.com","to":["mike@amail.token.tm"]}
```

`dir` values:
- `ping_intercepted` — webhook received ping email
- `pong_sent` — pong sent via send_mail
- `pong_returned` — pong looped back to webhook
- `inbound` — normal inbound email

### Log Rotation

No auto-rotation. Configure logrotate or cron:

```bash
# /etc/logrotate.d/agentmail
~/.agentmail/mail/*/agentmail.log {
    daily
    rotate 7
    compress
    missingok
}
~/.agentmail/logs/aimail-bridge.log {
    daily
    rotate 7
    compress
    missingok
}
```

---

## 3. Diagnostics (CLI)

### Run

```bash
# Full pipeline diagnostics
./agentmail check

# With repair suggestions
./agentmail check --verbose

# End-to-end heartbeat test (ping → pong loop)
./agentmail ping

# Welcome e2e test (send a welcome email to the manager)
./agentmail welcome
```

### check Layers

| Layer | Checks | Purpose |
|-------|--------|---------|
| **Level 1: gateway** | Health / whoami / domain list | Verify gateway connectivity and permissions |
| **Level 2: bridge** | Process alive / pending query / log activity | Verify bridge runtime and pull path |
| **Level 3: agent-gw** | Webhook port reachable / route config | Verify Hermes gateway ready |
| **Level 4: profile** | Config file exists / email valid | Verify agent profile completeness |

### Ping/Pong Test

```bash
./agentmail ping
```

Sends ping via SMTP to gateway to bridge to webhook, triggers auto-pong reply, verifies full loop. Expected output:

```
  Ping sent: __agentmail_ping__:a1b2c3d4e5f6
  +  1.2s    Webhook Receive (ping)         OK
  +  2.9s    Pong Sent (send_mail)          OK
  +  5.1s    Webhook Return (pong)          OK
  Total round-trip: 5.1s
  Full pipeline verified
```

---

## 4. aimail-bridge

### Process Management

```bash
# Status (process / config / route table / log freshness)
./agentmail bridge

# Restart (single instance)
./agentmail bridge --restart

# Refresh forward routes for one system
./agentmail bridge --system-id <sid>
```

### Config

`~/.agentmail/bridge/aimail_bridge.toml`:

```toml
mode = "pull"

[pull]
amail_url = "https://amail.token.tm"
admin_key = "***"
system_id = "system-xxxx"
poll_interval_sec = 5

[health]
check_interval_sec = 60
fail_threshold = 3
connect_timeout_sec = 3
```

### Dual Modes

| Mode | Use Case | Description |
|------|----------|-------------|
| `pull` | Hermes on internal network, gateway external | Bridge polls for pending emails |
| `push` | Hermes and gateway same network | Gateway pushes webhook directly (no bridge) |

---

## 5. Hermes Gateway

### Process Management

```bash
# Start root profile gateway
hermes gateway run --accept-hooks --replace

# Start named profile gateway
hermes -p {name} gateway run --accept-hooks --replace

# Status
hermes gateway status

# Ports
grep -A2 'webhook:' ~/.hermes/config.yaml
```

### Health Check

```bash
curl http://127.0.0.1:{port}/health
```

Root profile default port 8644, named profiles from 8645 sequentially.

---

## 6. Common Issues

### Ping test stuck on "pong not returned"

**Cause:** Pong email failed to loop back. Usually API key / email mismatch.

**Check:**
```bash
grep pong_status ~/.agentmail/mail/*/agentmail.log
```

**Fix:** Verify email and api_key match in `~/.agentmail/systems/{sid}/{addr}/agentmail.json`.

### Bridge cannot pull emails

**Check:**
```bash
./agentmail bridge
curl https://amail.token.tm/health
tail -20 ~/.agentmail/logs/aimail-bridge.log
```

### Gateway won't start

**Check:**
```bash
ss -tlnp | grep 8644
hermes gateway run --dry-run
cat ~/.hermes/gateway.log
```

### Re-integration

```bash
# Remove agentmail from the agent system (CLI, preserves ~/.agentmail/ local data)
./agentmail uninstall --system-id <sid> --yes

# Re-install
./agentmail install --home ~/.hermes --system-id <sid>
```

`agentmail install` is idempotent — re-runs skip completed steps.

### API Key Update

If gateway-side key is rotated or invalidated:

```bash
# Option 1: Clear activation_code and api_key in agentmail.json
# Option 2: Replace api_key directly
# Option 3: Re-run ./agentmail reset --system-id <sid>
```

---

## 7. CLI Reference

`./agentmail` is the single entry point (repo root, symlinked to `scripts/agentmail`).
Subcommands (alphabetical): `bridge`, `check`, `domain`, `install`, `mailname`,
`ping`, `reset`, `stats`, `uninstall`, `welcome`.

### Installation (key flow)

`.env` in the repo root is read automatically (CLI flag > shell env > .env
> built-in default), so repeated values only need to be set once:

```bash
# .env: AIMAIL_URL / AIMAIL_ADMIN_KEY | AIMAIL_PRODUCT_CODE / AIMAIL_MANAGER_ADDRESS

# New system — activate with a product code (from .env if not passed)
./agentmail install --home ~/.hermes --product-code <CODE> --manager admin@example.com

# Existing system — reuse the stored config or pass the admin key
./agentmail install --home ~/.hermes --system-id <sid>
./agentmail install --home ~/.openclaw --system-id <sid>
```

`install` runs the whole chain: system activation → bridge deploy → tool & skill
install → webhook patch & profile registration. Then verify:

```bash
./agentmail check                      # pipeline diagnostics
./agentmail ping                       # ping-pong round trip
./agentmail welcome                    # welcome e2e (mail to manager)
./agentmail stats                      # machine overview (systems/agents/mail counts)
```

### Day-to-day operations

```bash
./agentmail stats                      # systems installed + agents + mail stats
./agentmail domain --system-id <sid>   # list domains of a system
./agentmail domain --system-id <sid> --add example.com   # create a domain
./agentmail mailname --system-id <sid> --default NAME    # rename main agent
./agentmail reset --system-id <sid>    # re-run registration with stored admin key
./agentmail uninstall --system-id <sid> --yes            # remove the integration
./agentmail bridge --restart           # restart the local bridge
```

`--home` locates the platform root (`~/.hermes` / `~/.openclaw`); `--system-id`
is preferred when the platform cannot be auto-detected.
