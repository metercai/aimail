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

## L2 vs L3 (owner ruling 2026-11-21 — do not conflate)

| | **L2** | **L3** |
|---|---|---|
| plugin | installed BEFORE the test, from the **current development build** | none at start; the agent installs the **latest published** version during the test, guided by the activation prompt |
| purpose | test the build under review | test the published artifact + the agent's self-install journey |

The real-host dsh tool-surface gate (B4) is an L2 step: the dev plugin must be
installed into the profile the web server boots with, because the 15 mail tools
reach a session's tool surface ONLY through the plugin's `tool-mail` row (global
layer; `view()` seeds every scope from the global layer). A host booted without
the plugin in that profile runs model turns but exposes ZERO mail tools (B4).

```bash
# L2 real-host dsh tool-surface gate (no LLM, no gateway; pinned host from
# agent-env single source; dev source = repo tree / DSH_L2_DEV_TGZ / DSH_L2_DEV_DIR)
./tests/host-acceptance/run-l2-dsh-real-host.sh
# DSH_L2_LIVE=0 skips the live web-host probe (structural gate only).
# DSH_L2_CLEAN_ONLY=1 runs the negative control (gate MUST fail — CI self-check).
```

## Legacy L2

The old script remains the reference for push-era hosts and CLI install/register
regression. Do not delete it when changing the SSE profile; select the profile
explicitly in release automation.

## Pi / OpenClaw activation L2 (new)

Pi and OpenClaw now default to SSE; set `AIMAIL_TRANSPORT=push` (or Pi
`transport: 'push'`) only for legacy compatibility. Host-specific
activation-only L2 harnesses (no CLI, no listener):

```bash
GATEWAY_URL=... SYSTEM_CODE=... ADDRESS_CODE=... \
EMAIL=alice@test.local DOMAIN=test.local WORKSPACE=/tmp/workspace \
./tests/host-acceptance/run-pi-l2-activation.sh

GATEWAY_URL=... SYSTEM_CODE=... ADDRESS_CODE=... \
EMAIL=alice@test.local DOMAIN=test.local WORKSPACE=/tmp/workspace \
./tests/host-acceptance/run-openclaw-l2-activation.sh
```

They drive the real `aimail_system` + `aimail_address` host tools with a fake
host context, then assert the binding workspace and no `webhook_url`.

Set `DELIVERY=1` to also send a live gateway welcome and verify it arrives
through the host's SSE pull path into the binding workspace.


## Self-hosted gateway fixture (closed loop)

The host L2s can start the repository's existing gateway fixture and claim
their own system code from `/apply` (via Mailpit) — no external gateway or
pre-issued codes:

```bash
./tests/host-acceptance/run-l2-fixture.sh pi
./tests/host-acceptance/run-l2-fixture.sh openclaw
```

Each invocation resets the fixture volume, claims a fresh system code, runs
`aimail_system` + `aimail_address` (scenario-6 register) and the live welcome
check through the host SSE path.

Requires Docker; the local macOS workspace used for this session has no Docker,
so the script was syntax-checked but not executed here. The Linux QA fixture
has Docker.
