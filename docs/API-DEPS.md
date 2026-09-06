# API Dependencies Index

> Authoritative endpoint sources: `tssdk/packages/mail-core/src/gateway.ts`
> (TS SDK client) + the aimail-gateway route table (base:
> `src/core/api/http.rs`; advanced-only endpoints live in the
> aimail-advanced repo, `src/advanced/strategy.rs`). This index is the
> SDK dependency view.
>
> Thread summaries went local (2026-08-24): conversation digests are
> stored on the agent at `threads/{first-two}/{thread_id}.json`, message
> metadata at `meta/{first-two}/{mid}.json` (written on every message) —
> no gateway endpoint involved anymore.

## Open

Credential-free endpoints (activate-address uses the activation code as
its credential; activate-system is public product-code activation).

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/health` | GET | Health check (advanced/base edition probing) | `cli/check_status.py`, `cli/ping_test.py`, `cli/send_welcome.py` |
| `/api/v1/activate-address` | POST | Activation code → API key | `pysdk/aimail_tools.py` (`activate_address`; registration chain `pysdk/aimail_base.py`, `pysdk/hermes/aimail_hermes.py` go through it) + `tssdk mail-core auto-bind.ts` |
| `/api/v1/activate-system` | POST | Product-code system activation | `pysdk/gateway_api.py` (`activate_system`, public no-auth), `pysdk/aimail_tools.py`, `cli/setup_system.py`; **served by aimail-advanced** (advanced route `src/advanced/strategy.rs`; the base gateway has no such route) |

## Shared

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/whoami` | GET | Verify API key identity & scopes | `pysdk/gateway_api.py` (`whoami`; reused by the OpenClaw adapter `pysdk/openclaw/amail_base.py`) |
| `/api/v1/key/rotate` | POST | Rotate own key | Gateway/admin side (aimail-gateway `src/core/api/http.rs` has the route + implementation; no Python/TS SDK caller) |

## Agent

Identity from key, scoped to self.

