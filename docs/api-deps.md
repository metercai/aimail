# API Dependencies Index

## Open

Credential-free endpoints (activate-address uses the activation code as
its credential; activate-system is public product-code activation).

| Endpoint                   | Method | Purpose                                      | Callers                                                                                                                                                                                                                   |
| -------------------------- | ------ | -------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  `/health`                   |  GET     |  Health check (advanced/base edition probing)  | CLI only: `core/checks/l1.rs:65` (L1 step 1.1), `core/repair.rs:57` (hint ladder) |
|  `/api/v1/activate-address`  |  POST    |  Activation code → API key                     | `pysdk/aimail_tools.py` (`activate_address`, `pysdk/aimail_base.py:3097` registration chain, `pysdk/hermes/aimail_hermes.py:751`, `pysdk/deer-flow/manage.py:295`) + `tssdk auto-bind.ts:231` |
|  `/api/v1/activate-address-code`  |  POST  |  Address-code → agent key (address-level self-service mailbox)  | `pysdk/aimail_tools.py` (`aimail_address` action=bind, `aimail_tools.py:1283` / `activate_address_code` :1343), CLI `cmd/agent.rs:1306-1320` (`agent bind` code path, no auth) + `tssdk address-code.ts:96/:161` (`activateAddressCode`/`activateAddressCodePersist`); **advanced-only** (aimail-advanced `src/advanced/api/address.rs`; flow documented in `docs/agent-self-setup.md`) |
|  `/api/v1/activate-system`   |  POST    |  Product-code system activation                | CLI only: `core/setup.rs:457` (`aimail install --system-only` — the sole activation implementation, L1-only); `pysdk/gateway_api.py:85` keeps a shared client method for the CLI (the SDK side must not call it directly — reverse-call ABI); **served by aimail-advanced** (advanced route `src/advanced/strategy.rs`; the base gateway has no such route) |

## Shared

| Endpoint             | Method | Purpose                          | Callers                                                                                                            |
| -------------------- | ------ | -------------------------------- | ------------------------------------------------------------------------------------------------------------------ |
|  `/api/v1/whoami`      |  GET     |  Verify API key identity & scopes  | CLI only: `core/gateway.rs:175` (used by `core/checks/l1.rs:137` key-scope check, `core/setup.rs:65/:321/:358` identity alignment); `pysdk/gateway_api.py:118` keeps the shared client method |
| `/api/v1/key/rotate` | POST   | Rotate own key                   | Gateway/admin side (aimail-gateway `src/core/api/http.rs` has the route + implementation; no Python/TS SDK caller) |

## Agent

Identity from key, scoped to self.

