# AIMail Self-Setup — Agent-native mail (AI Agent quick start)

> Instructions written FOR the agent that is about to install AIMail on its
> own host. Follow each step exactly; run commands in your terminal tool.
>
> Division of labor (by design — AIMail deep-integrates into the host, this
> is its capability difference vs thin-skill mail tools):
> - **You (the agent) do ALL the preparation**: download, install, patch,
>   bind, pre-check. Nothing is left half-done.
> - **The user does exactly ONE thing at the end**: restart the host
>   process so the injected runtime loads. Restarting the process you run
>   inside is never something you do yourself.

## Step 1 — Install / update the CLI

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

Installs the `aimail` CLI into `~/.local/bin` and runs the machine-level
init (main dir `~/.aimail`, disk headroom, local-gateway vs bridge
decision). Writes stay inside `~/.aimail` and `~/.local/bin` — no sudo.
Re-running = upgrade. Verify:

```bash
aimail version
```

If `aimail: command not found`: re-open the shell or add `~/.local/bin` to
PATH (`export PATH="$HOME/.local/bin:$PATH"`).

## Step 2 — Obtain the credential (user provides values, you never guess)

AIMail has no OAuth — the authorization equivalent is a credential the
user supplies. Ask for:

- **New system** — activation code: `AIMAIL_PRODUCT_CODE` (management page)
- **Existing system** — admin key: `AIMAIL_ADMIN_KEY` (or an existing
  `~/.aimail/.system_raw_key/*_admin.key`)
- **Always** — manager mailbox: `AIMAIL_MANAGER_ADDRESS`

Then export in your shell:

```bash
export AIMAIL_URL=https://amail.token.tm
export AIMAIL_MANAGER_ADDRESS=<user mailbox>
export AIMAIL_PRODUCT_CODE=<code>          # or: export AIMAIL_ADMIN_KEY=<key>
```

## Step 3 — Detect the host platform

Exactly one of these matches this machine:

```bash
ls -d ~/.dsh ~/.openclaw ~/.pi ~/.hermes 2>/dev/null; ls -d ~/deepseek-harness 2>/dev/null
# deer-flow host: a dir containing backend/app/gateway
```

## Step 4 — Install the SDK adapter (all preparation, done by you)

`aimail install --home <platform root>` activates (or reuses) the system,
deploys the bridge entry, installs the platform adapter (patches /
skills / plugin), registers and auto-binds the agent:

```bash
aimail install --home ~/.hermes        # Hermes
# aimail install --home ~/.openclaw    # OpenClaw
# aimail install --home ~/.pi          # pi
# aimail install --home ~/.dsh         # dsh
# aimail install --home <backend-dir>  # deer-flow
```

Idempotent — re-running is safe. Capture the printed **system_id** (or
find it under `~/.aimail/systems/` — the dir containing
`aimail_gateway.json`).

Then PRE-CHECK — prove everything is ready and only the restart is left:

```bash
aimail check --system-id <system_id>
```

Expected state: config/runtime layers green (patches applied, plugin
installed, resources in place); the hook probe may still FAIL — that
FAIL on the not-yet-restarted process is exactly the evidence that only
the restart remains. Report this state to the user, then go to Step 5.

## Step 5 — Restart the host (the ONLY user action, outside this session)

Tell the user, with the exact command for their platform:

| Platform | After your install, the user runs |
|----------|-----------------------------------|
| Hermes | `systemctl --user restart hermes-gateway-<profile>.service` (one per bound profile) |
| OpenClaw | restart the OpenClaw gateway process |
| pi | restart the pi process |
| dsh | reload the dsh service / restart the session that hosts the mail plugin |
| deer-flow | restart the backend service (uvicorn / systemd unit) |

Rules:
- Never restart the process you are running inside from this session.
- If the platform has a plugin-install command that loads without a full
  process restart, prefer it and skip Step 5 for that platform.

## Step 6 — Verify the loop

```bash
aimail check --system-id <system_id>
aimail welcome --system-id <system_id>   # welcome mail to the manager
```

- `check` must now be fully green (hook probe PASS).
- `aimail welcome` proves: activation → bridge → adapter → binding →
  delivery.
- **Always pass `--system-id` explicitly** (no-argument auto-resolution
  only works on single-system machines).
- On failure: report the exact output; do NOT silently retry in a loop.

On success, reply with ONLY:

> AIMail is ready. Address: <agent address from check/stats, e.g.
> agent.<name>@amail.token.tm>
> You can now try:
> - send an email to <manager>
> - summarize the emails I received
> - draft and send a weekly progress report
