# AIMail Self-Setup — Agent-native mail (AI Agent quick start)

> Instructions written FOR the agent that is about to install AIMail on its
> own host. Follow each step exactly; run commands in your terminal tool.
> User-facing guidance style mirrors the agently-cli setup pattern.

## Step 1 — Install / update the CLI

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

What it does: installs the `aimail` CLI into `~/.local/bin` and runs the
machine-level init (main dir `~/.aimail`, disk headroom, local-gateway vs
bridge decision). All writes stay inside `~/.aimail` and `~/.local/bin` —
no sudo. Re-running = upgrade (idempotent).

Verify:

```bash
aimail version
```

If `aimail: command not found`: re-open the shell or add `~/.local/bin` to
PATH (`export PATH="$HOME/.local/bin:$PATH"`).

## Step 2 — Obtain the credential (needs the user)

AIMail has no OAuth — the equivalent of the authorization step is a
credential the user must supply. Ask the user for these (do NOT guess,
invent, or reuse old values):

- **New system** — an activation code: `AIMAIL_PRODUCT_CODE` (generated on
  the gateway management page, e.g. amail.token.tm)
- **Existing system** — the admin key: `AIMAIL_ADMIN_KEY` (or point the
  agent at an existing `~/.aimail/.system_raw_key/*_admin.key`)
- **Always** — the manager mailbox that receives the verification mail:
  `AIMAIL_MANAGER_ADDRESS`

When the user provides them, export in your shell:

```bash
export AIMAIL_URL=https://amail.token.tm
export AIMAIL_MANAGER_ADDRESS=<user mailbox>
export AIMAIL_PRODUCT_CODE=<code>          # or: export AIMAIL_ADMIN_KEY=<key>
```

Keep the values in this shell session — Step 3 needs them. (Persist for
future terminals by appending the same lines to `~/.aimail/.env`.)

## Step 3 — Detect the host and install the SDK adapter

Find which agent platform this machine runs (exactly one of these will
exist):

```bash
ls -d ~/.dsh ~/.openclaw ~/.pi ~/.hermes 2>/dev/null; ls -d ~/deepseek-harness 2>/dev/null
# deer-flow host: a dir containing backend/app/gateway
```

Then install with `--home` pointing at that platform root (the command
activates or reuses the system, deploys the bridge entry, installs the
platform adapter — patches/skills/plugin — registers and auto-binds the
agent):

```bash
aimail install --home ~/.hermes        # Hermes
# aimail install --home ~/.dsh         # dsh
# aimail install --home ~/.openclaw    # OpenClaw
# aimail install --home ~/.pi          # pi
# aimail install --home <backend-dir>  # deer-flow
```

Install is idempotent — re-running is safe. Capture the **system_id** the
install printed (or find it later: the directory name under
`~/.aimail/systems/` that contains `aimail_gateway.json`). You will need
it in Step 4.

## Step 4 — Verify the loop

```bash
aimail check --system-id <system_id>
aimail welcome --system-id <system_id>   # sends a welcome mail to the manager
```

- `check` must end green (or with only remote-host notes — those are
  expected and need action on the other machine, not here).
- `welcome` proves the end-to-end path: activation → bridge →
  adapter → binding → delivery.
- **Always pass `--system-id` explicitly.** Without it, welcome/check only
  auto-resolve on Hermes-pointer machines; on dsh/OpenClaw/pi hosts the
  no-argument form fails. Never omit it.
- Hermes hosts: if `check` reports missing webhook patches after install,
  the Hermes profile gateway needs a restart — tell the user to run
  `systemctl --user restart hermes-gateway-<profile>.service` in a
  terminal OUTSIDE this agent session, then re-run `aimail check`.

On success, reply with ONLY:

> AIMail is ready. Address: <the agent address from check/stats, e.g.
> agent.<name>@amail.token.tm>
> You can now try:
> - send an email to <manager>
> - summarize the emails I received
> - draft and send a weekly progress report

On failure: report the exact command output; do NOT silently retry in a
loop.
