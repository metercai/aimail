# DSH L2 profiles

Two L2 profiles coexist during the SSE transition:

| Profile | Entry | Scope |
|---|---|---|
| **Legacy L2 (push/CLI)** | `aimail-advanced/tests/SDK/docker-regression/hosts/run-host-regression.sh` | CLI `aimail install`, `register-cli`, push/`webhook_url`, local inbound endpoint, HMAC delivery. Kept unchanged for old hosts. |
| **New L2 (SSE, no CLI)** | `tests/dsh-acceptance/scripts/run-dsh-l2-sse.sh` | Agent-facing `aimail_system` + `aimail_address`, workspace binding, SSE pending delivery. No `aimail` CLI, no `register-cli`, no listener/webhook. |

The new profile follows [`docs/aimail-host-sse-contract.md`](../../docs/aimail-host-sse-contract.md).

## New L2: what it runs

```bash
cd tests/dsh-acceptance
GATEWAY_URL=http://127.0.0.1:18080 \
SYSTEM_CODE=sys-... \
ADDRESS_CODE=addr-... \
EMAIL=alice@test.local \
DOMAIN=test.local \
WORKSPACE=/tmp/l2-workspace \
./scripts/run-dsh-l2-sse.sh
```

For a shared-domain system use `SYSTEM_NAME` instead of `DOMAIN`.

Steps:

1. `aimail_system bind` (real gateway, no CLI) → system config + default worker.
2. `aimail_address bind` with the current `WORKSPACE` → binding.
3. Assert binding `workspace_path` equals the activation workspace and has no
   `webhook_url` / `session_id`.
4. Start `mail-hub`; send a real gateway welcome; assert the fake Agent session
   receives a followup in the same workspace through SSE.

It covers contract C1/C3/C5/C6 (system onboarding, address activation,
workspace preservation, welcome SSE → session). It deliberately does not use a
real LLM; L3 remains the live-LLM end-to-end profile.

## Legacy L2

The old script remains the reference for push-era hosts and CLI install/register
regression. Do not delete it when changing the SSE profile; select the profile
explicitly in release automation.
