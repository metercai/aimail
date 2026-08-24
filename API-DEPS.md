# API Dependencies Index

> thread-summary 已本地化(2026-08-24): 会话摘要存 agent 本地 `threads/{前两位}/{thread_id}.json`,
> 消息元数据存 `meta/{前两位}/{mid}.json`(常写), 不再依赖 gateway 端点。

## Open

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/health` | GET | Health check | `integrate.sh`, `scripts/check_status.py`, `scripts/hermes_gateway.sh` |
| `/api/v1/activate-address` | POST | Activation code → API key | `tools/aimail_tools.py` |

## Shared

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/whoami` | GET | Verify API key identity & scopes | `scripts/deploy_bridge.py`, `scripts/check_status.py`, `integrate.sh` |
| `/api/v1/key/rotate` | POST | Rotate own key | `tools/aimail_tools.py` |

## Agent

Identity from key, scoped to self.

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/send` | POST | Send email | `tools/aimail_tools.py` |
| `/api/v1/upload` | POST | Upload attachment | `tools/aimail_tools.py` |
| `/api/v1/attachments/:id` | GET | Download attachment | `tools/aimail_tools.py` |
| `/api/v1/inbox` | GET | Pull own pending deliveries | `tools/aimail_tools.py` |
| `/api/v1/inbox/ack` | POST | Acknowledge delivery receipt | `tools/aimail_tools.py` |
| `/api/v1/stats/agent/me` | GET | Self statistics | `scripts/send_welcome.py` |
| `/api/v1/agent-state/:key` | GET/PUT | Agent KV storage | `tools/aimail_tools.py` |
| `/api/v1/contacts/:address` | GET/PUT | Contact profile CRUD | `tools/aimail_tools.py` |
| `/api/v1/contacts?name=` | GET | Search contacts by name | `tools/aimail_tools.py` |
| `/api/v1/whitelists` | GET/POST | List/create whitelist (agent: own; agent_admin: scoped) | `tools/aimail_tools.py` |
| `/api/v1/whitelists/check?...` | GET | Whitelist lookup | `tools/aimail_tools.py` |
| `/api/v1/whitelists/:id` | PUT/DELETE | Update/delete whitelist by id | `tools/aimail_tools.py` |
| `/api/v1/whitelists` | PUT/DELETE | Update/delete whitelist by composite key (?domain_addr=&value=) | `tools/aimail_tools.py` |

## Admin

Pass target email when operating on others. Scope checked via `require_domain_match`.

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/admin/api-keys?email=` | GET | Lookup API key by email | `tools/aimail_tools.py` |
| `/api/v1/admin/api-keys` | POST | Create API key | `scripts/deploy_bridge.py` |
| `/api/v1/admin/api-keys/:id` | DELETE | Delete any key | `tools/aimail_tools.py` |
| `/api/v1/admin/systems/:sid/domains` | GET/POST | System domain CRUD | `scripts/list_domains.py`, `integrate.sh`, `tools/aimail_tools.py` |
| `/api/v1/admin/systems/:sid/addresses` | POST | Register agent email address | `tools/aimail_tools.py` |
| `/api/v1/admin/system-domains/:id` | PUT | Update domain settings | `tools/aimail_tools.py` |
| `/api/v1/admin/domains/check?domain=` | GET | Check domain uniqueness | `scripts/helpers.sh` |
| `/api/v1/admin/agent-meta/:email` | PUT | Update agent metadata | Gateway admin |
| `/api/v1/admin/pending` | POST | Bridge push pending emails | `scripts/check_status.py` |
| `/api/v1/admin/pending/ack` | POST | Bridge acknowledge delivered emails | `scripts/check_status.py` |

## Bridge

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/routes` | POST | Register agent inbound route | `scripts/hermes_gateway.sh` |

## Board

Auth: `Authorization: Bearer <board_token>`（来自 `notify_invite`).

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/board/:id/tasks` | GET | List board tasks | `tools/aimail_board.py` |
| `/api/v1/board/:id/task/:tid` | GET | Get task details | `tools/aimail_board.py` |
| `/api/v1/board/:id/members` | GET | List board members | `tools/aimail_board.py` |
| `/api/v1/board/:id/roles` | GET | List role permissions | `tools/aimail_board.py` |
| `/api/v1/board/:id/status` | GET | Board pipeline + dependencies | `tools/aimail_board.py` |
| `/api/v1/board/:id/task/:tid/heartbeat` | POST | Task heartbeat (Ready→Running) | `tools/aimail_board.py` |
