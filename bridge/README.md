# aimail-bridge

[🇨🇳 中文](README_zh.md)

**Zero ports, email inbound. One port, instant forwarding to all agents.**

A high-performance transparent bridge between [aimail-gateway](https://github.com/metercai/aimail-gateway)
and the per-agent webhook endpoints on [Hermes agents](https://github.com/nousresearch/hermes-agent).
Solves firewall penetration for heterogeneous multi-agent deployments with minimal
surface area.

---

## Why bridge

**Pain 1 — Multi-agent firewall penetration**: Each Hermes agent's webhook
runs on its own port (8645, 8646, …). Exposing them directly means N ports, N firewall
rules. Bridge's push mode provides a **single entry port** with auto-routing to every
webhook — open just one port, all agents instantly reachable. When TLS + ACME is
enabled, port 80 must also be opened (HTTP-01 challenge only).

**Pain 2 — Zero-dependency email inbound**: No public IP? No port forwarding? Pull mode
uses a single **outbound HTTP poll (every 10s)** — bridge actively fetches deliveries from
the gateway and fans out to local webhook ports. **Zero public ports** — loopback admin
API only, no external inbound mail traffic; complete NAT/firewall bypass.

---

## Key features

### Secure transparent pass-through

Bridge holds zero HMAC secrets. Gateway signs with each agent's webhook secret →
bridge forwards headers & body verbatim → agent verifies. Security boundary
unchanged. Push mode supports IP allowlist + blacklist + per-IP rate limiting;
pull mode uses ACK-based consumption with 2-hour dedup cache — zero message loss,
zero duplicates.

### Lightweight, pure Rust, zero OpenSSL

Single binary ~8 MB (stripped, fat LTO). < 10 MB memory at idle, near-zero CPU.
Pure Rust TLS stack — rustls with ring crypto. Zero OpenSSL, zero native-tls,
zero system dependency beyond libc.

### Efficient aggregated forwarding

When one email reaches multiple recipients behind the same bridge, the gateway
sends a **single body copy** with per-recipient headers — bridge fans out to
each webhook port. Batch body serialized once, reused across all entries.
Works for both push and pull modes.


### Security hardening

- **IP allowlist + blacklist** — push mode accepts POSTs only from trusted source IPs
- **Per-IP rate limiting** — configurable req/sec cap with sliding window (default 30)
- **Body size limit** — configurable cap (default 20 MB) prevents memory exhaustion
- **Header filtering** — only business headers forwarded: `x-aimail-email`
  (primary; required on push deliveries since v0.7.0 — missing returns 400; legacy
  `x-amail-email` accepted as alias), `x-webhook-signature`,
  `x-mailrelay-timestamp`, `content-type`)
- **Graceful shutdown** — SIGINT/SIGTERM drain in-flight requests
- **Connection pooling** — reqwest client reused across all forwards (keep-alive)
- **HSTS** — sends an HSTS header (end-to-end behavior subject to field verification)

### Zero-config automation

- **API route registration** — agents register their webhook via `POST /api/v1/routes`
- **inotify hot-reload** — changes to `aimail_routes.toml` are applied immediately
- **ACME auto-TLS** — set `hostname` → automatic Let's Encrypt certificate
  (HTTP-01 challenge), cached and auto-renewed every ~60 days
- **Dual-port mode** — `bind` port 80 + `hostname` set → auto 80→443 redirect
- **Daemon mode** — `--daemon` detaches from the terminal, runs in the background
  (default PID/log: `~/.hermes/aimail-bridge.pid` / `~/.hermes/aimail-bridge.log`)

---

## Two modes

### Push — one port, instant forwarding to all agents

```
                       ┌─────────────────────────────────┐
                       │         aimail-bridge             │
                       │  (single public port 38080)       │
gateway ──POST──►      │                                  │
  alice@...+bob@...    │  alice → 127.0.0.1:8645          │──► webhook:8645
  (one body copy)      │  bob   → 127.0.0.1:8646          │──► webhook:8646
                       │  carol → 127.0.0.1:8647          │──► webhook:8647
                       └─────────────────────────────────┘
```

- Gateway POSTs to a **single port** on bridge; bridge auto-routes by agent email
- Batch aggregation — multiple recipients → one body copy (see “Efficient aggregated forwarding”)
- TLS via rustls; automatic Let's Encrypt certificate when `hostname` is set
- Dual-port mode: `bind = "0.0.0.0:80"` + `hostname = "bridge.example.com"` → auto 80→443
- Real-time: gateway gets immediate HTTP response from agent via bridge

### Pull — zero ports, email inbound through NAT

```
gateway (public)                              behind NAT/firewall
  │                                               │
  │◄── POST /pending (poll every 10s) ────────────│ bridge (outbound only)
  │                                               │
  │── batches [{body, deliveries}] ──────────────►│
  │                                               │
  │                                 ┌─────────────▼──────────────────────┐
  │                                 │ fan-out to each agent webhook       │
  │                                 │ ACK forwarded deliveries            │
  │                                 └────────────────────────────────────┘
  │◄── POST /pending/ack ─────────────────────────│
```

- Single **outbound HTTP connection** to gateway, fully bypasses NAT/firewall
- **Zero public ports** — loopback admin API only, no external inbound mail traffic
- Same batch aggregation applies (see “Efficient aggregated forwarding”)
- ACK-based consumption + 2-hour dedup cache — no messages lost, no duplicates
- Exponential backoff on fetch failures (max 5 minutes)

---

## Quickstart

```bash
# Unzip the appropriate zip for your platform
VER=v0.7.0
ARCH=$(uname -m | sed 's/x86_64/amd64/;s/aarch64/arm64/')
unzip aimail-bridge-${VER}-linux-${ARCH}.zip
mv aimail-bridge-${VER}-linux-${ARCH} aimail-bridge
chmod +x aimail-bridge

# Push mode (single port, all agents)
cat > aimail_bridge.toml << 'EOF'
mode = "push"
bind = "0.0.0.0:38080"
hostname = "bridge.example.com"     # enables TLS + ACME auto-cert
admin_allowed_ips = ["127.0.0.1", "::1"]

[logging]
level = "info"       # stdout (default is /var/log/aimail-bridge.log without [logging]; non-root fails)

[push]
allowed_ips = ["10.0.0.0/8"]
EOF

# Pull mode (no public ports, outbound only)
cat > aimail_bridge.toml << 'EOF'
mode = "pull"
bind = "127.0.0.1:38080"

[logging]
level = "info"       # stdout (default is /var/log/aimail-bridge.log without [logging]; non-root fails)

[pull]
amail_url = "http://gateway.example.com:38080"
admin_key = "sk-xxxxxxxx"           # system-scope key (pending filtered by key's system)
system_id = "admin"
EOF

# Run
./aimail-bridge -c aimail_bridge.toml

# Check health
curl http://localhost:38080/health
# {"status":"ok","uptime_secs":42,"version":"0.7.0"}
```
## Configuration

### Push

```toml
mode = "push"
bind = "0.0.0.0:38080"                # listen address (default: "0.0.0.0:38080")
hostname = "bridge.example.com"       # public domain — enables TLS (see below)
admin_allowed_ips = ["127.0.0.1", "::1"]   # admin API whitelist (default: localhost)

# TLS: three ways — pick one
# 1) hostname + nothing        → ACME auto-cert (Let's Encrypt HTTP-01)
# 2) hostname + static certs   → use tls_cert / tls_key below
# 3) no hostname / IP hostname → plain HTTP
# tls_cert = "/etc/ssl/bridge.crt"   # static TLS cert (optional)
# tls_key  = "/etc/ssl/bridge.key"   # static TLS key (optional)
# acme_email = "admin@example.com"   # ACME contact (optional)
# acme_challenge_path = "/var/www/"  # challenge dir for external web server
                                     # (unset → bridge listens on port 80)

[push]
allowed_ips = ["10.0.0.0/8"]         # IP allowlist, empty = allow all (default: [])
blacklist_ips = ["1.2.3.4"]          # permanently blocked IPs (default: [])
rate_limit = 30                       # req/sec per source IP, 0 = disabled (default: 30)
body_limit_mb = 20                    # max request body in MB (default: 20)
```

### Pull

```toml
mode = "pull"
bind = "127.0.0.1:38080"              # listen address (admin API only)

[pull]
amail_url = "http://gateway.example.com:38080"
admin_key = "sk-xxxxxxxx"            # system-scope key — must belong to the
                                     # same system as the pending deliveries
system_id = "admin"                  # system ID for pending query (default: "admin")
poll_interval_sec = 10               # poll interval in seconds (default: 10)
```

### Pull — multi-system

One bridge serving several systems (the production shape: a single gateway
hosting multiple `shared-token-*` systems). Each entry polls its own
system's pending deliveries with its own key; `amail_url` may omit the
scheme (`http://` is added automatically).

```toml
mode = "pull"
bind = "127.0.0.1:38080"

[pull]
systems = [
  { amail_url = "https://amail.example.com", admin_key = "sk-aaa", system_id = "shared-token-aaaaaaaa", poll_interval_sec = 2 },
  { amail_url = "https://amail.example.com", admin_key = "sk-bbb", system_id = "shared-token-bbbbbbbb", poll_interval_sec = 2 },
]
```

### Logging

By default — no `[logging]` section in the config — logs are written to
`/var/log/aimail-bridge.log`; starting as non-root fails (panic) because that path
is not writable. To log to stdout, declare a `[logging]` section and leave `file` unset:

```toml
[logging]
level = "info"                        # log level (default: "info")
# file = "/tmp/aimail-bridge.log"     # log file path; unset → stdout
```



### Environment variables

| Variable | Equivalent config |
|---|---|
| `AIMAIL_BRIDGE_MODE` | `mode` |
| `AIMAIL_BRIDGE_HOSTNAME` | `hostname` (top-level) |
| `AIMAIL_GATEWAY_URL` | `pull.amail_url` |
| `AIMAIL_BRIDGE_ADMIN_KEY` | `pull.admin_key` |
| `AIMAIL_BRIDGE_SYSTEM_ID` | `pull.system_id` |
| `AIMAIL_BRIDGE_POLL_SECS` | `pull.poll_interval_sec` |
| `AIMAIL_BRIDGE_ALLOWED_IPS` | `push.allowed_ips` (comma-separated) |
| `HERMES_HOME` | Hermes home directory (default `~/.hermes`; equivalent to the top-level `hermes_home` config field) |
| `RUST_LOG` | tracing filter (overrides `logging.level`) |

---

## Network scenarios

| Scenario | Mode | Notes |
|---|---|---|
| gateway + agents on same machine | Push | Bridge proxies single port to local webhook ports |
| gateway public, agents behind NAT | Pull | Bridge polls gateway outbound, no public inbound ports |
| Bridge on public VPS | Push + TLS | `hostname = "bridge.example.com"`, ACME auto-cert, dual-port |


---

