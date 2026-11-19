---
name: agentmail-activation
description: "Bind an AIMail address when the user pastes the gateway activation prompt. Use aimail_address; never use CLI, files, or system onboarding."
version: 1.0.0
author: MeterCai
license: GPL-3.0
metadata:
  hermes:
    tags: [email, aimail, activation, binding]
    toolset: agentmail # contract-allowed: toolset dependency name
---

# AIMail Address Activation

This skill is used only when the user pastes the **address activation prompt**
from the AIMail gateway email. It is separate from the mail-handling skill.

## Input

From the activation prompt extract exactly:

```text
email
code            (addr-...)
gateway_url
```

The prompt may mention a safety officer/manager. That is information from the
gateway application; do **not** pass it to `aimail_address`.

## Action

Call exactly:

```text
aimail_address(
  action="bind",
  email=<email>,
  code=<code>,
  gateway_url=<gateway_url>
)
```

Do not add `system_code`, `manager`, `system_name`, `domain`, raw keys, or any
local system-onboarding call. There is no separate system-onboarding tool.

## Result handling

### success: true

- Binding is complete.
- `GatewaySystemId` and address credentials are persisted by the host.
- SSE should be armed by the host for the relevant gateway.
- Tell the user the address is activated and that the welcome mail will arrive
  through the normal mailbox stream.
- Do not invent a welcome message, a key, or a session.

### already_bound

If the result is:

```json
{"success": true, "reason": "already_bound"}
```

treat it as success:

- no gateway call was made;
- the code was not consumed;
- the key was not rotated;
- the binding was not modified.

Do not ask for a new code and do not report it as a failure.

### tool unavailable

If `aimail_address` is genuinely not registered in this session (unknown tool /
tool not found / not available), stop and tell the user that the AIMail toolset
must be enabled for this host. Do not use a CLI install path, do not write files
yourself, and do not retry in a loop.

### business failure

- 4xx (expired/used/mismatched code): do not retry; ask for a new activation code.
- 5xx/connection failure/timeout: retry once; if it still fails, report the error.
- parameter error: re-check `email`, `code`, `gateway_url` from the prompt once.
- `success: false` with a business reason: do not install anything; report it.

## Forbidden

- Do not create a binding by writing a file.
- Do not run a CLI or external installer.
- Do not use `manager`/system raw keys.
- Do not loop activation attempts.
- Do not claim success before the tool returns success.
- Do not conflate this skill with the mail-handling or board skill.