| Endpoint                       | Method     | Purpose                                                             | Callers                                                                                                                                                                                                                                                                             |
| ------------------------------ | ---------- | ------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  `/api/v1/send`                  |  POST        |  Send email                                                           | `pysdk/aimail_tools.py` (`send_mail`) + `tssdk gateway.ts:291` (`sendMail`) |
|  `/api/v1/upload`                |  POST        |  Upload attachment                                                    | `pysdk/aimail_tools.py` (`upload_attachment`) + `tssdk gateway.ts:319` (`uploadAttachment`) |
|  `/api/v1/attachments/:id`       |  GET         |  Download attachment                                                  | `pysdk/aimail_tools.py` (`download_attachment`) + `tssdk gateway.ts:343` (`downloadAttachment`) |
|  `/api/v1/stats/agent/me`        |  GET         |  Self statistics                                                      | No SDK caller anymore (the old polling dropped with the CLI's Rust port — welcome now uses `POST /api/v1/system/welcome`); **advanced-only endpoint** (aimail-advanced `src/advanced/strategy.rs`, no base-gateway route) |
|  `/api/v1/agent-state/:key`      |  GET/PUT     |  Agent KV storage                                                     | `pysdk/aimail_tools.py` (`agent_state_put` :364) + `tssdk gateway.ts:375` (`agentStatePut`) — PUT only on the SDK side (the GET route is unused by SDKs) |
|  `/api/v1/contacts/:address`     |  GET/PUT     |  Contact profile CRUD                                                 | `pysdk/aimail_tools.py` (`get_contact`/`put_contact`) + `tssdk gateway.ts:383/:379` (`contactGet`/`contactPut`) |
|  `/api/v1/contacts?name=`        |  GET         |  Search contacts by name                                              | `pysdk/aimail_tools.py` (`get_contacts_by_name`/`manage_contacts`) + `tssdk gateway.ts:387` (`contactSearch`) |
|  `/api/v1/contacts?addresses=`   |  GET         |  Bulk profile lookup (single round-trip, B1 injection)                | `pysdk/aimail_tools.py` (`get_contact_profiles`; inbound preprocessor `pysdk/aimail_base.py` B1 bulk profiles), `tssdk gateway.ts` + `preprocess.ts` step 10; the gateway's `GET /api/v1/contacts` serves both on the same route (handler `get_contacts_by_name`, cap 50) |
| `/api/v1/whitelists`           | GET/POST   | List/create whitelist (agent: own; agent\_admin: scoped)            | No SDK caller (neither the Python SDK nor the TS SDK exposes a list/create client method) → gateway/admin side                                                                                                                                                                         |
|  `/api/v1/whitelists/check?...`  |  GET         |  Whitelist lookup                                                     | `pysdk/aimail_tools.py` (`check_whitelist_value`) + `tssdk gateway.ts:360` (`checkWhitelist`) |
| `/api/v1/whitelists/:id`       | PUT/DELETE | Update/delete whitelist by id                                       | No SDK client (SDK always uses the composite-key form, next row; deregistration cleanup deletes by value) → gateway-side only                                                                                                                                                       |
|  `/api/v1/whitelists`            |  PUT/DELETE  |  Update/delete whitelist by composite key (`?domain_addr=`&`value=`)  | `pysdk/aimail_tools.py` (`update/delete_whitelist_by_value`; `deregister_agent_email` cleans by value) + `tssdk gateway.ts:365/:370` (`setWhitelist`/`deleteWhitelist`) |

## Admin

Pass the target email when operating on others.

| Endpoint                               | Method   | Purpose                                                                                                | Callers                                                                                                                                                                                                              |
| -------------------------------------- | -------- | ------------------------------------------------------------------------------------------------------ | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
|  `/api/v1/admin/api-keys?email=`         |  GET       |  Lookup API key by email                                                                                 | `pysdk/aimail_tools.py` (`get_api_key_by_email`; deregistration chain `pysdk/aimail_base.py:3107` goes through it) + `tssdk sdk-ops.ts:140` (`getApiKeyByEmail`) |
|  `/api/v1/admin/api-keys`                |  POST      |  Create API key                                                                                          | CLI only: `core/gateway.rs:194` `create_api_key` (called by `core/setup.rs:388` activation key mint, `core/bridge_deploy.rs:1094` bridge key) — `pysdk/gateway_api.py:133` keeps the shared client method for the CLI |
|  `/api/v1/admin/api-keys/:id`            |  DELETE    |  Delete any key                                                                                          | `pysdk/aimail_tools.py` (`delete_api_key`; deregistration chain `pysdk/aimail_base.py:3107`) + `tssdk sdk-ops.ts:147` (`deleteApiKey`) |
|  `/api/v1/admin/systems/:sid/domains`    |  GET/POST  |  System domain CRUD                                                                                      | CLI `cmd/domain.rs:59/:74` (`aimail domain` list / `--add`), `core/setup.rs` (activation domain creation) + `pysdk/aimail_base.py` (`list_system_domains` in registration/deregistration chains :3084/:3129) + `tssdk auto-bind.ts:347` |
|  `/api/v1/admin/systems/:sid/addresses`  |  POST      |  Register agent email address                                                                            | `pysdk/aimail_base.py:2971` (`register_agent_email` chain — hermes `pysdk/hermes/aimail_hermes.py` / deerflow `pysdk/deer-flow/manage.py` go through it; the door op `register_agent_email` exposes the same chain), CLI `cmd/agent.rs` (`agent bind`) + `tssdk auto-bind.ts:198` |
|  `/api/v1/admin/systems/:sid/addresses/rename`  |  POST  |  Rename a registered agent address (server-side resources fully inherited)  | SDK CRUD: `pysdk/aimail_base.py:585` `rename_address` (POST :638) / `tssdk sdk-ops.ts:610` (`update(action=rename)`); the CLI `address -n` only triggers the door op and never assembles names or takes intermediate values (contract §4.1) |
|  `/api/v1/admin/system-domains/:id`      |  PUT       |  Update domain settings                                                                                  | `pysdk/aimail_tools.py` (`update_system_domain`; registration chain `pysdk/aimail_base.py` updates webhooks through it) + `tssdk auto-bind.ts:357` |
| `/api/v1/admin/domains/check?domain=`  | GET      | Check domain uniqueness                                                                                | No local caller (no aimail repo SDK/CLI call) → gateway-side (aimail-gateway `src/core/api/http.rs` `check_domain_exists`) or delete                                                                                 |
| `/api/v1/admin/agent-meta/:email`      | PUT      | Update agent metadata                                                                                  | Gateway admin endpoint (aimail-gateway repo: route in `http.rs` + `update_agent_meta`, 4 hits; no SDK caller in this repo)                                                                                           |
|  `/api/v1/system/welcome`                |  POST      |  System welcome mail (server-fixed subject/body, sole param `to`; scope = platform/system/agent\_admin)  | CLI only: `core/welcome.rs:54-62` (`aimail welcome`, admin-key signed) — the endpoint replacing the stats polling in welcome tests (base gateway `src/core/api/http.rs`) |
|  `/api/v1/admin/pending`                 |  POST      |  Bridge pulls pending mail / redelivery retry                                                            | CLI `core/repair.rs:909` (evidence query), `:1464` (`--deep` drain-stuck), `cmd/check.rs` (L5 ping polling); aimail-bridge remote (pull: SSE stream with long-poll fallback, see aimail-bridge `src/pull.rs`) |
|  `/api/v1/admin/pending/ack`             |  POST      |  Bridge acknowledges delivered emails                                                                    | CLI `core/repair.rs:1041` (ack delivered / stuck-pending drain); aimail-bridge remote (ACK of forwarded deliveries) |
|  `/api/v1/admin/renew-system`            |  POST      |  Renew a system with a product code (additive `max(now, expiry) + validity`)                             | CLI only: `cmd/renew.rs:88` (`aimail renew -c <code>`); **advanced-only** (aimail-advanced `src/advanced/strategy.rs`; the base gateway has no such route) |
|  `/api/v1/quotas`                        |  GET       |  Read-only system quota / expiry view                                                                    | CLI only: `cmd/renew.rs:39` (`renew --status`), `cmd/stats.rs:103` (`stats -a` cloud status); **advanced-only** (aimail-advanced `src/advanced/strategy.rs`) |

## Bridge

Local aimail-bridge API.

| Endpoint         | Method | Purpose                                          | Callers                                                                           |
| ---------------- | ------ | ------------------------------------------------ | --------------------------------------------------------------------------------- |
|  `/api/v1/routes`  |  POST    |  Register agent inbound route (idempotent upsert)  | CLI only: `core/bridge_wire.rs:404` (`sync_route`) / `:409` (withdraw), driven by `cmd/install.rs:893` / `cmd/reset.rs:220` (`ensure_inbound_routes`) and `aimail address --inbound-live|--inbound-down` — the SDK has no bridge calls (zero-bridge ratchet) |

## Board

Auth: board API credentials are per-member tokens (Bearer auth — not the API key). Credentials arrive via the invite mail —
Subject `[A2A] invite: {board}`, body carries the API URL + personal token — and the agent stores them locally for the SDK board calls.

| Endpoint                                | Method | Purpose                        | Callers                                                    |
| --------------------------------------- | ------ | ------------------------------ | ---------------------------------------------------------- |
|  `/api/v1/board/:id/tasks`                |  GET     |  List board tasks                | `pysdk/aimail_board.py` (`board_task_list`) + `tssdk gateway.ts:438` (`boardTaskList`) |
|  `/api/v1/board/:id/task/:tid`            |  GET     |  Get task details                | `pysdk/aimail_board.py` (`board_task_show`) + `tssdk gateway.ts:430` (`boardTaskShow`) |
|  `/api/v1/board/:id/members`              |  GET     |  List board members              | `pysdk/aimail_board.py` (`board_members`) + `tssdk gateway.ts:443` (`boardMembers`) |
|  `/api/v1/board/:id/roles`                |  GET     |  List role permissions           | `pysdk/aimail_board.py` (`board_roles`) + `tssdk gateway.ts:448` (`boardRoles`) / `board.ts:163` (tool `board_roles`) |
|  `/api/v1/board/:id/status`               |  GET     |  Board pipeline + dependencies   | `pysdk/aimail_board.py` (`board_status`) + `tssdk gateway.ts:426` (`boardStatus`) |
|  `/api/v1/board/:id/task/:tid/heartbeat`  |  POST    |  Task heartbeat (Ready→Running)  | `pysdk/aimail_board.py` (`board_heartbeat`) + `tssdk gateway.ts:451-454` (`boardHeartbeat`) |

