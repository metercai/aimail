# AIMail CLI/SDK Responsibility Boundary and Invocation Contract (**v1.0 · In effect**)

**This file is the top-level rule (signed into effect by the owner on 2026-10-06)**; where it conflicts with code, this file prevails, and the code or this file must be corrected in sync. Evidence: aimail@main (2026-10-06, per-item file:line).

## 1 Domain scopes and hard boundaries

**CLI (`cli/`)**
- Target form: **pure-Rust single binary** (platform static knowledge = probe-module constants + contract constants + host self-report; **no cross-platform data files**, see §5); no Python; does not produce `bin/aimail-src` or `bin/mcp`.
- Responsibility: install / detect / wire / self-check / maintenance; **invokes only, never implements** platform adaptation or SDK logic.
- Command surface (**17**, measured from `aimail --help`, re-verified 2026-10-30): address · agent · bridge · check · domain · install · persona · ping · **platform** · prompt · renew · repair · reset · stats · uninstall · version · welcome. (`mod.rs`, `stub.rs`, `report.rs` (the user-facing output helpers `ok/warn/fail`) are internal files, not commands; 2026-10-11 correction: the old table mislisted the internal helper `report` as a command and was missing `platform`; 2026-10-30 correction: `agent` (address-level bootstrap, added 2026-10-19) was missing)
- **bridge (hard boundary)**: `bridge restart` may only be executed by the CLI; **the implementation and maintenance of the bridge live entirely in the CLI**. The SDK side has **zero bridge symbols at the code level** (ratchet `tests/contract/check-zero-bridge.py`, in force at L0); but the webhook semantics **take value per mode** — the SDK decides `webhook_url` from the `webhook_host` tri-state given by the CLI: ① **key absent** = direct push (registered as the agent's local inbound endpoint, the port is confirmed at inbound-up time, see §4.2 inbound-up feedback); ② **empty string** = bridge pull (registered empty, the bridge fetches via SSE stream with a long-poll fallback); ③ **URL value** = bridge push (registered as the BRIDGE_HOST URL, `host:port` with no path). **Single-agent pull is a binding-level self-contained path** (bindings under the `shared_addr_` prefix); it is **not** a system-level `webhook_host` state, and the SDK does **not** parse the gateway's NULL parameter to recognize it (tri-state decision: `aimail_base.py:2895` `resolve_register_webhook_url`; deer-flow wiring `pysdk/deer-flow/manage.py:387-390`). It is possible for the SDK, during a reverse call into the CLI, to **indirectly** trigger a bridge subcommand.

**SDK (`pysdk/` = Python, `tssdk/` = TypeScript source of truth; published as PyPI `agent-aimail` + npm `agent-aimail` — the npm side was merged from 4 packages into this single package on 2026-10-22)**
- Responsibility: the runtime **implementation** layer — naming / registration / renaming / binding content / inbound implementation / send-receive / webhook / preprocessing / tools.
- Packaged as a single package per language (PyPI `agent-aimail` wheel / npm `agent-aimail` tarball — one package, all host adapters inside), **installed by the agent host's own installation step**; not provided by the CLI. Does not write system-level configuration; has no awareness of the bridge.

**Assembly ownership (determined by the platform host's capability, independent of the SDK language)**
- Can self-assemble (node hosts: pi, dsh, openclaw): the host package manager installs the extension, which carries its own registrar ⇒ use `node_entry` (standalone executable entry `<pkg>/dist/register-cli.js`); **no** SDK-side assembly function needed.
- Cannot self-assemble (python hosts: hermes, deerflow): the host has no plugin hook ⇒ `sdk_install` (invoking the SDK-side install function) or an equivalent `spawn` takes on the role.

**Where the ownership lands (evidence added 2026-10-11)**
- A node host's assembly implementation lives **only inside that platform's own package**: the assembly logic is shared (the assembly steps in `mail-core`), and the host layout data is provided by that package (dsh: `tssdk/packages/agent-aimail/src/dsh/register-cli.ts`).
- `pysdk/` assembles python hosts (hermes/deerflow) only; **no node-host assembly entry or payload may reappear** (ratchet: `tests/sdk/test_hosts_layer.py` `test_registry_keys_are_the_two_python_hosts` — the registry key set is pinned to exactly hermes/deerflow).

## 2 Write-surface boundary and the CLI's read scope

**Writing (hard boundaries)**

This table lists only the **two hard boundaries that may not be written across domains**; the other landing points under `systems/<sid>/` (such as `webhook_host`) are held by the owning side, and the CLI may both read and write them (`cli/src/core/bridge_deploy.rs:1143-1151` writes back `webhook_host` only when it is a deliverable absolute URL).
| File | Maintainer | Reader |
|---|---|---|
| `~/.aimail/systems/<sid>/aimail_gateway.json` | **CLI writes only** (core/setup.rs:181/:542) | SDK reads only (deer-flow/manage.py:193) |
| per-agent `agentmail.json` (binding file) | **SDK writes only** (aimail_base.py:510 `save_agent_config`; semantic thin entry points :540/:551/:560) | CLI reads only; **CLI is forbidden to write** (layering ruling block aimail_base.py:533-541) |

**The CLI's read scope (completeness premise)**: reads the system-level `aimail_gateway.json` (gateway_url / admin_key / system_id / domain / system_name / manager_address / runtime); reads the per-agent `agentmail.json` (webhook_url / api_key / persona / prompt_rules / manager_address, **read-only**); the gateway state is queried **directly** by the CLI's own HMAC client (evidence `welcome.rs:63-106`, `ping.rs:129-180`). ⇒ the value retrieval of `check`/`repair`/`stats` needs **no** SDK interface.

## 3 System integration flow

**Stage 1: parameter intake (two paths, two different gateway binaries)**
- Path A **advanced `aimail-advanced`**: application pages (`/apply/address` · `/apply/system` · `/apply/dedicated`) + `POST /api/v1/public/email-verify/{send,check}` ⇒ **activation code delivered to the applicant's email** (the only path; minting codes via API is forbidden); `renew` applies to the advanced edition only; the four code classes = address-level / system-level shared-domain / system-level dedicated-domain / standalone-gateway license.
- Path B **base `aimail-gateway` (self-hosted)**: the `<sid>.system.key` printed at startup serves as the **admin-key** (no application page, no claim chain).

**Stage 2: CLI bootstrap (target form)**
① the user **sets environment variables first** (the parameters from the email) ② runs the remote bootstrap program; under the bootstrap program's drive the environment is initialized:
- create the root directory `~/.aimail`; if that directory's disk free space is <1GB, warn that the reserved space for storing mail is insufficient, but continue.
- **probe the user's hardware environment and deliver the correct binary version** to the correct place (`~/.aimail/bin/aimail`)
- gateway locking: direct-vs-bridge adjudication / copy the bridge into place and configure startup
- a hint pointing to the next action
> Current state (refactored 2026-10): `bootstrap.sh` has zero python dependencies and fetches a single Rust binary from the GitHub Release by OS/arch; machine-level gateway locking / bridge deployment belongs to `aimail install` (local gateway → direct, remote → bridge).

**Stage 3: SDK install (one step, two entry points: `aimail install --home <agent-root>` / host-plugin self-install)**
- Two layers, with a **hard timing constraint: activate first, then adapt**, controlled by the CLI flow: ① system-level activation (the resolver judges the agent environment: parser / package manager / SDK root; produces `system_id` / domain / system-level key ⇒ writes `aimail_gateway.json`; core/setup.rs:457/:542) ② platform adaptation (the CLI dispatches the registry `install_steps`; cmd/install.rs:887. The step `when: cfg_complete` is the embodiment of that timing dependency, core/steps.rs:75).
- **The two entry points run the same sub-flow (identical step set); the differences are exactly two**: the trigger differs (CLI / host package manager); the executor of system-level activation differs (inside CLI install / on the plugin path the SDK reverse-calls `install --system-only`).
- **SDK installation is executed by the host package manager and its timing is controlled by the CLI**: `pip install agent-aimail` / `dsh plugin add agent-aimail` / `pi install npm:agent-aimail` / `openclaw plugins install …`; the SDK **needs no install touchpoint**.

**Stage 4: closed-loop operation**: the `aimail welcome` closed-loop check covers all paths; what the user perceives is the runtime.

**Stage 5: day-to-day maintenance**: the runtime lives in the SDK; the CLI provides maintenance subcommands.

## 4 Bidirectional interfaces

### 4.1 CLI → SDK

**(1) Standard common (every transport must provide, irreplaceable, retained after convergence) = 3 action ops**
| op | Semantics | Why irreplaceable |
|---|---|---|
| `assemble` | naming (rules) → build the registrar call → run the platform registrar (dispatched by transport) → rename if needed → write the binding | naming/registration rules live in the SDK; the CLI **has no binding write permission** ⇒ without calling it, registration is impossible |
| `update` | controlled field updates: `manager_address` · `prompt_rules` · **`persona`** · `rename` · `webhook-secret` · `backfill` · **`repair`** (gap determination and fix) | the binding content judgment and persistence live in the SDK ⇒ `address set-manager` / `address set-name` / `prompt add\|rm` / `persona` / `repair` all depend on it |
| `teardown` | deregistration (`deregister_agent_email`) · whitelist cleanup (`cleanup_system_whitelists`) · binding backfill (`backfill_binding`) | same as above; without it, uninstall is not clean |
**Value-retrieval ops are not in this list**: data-class values are **read directly** by the CLI per §2; rule-class values (naming / webhook derivation) are **consumed inside the action** by the SDK, with no intermediate values exposed. The three ops share one ABI: a single JSON in; a **flat payload + always-present `ok`/`sdk_version`** out (the door adds the envelope at the outer layer); error kinds are only `usage`/`call`; output is **single-line JSON**. Rule judgments (including repair's gap determination and fix) **belong to the SDK**; the CLI only triggers.

**(1b) Read-only judgment op (added by owner ruling 2026-10-10 (a))**
| op | Semantics | Boundary |
|---|---|---|
| `prompt-test` | the CLI passes `system_id` + rules + text; the SDK returns `name_ok` / the within-field-or / across-field-and match result | **no persistence**; the judgment lives in the SDK, the CLI only passes parameters and **does not re-implement the predicates** (`cli/src/cmd/prompt.rs:8`, `:49`, `:86`) |

**(1c) Value ownership of the template variable `{{TOOLSET_LIST}}` (owner ruling 2026-10-25)**
| Item | Boundary |
|---|---|
| Value semantics | **the host's actual state**: the aimail tool names actually registered on the host for the current agent, same semantics as `SOUL_MD_CONTENT` / `SKILLS_LIST`; **not an SDK static constant**, and there is no "constant fallback" path |
| Where the value is produced | the SDK adapter layer injects a provider (py `_TOOLSET_PROVIDER`, ts `setToolsetProvider`); core invokes it during `build_ctx`; a host with no stable anchor injects nothing ⇒ **empty string** (`pysdk/aimail_base.py:405-417`, `src/core/preprocess.ts:464-472`) |
| CLI boundary | the CLI only **lists the variable name** in the `prompt` scaffold help (`cli/src/cmd/prompt.rs:452`); it **does not parse, does not fetch a value, does not fabricate tool names**; value retrieval and empty-value judgment all belong to the SDK runtime |

**(2) Retained due to py / ts environment differences**
- Transport form (the two transports carry **the same set of semantics**):
  - **python side**: through the door `<host-interpreter> -m aimail.sdk_ops <op>`. Where
    · `<op>` action-class **= the 3 in §4.1(1)**: `assemble` / `update` / `teardown`; read-only class **= 1**: `prompt-test` (§4.1(1b)); plus 6 **tool/value ops** (`version` / `iter_bindings` / `ensure_webhook_secret` / `resolve_register_webhook_url` / `register_agent_email` / `backfill_binding`) — the tool/value class does not belong to the standard action ops (as defined in §4.1(1)). The full op set inside the door: `pysdk/sdk_ops.py:680-693`;
    · `<host-interpreter>` **must be the environment configured by the agent host itself** (e.g. the venv of hermes / deerflow), **not** the python bundled with the CLI ✗ — the CLI is only responsible for **locating** it (`$AIMAIL_PYTHON` → host-venv probe outside `~/.aimail/bin` → PATH `python3`; the locating method is a transport detail of this contract). When the host self-installs the SDK package, it must install it into the **same** interpreter.
    · **Registration and assembly also go through the door (`assemble`)**: the CLI **must not** call host modules/scripts directly (calls like hermes `_auto_register_email` or deerflow `manage.py` are all moved into the door; owner ruling 2026-10-11).
  - **node side**: **always** through the platform registrar (`node_entry`, standalone executable entry `<pkg>/dist/register-cli.js`, **does not load host plugins** ⇒ lifecycle diagnostics must not enter stdout); it carries the same semantics as above (owner ruling 2026-10-11: the `host_command` form is retired).
  The **semantics and JSON shape of the two are identical**; only the transport differs.
- python host assembly: hermes / deerflow are kept via `sdk_install` (invoking the SDK-side install function) for now — a targeted retention under "the host has no plugin hook"; removable once the host provides an install hook.
- platform extension installation: executed by the **host package manager** (the CLI triggers it via `spawn`) ⇒ **not part of the SDK interface**.
- host interpreter locating contract (**priority semantics**; single source of truth `cli/src/core/sdkroot.rs::probe_in_domain`): `$AIMAIL_PYTHON` → static candidates → **nested-venv probe inside the host tree** → **PROBE_SH** → **live-process probe** → PATH `python3` (evidence added 2026-10-11; details take that function as the single source of truth).
- CLI locating contract: `$AIMAIL_BIN` → `~/.aimail/bin/aimail` → PATH (aimail_base.py:1767-1779).

### 4.2 SDK → CLI (reverse-call ABI)

**(1) Standard common**
| Scenario | CLI command | Evidence |
|---|---|---|
| system-level activation | `aimail install --system-only` (L1-only) | core/setup.rs:457 (POST `activate-system`) · mail-service.ts:132 (`ensureDefaultAgentBinding`) · aimail_base.py:3213 (`ensure_system`) · hermes/aimail_hermes.py:490-501 (`trigger_profile_hooks`) |
| uninstall | `aimail uninstall` | pysdk/install.py:413/:534 (per-host `uninstall_*` steps) |
| inbound up / down | `aimail address -a <addr> --inbound-live --inbound-url <confirmed-inbound-URL>` / `--inbound-down` | dsh/inbound.ts:412 (`notifyInboundForSystem`) · core/inbound-notify.ts:156 · dsh/register-cli.ts:166-168 |
| **Reserved**: agent add / remove | part of the `address` subcommand (the SDK currently has no entry) | owner ruling 2026-10-06 |

**(2) py / ts differences**: **none** — both environments invoke the same CLI command; the only difference is "how the CLI is located" (see the last item in §4.1(2)).

### 4.3 Inbound endpoint up feedback (port-determinism closed loop, owner 2026-10-13)

- **Port determinism lands at inbound-up**: at install time only the "best known" inbound port is known (AGENT_HOST locking / platform default); the port becomes **definitely available** only after the inbound server actually listens. The inbound-up callback **must carry the confirmed inbound URL** (`--inbound-url <http(s)://host:port/path>`, = the URL the listener actually serves), and the CLI writes the definite URL back to the route layer according to state (overwriting install's pre-registered value).
- **Multi-agent loop**: when one inbound endpoint carries multiple agent addresses, the SDK calls back `address` **per address**, ensuring each address has a route/registration (`inbound-notify.ts::notifyInboundForSystem`).
- **Write-back per state**: bridge push ⇒ external bridge route (admin `sync_route` action=live, target=confirmed URL); bridge pull ⇒ local bridge route (same API, 127.0.0.1); direct push ⇒ **reuse the registration (re-send the address)** idempotent upsert of the gateway's per-address webhook_url=confirmed URL.
- **Down is symmetric**: `--inbound-down` ⇒ bridge route withdraw / gateway registration retraction.
- **Race-safe**: bridge pull does not ack a missing route (retried next round) ⇒ mail stays pending until the route is filled in, nothing is lost.
- **The listener must serve on the confirmed URL**: when the confirmed URL is an external IP (AGENT_HOST), the listener binds that IP / 0.0.0.0 (**not loopback only**); when the port is occupied (self-bind hosts dsh / pi, since 2026-10-25) ⇒ the runtime **re-selects** an available port at run time (the actual bind result is the source of truth), and the confirmed callback carries the real endpoint after re-selection — a single port is never pinned.

## 5 CLI detection surface (added 2026-10-11)

Platform static knowledge has **no cross-platform data file** (`cli/platforms.json` retired), split into three sources by origin:

| Category | Content | Landing point |
|---|---|---|
| Derivable | `pointer.kind` (derived from the container shape), agent container shape and multi-profile naming, op entry templates (one constant per type), the plugin `register-cli.js` path (post-install glob `**/node_modules/*-aimail/dist/register-cli.js` — the entry path and ABI are uniform across the three packages, owner 2026-10-11: no installed older version exists ⇒ always `dist/`) | CLI probe module |
| Constants | home directory names, host feature markers, host package-manager install commands, **host layout patterns** (the relative path pattern of the plugin entry inside the platform home), self-check probe vocabulary | CLI probe module (single copy) |
| Host self-report / internal | fixed agent names, aliases (`default` / `main`), platform-private registration parameters (e.g. dsh `--preset`) | SDK internal |

- Probe entry: `detect_static(home_dir, explicit_platform) -> StaticInfo{platform, transport, agent_keys, pointer, entry, installed_version}`; **`home_dir` is external input**.
- Priority: explicit `--platform` (validated, no silent fallback) → installed components (package name / module) → host feature markers (fallback for the not-installed state) → `unknown` ⇒ **loud failure** with a `--platform` hint.
- In the not-installed state **only** the two methods above are allowed (marker probe + explicit `--platform`); any implicit guessing is forbidden.
- Export surface: `aimail platform spec --json` (for gate and fixture assertions; the single source of truth remains the constants inside the probe module).
- The boundary is unchanged: probing belongs to the "detection/self-check" responsibility in §1; the CLI **still does not implement** platform adaptation or SDK logic (§1).
