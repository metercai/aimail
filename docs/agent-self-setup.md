# Getting Your AIMail Address — self-service for ONE agent

> Written for YOU — the agent that wants its own AIMail email address.
> First person, one agent, one address. Follow each step exactly.
>
> **Scope boundary**: this guide gives ONE agent (you) an address on an
> existing AIMail system. Full platform onboarding (activating a system,
> wiring a whole platform's agents, bridge, maintenance) is the CLI's job
> (`aimail install/check/repair`) and needs an operator — when this guide
> hits that boundary it says so and points there. Deep host integration is
> by design; the only user action is the host restart at the end.

## Step 1 — Know who you are

You run inside one platform and have one identity there:

- **Platform**: your profile/session/agent id implies it — Hermes profile,
  dsh session, OpenClaw/pi agent, deer-flow assistant. Do not guess from
  directories (a machine may host several platforms; you belong to one).
- **Your name in the platform**: your profile name (Hermes), session id
  (dsh), agent id (OpenClaw/pi) — this becomes your address prefix
  (`{name}@{domain}`, or `{name}.{system_name}@{shared-domain}`).
- **Your platform root**: `~/.hermes`, `~/.openclaw`, `~/.pi`, `~/.dsh`,
  or the deer-flow backend dir.

Confirm with `env | grep -iE 'hermes|openclaw|deepseek|dsh|deer'` and
`ps -p $PPID -o args=` if unsure.

## Step 2 — Do you already have an address?

Check your identity pointer (written at registration):

```bash
cat <platform-root>/.agentmail 2>/dev/null          # openclaw / pi / dsh / deer-flow root
cat <your-profile-dir>/.agentmail 2>/dev/null       # hermes: ~/.hermes or ~/.hermes/profiles/<name>
# or look for your agentmail.json:
ls ~/.aimail/systems/*/<cleaned-address>/agentmail.json 2>/dev/null
```

If it exists with your system_id and email → you already have an address;
jump to Step 5 and verify it. Otherwise continue.

## Step 3 — Make sure the environment is ready

In order, fix whatever is missing (each is a normal download/install you
can do yourself):

1. **CLI present?** If `aimail` is missing, install it (no sudo, ~10s):

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

2. **Machine initialized?** If `~/.aimail/systems` does not exist yet:

```bash
aimail init
```

3. **An AIMail system exists on this machine?** (a `systems/*/aimail_gateway.json`)

   - **Yes** → the system is already there; you only need your address (Step 4).
   - **No** → a system must be created before any address can exist. This is
     the platform-onboarding boundary: ask the user for an activation code
     (`AIMAIL_PRODUCT_CODE`) and manager mailbox (`AIMAIL_MANAGER_ADDRESS`),
     then run one platform-level install that creates the system and wires
     your platform in one pass:

```bash
export AIMAIL_MANAGER_ADDRESS=<user mailbox>
export AIMAIL_PRODUCT_CODE=<code>
aimail install --home <your platform root>
```

   (This also registers every existing agent of your platform — that is the
   CLI's all-agents scope; your address is created in the same pass.)

4. **Your platform's runtime wired?** If install above was skipped because
   a system exists but your platform was never integrated (no webhook
   patch / no plugin), one platform-level install fixes it:

```bash
aimail install --home <your platform root> --system-id <sid>
```

   If you cannot run it (no credential), tell the user/operator: platform
   onboarding is the CLI dimension — after it runs, come back to Step 4.

## Step 4 — Get YOUR address (registration)

Every platform has a LAZY registration path (no operator action needed):
the adapter registers you automatically at its natural trigger point,
guarded by an existence check (already have a pointer → skip).

- **openclaw / pi / dsh**: the SDK auto-binds on first use
  (existence-guarded, one session ⇔ one address). Trigger it by using your
  mail capability once (openclaw command / pi entry / dsh mail-service
  apply); the SDK registers you and writes your pointer.
- **Hermes**: registration hooks on the profile lifecycle — when your
  profile was created, `trigger_profile_hooks("profile_created")` fired
  `_auto_register_email` (hermes profiles.py, patched by the adapter).
  Your address exists from birth. If you predate the platform
  onboarding (or the system changed), a re-run of the platform register
  step covers you:
  `python -m aimail.install register-profiles --type hermes --home ~/.hermes`
  (or `aimail install --home ~/.hermes --system-id <sid>`), then re-check
  your pointer.
- **deer-flow**: binding happens at the backend entry/reconcile point.

After registration your address is `{name}@{domain}` (bare-domain system)
or `{name}.{system_name}@{shared-domain}` — it is in your `.agentmail`
pointer / `agentmail.json`. If it is already there, you are done — verify
in Step 5.

## Step 5 — Verify your address works

```bash
aimail check --system-id <sid>     # your platform/agent rows green
aimail ping --system-id <sid>      # SMTP round-trip: you receive your own ping
```

Then tell the user your address and ask them to send you one real email
(or run `aimail welcome --system-id <sid>` — a welcome mail to the
manager). On success reply with ONLY:

> My AIMail address is <your address>. Send me an email to reach me —
> try it now, or ask me to email you.

On failure: report the exact output; do NOT silently retry in a loop.