> ~~`/api/v1/inbox` (GET), `/api/v1/inbox/ack` (POST)~~ **retired** —
> inbound pull now goes through aimail-bridge `POST /api/v1/admin/pending`
> (the gateway `src/` has no `/api/v1/inbox` route; zero SDK hits).

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/send` | POST | Send email | `pysdk/aimail_tools.py` (`send_mail`) + `tssdk mail-core gateway.ts` |
| `/api/v1/upload` | POST | Upload attachment | `pysdk/aimail_tools.py` (`upload_attachment`) + `tssdk mail-core gateway.ts` |
| `/api/v1/attachments/:id` | GET | Download attachment | `pysdk/aimail_tools.py` (`download_attachment`) + `tssdk mail-core gateway.ts` |
| `/api/v1/stats/agent/me` | GET | Self statistics | No SDK caller anymore (old `cli/send_welcome.py` polling dropped; welcome now uses `POST /api/v1/system/welcome`); **advanced-only endpoint** (aimail-advanced `src/advanced/strategy.rs`, no base-gateway route) |
| `/api/v1/agent-state/:key` | GET/PUT | Agent KV storage | `pysdk/aimail_tools.py` (`agent_state_get/put`) + `tssdk mail-core gateway.ts` |
| `/api/v1/contacts/:address` | GET/PUT | Contact profile CRUD | `pysdk/aimail_tools.py` (`get_contact`/`put_contact`) + `tssdk mail-core gateway.ts` |
| `/api/v1/contacts?name=` | GET | Search contacts by name | `pysdk/aimail_tools.py` (`get_contacts_by_name`/`manage_contacts`) + `tssdk mail-core gateway.ts` |
| `/api/v1/contacts?addresses=` | GET | Bulk profile lookup (single round-trip, B1 injection) | `pysdk/aimail_tools.py` (`get_contact_profiles`; inbound preprocessor `pysdk/aimail_base.py` B1 bulk profiles), `tssdk mail-core gateway.ts` + `preprocess.ts` step 10; the gateway's `GET /api/v1/contacts` serves both on the same route (handler `get_contacts_by_name`, cap 50) |
| `/api/v1/whitelists` | GET/POST | List/create whitelist (agent: own; agent_admin: scoped) | No SDK caller (neither Python SDK nor TS mail-core exposes list/create client methods) → gateway/admin side |
| `/api/v1/whitelists/check?...` | GET | Whitelist lookup | `pysdk/aimail_tools.py` (`check_whitelist_value`) + `tssdk mail-core gateway.ts` |
| `/api/v1/whitelists/:id` | PUT/DELETE | Update/delete whitelist by id | No SDK client (SDK always uses the composite-key form, next row; deregistration cleanup deletes by value) → gateway-side only |
| `/api/v1/whitelists` | PUT/DELETE | Update/delete whitelist by composite key (`?domain_addr=`&`value=`) | `pysdk/aimail_tools.py` (`update/delete_whitelist_by_value`; `deregister_agent_email` cleans by value) + `tssdk mail-core gateway.ts` |

## Admin

Pass the target email when operating on others. Scope is checked via `require_domain_match`.

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/admin/api-keys?email=` | GET | Lookup API key by email | `pysdk/aimail_tools.py` (`get_api_key_by_email`; deregistration chain `pysdk/aimail_base.py` goes through it) |
| `/api/v1/admin/api-keys` | POST | Create API key | `cli/setup_system.py`, `cli/deploy_bridge.py` (via `pysdk/gateway_api.py` `create_api_key`) |
| `/api/v1/admin/api-keys/:id` | DELETE | Delete any key | `pysdk/aimail_tools.py` (`delete_api_key`; deregistration chain `pysdk/aimail_base.py`, `cli/bin/deregister_agent.py`) |
| `/api/v1/admin/systems/:sid/domains` | GET/POST | System domain CRUD | `cli/aimail` (`domain` subcommand: list via `pysdk/aimail_tools.py` `list_system_domains`, `--add` POSTs directly), `pysdk/aimail_base.py` (preprocess/deregistration read domains) + `tssdk mail-core auto-bind.ts` |
| `/api/v1/admin/systems/:sid/addresses` | POST | Register agent email address | `pysdk/aimail_tools.py` (`register_email`; registration chain `register_agent_email` → `cli/bin/register_agent.py`, `pysdk/hermes/register_profiles.py`) + `tssdk mail-core auto-bind.ts` |
| `/api/v1/admin/system-domains/:id` | PUT | Update domain settings | `pysdk/aimail_tools.py` (`update_system_domain`; registration chain `pysdk/aimail_base.py` updates webhooks through it) + `tssdk mail-core auto-bind.ts` |
| `/api/v1/admin/domains/check?domain=` | GET | Check domain uniqueness | No local caller (no aimail repo SDK/CLI call) → gateway-side (aimail-gateway `src/core/api/http.rs` `check_domain_exists`) or delete |
| `/api/v1/admin/agent-meta/:email` | PUT | Update agent metadata | Gateway admin endpoint (aimail-gateway repo: route in `http.rs` + `update_agent_meta`, 4 hits; no SDK caller in this repo) |
| `/api/v1/system/welcome` | POST | System welcome mail (server-fixed subject/body, sole param `to`; scope = platform/system/agent_admin) | `cli/send_welcome.py` (`_api_send`, admin-key signed; `aimail welcome` goes through it) — the endpoint replacing the stats polling in welcome tests (base gateway `src/core/api/http.rs`) |
| `/api/v1/admin/pending` | POST | Bridge pulls pending mail / redelivery retry | `cli/repair.py` (flush pending/retry), `cli/check_status.py` (L5 ping polling), aimail-bridge remote (pull polling, see aimail-bridge `src/pull.rs`) |
| `/api/v1/admin/pending/ack` | POST | Bridge acknowledges delivered emails | `cli/repair.py` (ack delivered) |

## Bridge

Local aimail-bridge admin API (not the cloud gateway).

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/routes` | POST | Register agent inbound route (idempotent upsert) | `pysdk/aimail_base.py` (`register_bridge_route`, always called in the registration chain) + `cli/bin/register_agent.py` + `tssdk mail-core auto-bind.ts` |

## Board

Auth: board API credentials are per-member tokens (`board_members.board_token`,
Bearer auth — not the API key). Credentials arrive via the invite mail —
Subject `[A2A] invite: {board}`, body carries the API URL + personal token —
and the agent stores them locally for the SDK board calls.

| Endpoint | Method | Purpose | Callers |
|------|--------|---------|---------|
| `/api/v1/board/:id/tasks` | GET | List board tasks | `pysdk/aimail_board.py` + `tssdk mail-core gateway.ts` |
| `/api/v1/board/:id/task/:tid` | GET | Get task details | `pysdk/aimail_board.py` + `tssdk mail-core gateway.ts` |
| `/api/v1/board/:id/members` | GET | List board members | `pysdk/aimail_board.py` + `tssdk mail-core gateway.ts` |
| `/api/v1/board/:id/roles` | GET | List role permissions | `pysdk/aimail_board.py` (TS mail-core does not wrap roles) |
| `/api/v1/board/:id/status` | GET | Board pipeline + dependencies | `pysdk/aimail_board.py` + `tssdk mail-core gateway.ts` |
| `/api/v1/board/:id/task/:tid/heartbeat` | POST | Task heartbeat (Ready→Running) | `pysdk/aimail_board.py` + `tssdk mail-core gateway.ts` |
