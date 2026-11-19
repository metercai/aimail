---
name: agentmail-activation
description: "AIMail address lifecycle: bind via activation prompt, calibrate identity, pause/resume/unbind/move."
version: 2.0.0
author: MeterCai
license: GPL-3.0
metadata:
  hermes:
    tags: [email, aimail, activation, binding, lifecycle]
    toolset: agentmail # contract-allowed: toolset dependency name
---

# AIMail Address Activation & Lifecycle

Trigger phrase: **AIMAIL-ACTIVATE**

Use this skill when the user pastes an AIMail activation prompt or asks to
pause/resume/unbind/move an AIMail address. This skill is separate from the
mail-handling skill and the future board skill.

## 1. Preconditions

The AIMail toolset must expose:

- `aimail_address`
- `role_calibrator`
- normal mail tools

If `aimail_address` is genuinely absent from this session:

1. ask the user to install/enable the AIMail plugin for this host using the
   host's normal plugin/extension mechanism;
2. do **not** use a separate `aimail install` CLI, shell installer, or file
   writes;
3. after the toolset is enabled, continue only once.

Do not conclude the tool is missing from memory; only from an explicit
unknown-tool/not-registered result or the host tool list.

## 2. Bind

From the activation prompt extract:

```text
email
code        (addr-...)
gateway_url
```

Call exactly:

```text
aimail_address(
  action="bind",
  email=<email>,
  code=<code>,
  gateway_url=<gateway_url>
)
```

Do not pass `system_code`, `manager`, `system_name`, `domain`, raw keys, or a
local session id. There is no separate system-onboarding step.

### Result branches

- `success: true` → continue to identity calibration.
- `success: true, reason: "already_bound"` → local short-circuit; the code was
  not consumed, key not rotated, binding unchanged. Continue to identity
  calibration; do not ask for a new code.
- tool unavailable → see §1; bounded installation, never loop.
- 4xx (expired/used/mismatch) → no retry; ask for a fresh activation code.
- 5xx/connection/timeout → retry once, then report.
- parameter error → re-read the prompt once; then ask the user.
- `success: false` business error → do not install anything; report.

## 3. Identity calibration

Every activated address must be calibrated.

Call:

```text
role_calibrator()
```

The tool sends a welcome mail. Follow the welcome mail instructions and reply
in the same thread. The welcome body defines:

- persona;
- signature;
- current_time;
- manager approval command (for example `[CMD] approve persona`).

Completion signal: a notification/message titled approximately:

```text
[AIMail] Persona applied
```

Only then is the address calibrated.

Rules:

- do not rewrite the welcome body's persona/signature/time lines;
- reply in the same thread; do not start an unrelated thread;
- if `[AIMail] Persona applied` does not arrive, report to the user;
- do **not** re-trigger `role_calibrator` yourself;
- do not claim calibration before the completion signal.

## 4. Lifecycle actions

### pause

```text
aimail_address(action="pause", email=<email>)
```

- stops local delivery/aggregation for that address;
- new mail is not acked;
- gateway credentials are unchanged.

### resume

```text
aimail_address(action="resume", email=<email>)
```

- re-enables delivery;
- drains pending deliveries for the address;
- do not ask for a new activation code.

### unbind

```text
aimail_address(action="unbind", email=<email>)
```

- removes local routing and stops aggregation for the address;
- archives local binding/credentials;
- clears the pointer only if it points at this address;
- does **not** delete the gateway-side key; remote deregistration is separate;
- pending mail is not acked.

### move

```text
aimail_address(action="move", email=<email>, workspace_path=<path>)
```

- same AgentSystemId, new Agent workspace;
- no new activation code;
- no new SSE;
- future mail routes to the new workspace;
- existing Sessions are not migrated;
- the target workspace must exist.

Moving to another Gateway or another AgentSystemId requires unbind + fresh
activation; never copy a binding across instances.

## 5. Error classification

| Result | Action |
|---|---|
| tool unknown/not registered | enable toolset once; retry once |
| 4xx expired/used/mismatch | new code needed; no retry |
| 5xx/connection/timeout | retry once, then stop |
| parameter error | re-read prompt once |
| business `success:false` | report; never install |
| calibration signal missing | report; never re-trigger |

## 6. Forbidden

- writing a binding file directly;
- calling `aimail install` or any shell installer;
- passing manager/raw keys;
- using `system_code`;
- looping activation or installation;
- creating a second address on a single-address host;
- claiming success/calibration without tool/signal evidence.
