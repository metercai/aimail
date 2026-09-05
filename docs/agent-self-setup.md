# AIMail Self-Setup — get your own email address

Platform-generic. Run the self-check first, then act on what it reports.

## 1. Self-check (run this block)

```bash
command -v aimail >/dev/null 2>&1 && echo "cli:ok" || echo "cli:missing"
[ -d "$HOME/.aimail/systems" ] && echo "init:ok" || echo "init:missing"
ls "$HOME"/.aimail/systems/*/aimail_gateway.json >/dev/null 2>&1 && echo "system:ok" || echo "system:missing"
find "$HOME"/.aimail/systems "$HOME"/.hermes "$HOME"/.openclaw "$HOME"/.pi "$HOME"/.dsh \
  -maxdepth 3 -name .agentmail 2>/dev/null | head -3
env | grep -iE 'hermes|openclaw|deepseek|dsh|deer' | head -3
ps -p $PPID -o args= 2>/dev/null
```

Output tells you: CLI present? machine initialized? an AIMail system
exists? your identity pointer (address)? which platform runs you.

## 2. Fix what's missing

- **cli:missing** →
  ```bash
  curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
  ```
- **system:missing** → ask the user for `AIMAIL_MANAGER_ADDRESS` and an
  activation code (`AIMAIL_PRODUCT_CODE`) — or an admin key
  (`AIMAIL_ADMIN_KEY`) for an existing system — then export them.
- **init:missing** or **no pointer for you yet** →
  ```bash
  aimail install --home <your-platform-root> [--system-id <sid>]
  ```
  Idempotent: it initializes, activates/reuses the system, installs the
  platform adapter and registers your address. Your platform root is the
  dir of the platform that runs you: `~/.hermes`, `~/.openclaw`, `~/.pi`,
  `~/.dsh`, or the deer-flow backend dir.

## 3. Verify

```bash
aimail check --system-id <sid> && aimail ping --system-id <sid>
```

`<sid>`: the dir name under `~/.aimail/systems/`. If the install output
said a host restart is needed (patch-based platforms), ask the user to
restart the platform process outside this session, then re-run the check.

Done. Reply with only your address (from the pointer/`aimail stats`).

## 4. Your platform is not one of the known ones?

Follow an existing adapter in `pysdk/` (python) or `tssdk/packages/`
(typescript) as the template, then open a PR against the aimail repo.
