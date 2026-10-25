# Agent Adapter Playbook (SDK Adapter-Layer Adaptation Points)

🇨🇳 中文 (agent-adapter-playbook_zh.md) | English

> Purpose: the **single maintained doc** for SDK adapter layers — what a
> single agent host must adapt, point by point, inside the two isomorphic
> SDK packages, plus the known implementations and verification criteria per
> point. (CLI/SDK boundary content lives in
> `cli/contract-boundary.md`; CLI contracts / config-field tables in
> `cli/README.md`.)
> Paths are relative to the repo root `/aimail`; line numbers reflect the
> 2026-10-23 code state.

## 0. Boundary Iron Rules (what the adapter does / does not do)

1. **Platform knowledge lives only in the host modules** (TS
   `tssdk/packages/agent-aimail/src/{host}/`, py `pysdk/{host}/`);
   core / mail / identity / board / skill / pull are host-agnostic and are
   **never duplicated** (see the `src/hosts/registry.ts` header and the
   §1 isomorphism discipline).
2. **core never touches the filesystem**: core passes the agent identity
   (`AgentConfig` / `config` dict) to the providers injected by the adapter;
   SOUL/skills file resolution is entirely the adapter's job
   (`src/core/preprocess.ts:383-415`, `pysdk/aimail_base.py:350-376`).
3. **Single inbound entry**: every host, every inbound path funnels into the
   same `processInboundMail` / `process_inbound_mail` (HMAC verify →
   preprocessing → ping/pong interception → delivery). The adapter only
   "plugs the request into this function".
4. **Tools have a single source of truth**: `MAIL_TOOLS`
   (`src/core/tool-registry.ts:66`, 14 tools). The adapter only translates
   "registry entry → host tool format"; it never keeps its own tool table.
5. **Contract constants have a single source of truth**: repo-root
   `contract/aimail-contract.json` → `src/core/contract.ts` (hardcoded +
   L0 gate asserts equality) → `pysdk/aimail_contract.py`. Adapters import
   the constants, never write literals; new literals must go into the
   constants module or carry a `contract-allowed` marker (L0 ratchet:
   `agentmail.json` literal counts may only decrease).
6. **The CLI holds zero platform protocol**: the CLI only reads the host
   registry and runs generic executors; adding a platform must never touch
   `cli/src/hosts/mod.rs` or the `cli/src/hosts/*.rs` data files (the L0
   gate fails on any platform literal there). The finalized boundary lives
   in `cli/contract-boundary.md`.

## 1. The Two Isomorphic SDK Packages

### 1.1 The two packages

Adapting a new agent lands entirely in two isomorphic SDK packages (the
side is chosen by the host's language):

| | TS package | Python package |
|---|---|---|
| Published as | npm `agent-aimail` (`tssdk/packages/agent-aimail/`) | PyPI `agent-aimail` (`pysdk/`, in-wheel package name `aimail/`) |
| Current hosts | openclaw / dsh / pi | hermes / deer-flow |
| Entries | `dist/{host}/index.js` (consumed by the host loader) + `dist/{host}/register-cli.js` (spawned by the CLI) | per-host adapter module (registration) + `pysdk/sdk_ops.py` (process-command contract) |
| Host loader | npm extension mechanisms (plugin entry / cordis bundle / pi.extensions) | in-process import / host-source patches (see P4/P5) |

Both packages share the name `agent-aimail` but live on separate registries
(npm and PyPI) and do not depend on each other. The host's language picks
the side: TS/JS runtime ⇒ TS package; Python runtime ⇒ Python package. A
host appears in exactly one package — never both.

### 1.2 Layer-by-layer isomorphism

| Layer | TS package | Python package |
|---|---|---|
| Host registry | `src/hosts/registry.ts` (`HostSpec`: sdkSpecs/configDir/entry/registerCli/peer) | `pysdk/hosts/registry.py` (`HostSpec`: home_dir/marker/adapter_dir/register_module/register_fn/install_fn) |
| Host detection / self-check | `src/hosts/detect.ts` (multi-signal) + `activate.ts` (`activateHost` assertion) | `pysdk/hosts/{detect,activate}.py` (port of the tssdk trio) |
| Shared core | `src/core/` (preprocess / contract / tool-registry / skill-roots / ensure-binding / release-resources) | `pysdk/aimail_base.py` (injection-point group) + `aimail_tools.py` + `aimail_board.py` + `_resources_release.py` |
| Host adapter layer | `src/{host}/` (`index` / `identity` / `inbound` / `tools` / `skills`) | `pysdk/{host}/` (`aimail_{host}.py` + patch/ensure family) |
| Contract constants | `src/core/contract.ts` (hardcoded + L0 asserts == manifest) | `pysdk/aimail_contract.py` (same) |
| Resource release | `src/core/release-resources.ts` (prepack stages into the package) | wheel force-include `resources/` → `aimail/resources/` |

**Isomorphism discipline**: both packages' cores implement the same
contract (`contract/aimail-contract.json` is the single source of truth,
with L0 gates asserting equality both ways); the injection-point lists map
one-to-one across the two sides (§2 P9); tool semantics have the single
source `MAIL_TOOLS`, each side translating them into its host's format.
The adapter layer does exactly three things — host translation,
injection-point assignment, registration — and never duplicates core.

### 1.3 Where a new host goes (decision tree)

1. **Host language**: TS/JS runtime → TS package; Python runtime →
   Python package.
2. **Inside the package** (expanded in the §4 checklist): add a registry
   entry → create the `{host}/` adapter dir → inject providers → wire
   inbound → translate tools → release resources → registration entry.
3. **CLI side (companion, not the SDK adapter layer)**: the CLI's host
   table is declarative registration (`cli/src/hosts/{name}.rs` + one line
   in `ALL`; six facets = identity/runtime/exec/health/pointer/catalog,
   the engine is untouched — `cli/src/hosts/mod.rs`). The CLI holds zero
   platform protocol (enforced by the L0 gate) and only "reads the registry
   and runs executors"; but it drives install/check/repair, so a new host
   must be registered there too, or the CLI side goes blind.

### 1.4 Package layout at a glance (TS package; py package is isomorphic)

```
tssdk/packages/agent-aimail/
├── src/
│   ├── core/        # host-agnostic: preprocess/contract/tools/skills/ensure-binding
│   ├── hosts/       # registry + detection + assertion
│   ├── openclaw/    # adapter: index/identity/inbound/tools/skills
│   ├── dsh/         # adapter: index/mail-service/inbound/tools/skills
│   └── pi/          # adapter: index/identity/inbound/tools
├── resources/skills/  # prepack staging (source of truth = repo-root resources/)
└── test/            # per-host sandbox unit tests
pysdk/
├── aimail_base.py   # core: injection-point group + process_inbound_mail
├── aimail_tools.py  # core: 14 tool implementations
├── hosts/           # registry + detect + activate (ported)
├── hermes/          # adapter: aimail_hermes + patch/ensure family
└── deer-flow/       # adapter: aimail_deerflow + manage + inbound
```

## 2. The Ten Adaptation Points

### P1 Host Declaration & Entry Registration

- **Responsibility**: declare "which hosts this package supports and how
  each is loaded", plus the published entry subpaths.
- **Contract**: on the TS side, the `HostSpec` in
  `src/hosts/registry.ts` (`host / label / sdkSpecs / configDir / entry /
  registerCli / peer`), one entry per host; on the py side, the isomorphic
  `pysdk/hosts/registry.py`.
- **Known methods**:
  - openclaw: `openclaw.plugin.json` + package.json `openclaw.extensions`
    → `dist/openclaw/index.js` (plugin entry).
  - dsh: `cordis.patch.yml` self-mounting bundle with 3 subpaths
    (`dsh-aimail/mail-service` → `ctx.mail`; `/tools`; `/inbound` →
    node:http endpoint), see `src/dsh/index.ts:1-14`.
  - pi: package.json `pi.extensions` → `dist/pi/index.js`.
  - hermes: does not use an npm extension surface; the SDK attaches by
    **patching the host's source** (`pysdk/hermes/patch_*.py`, see P4/P5).
- **Verification**: `detectHost()` (P2) + the entry's `activateHost()`
  assertion pass; the artifact `dist/{host}/index.js` exists.

### P2 Host Detection & Self-Check

- **Responsibility**: catch "package installed under the wrong host"
  half-failures; provide diagnostics.
- **Contract**: `src/hosts/detect.ts` — multi-signal, lazy, never throws.
  Signal 1 (strong): the host SDK is resolvable (`sdkSpecs`, e.g.
  `openclaw/plugin-sdk/plugin-entry`, `@deepseek-ai/cordis`);
  Signal 2 (weak): `~/{configDir}` exists.
- **Known methods**: each host entry calls `activateHost('<host>')` at
  register time to assert "the host that loaded me == the host I claim to
  be" (e.g. `src/openclaw/index.ts:41`).
- **Verification**: inside a host process, `detectHost()` returns the
  correct host key; in a bare node/python process it returns null/None
  without throwing.

### P3 Identity Resolution (binding + auto-bind + outbound identity)

- **Responsibility**: resolve `{system_id, email}` → `AgentConfig` from the
  host environment; carry the outbound `X-AIMail-Agent: platform/ver`
  header.
- **Contract**: pointer file `POINTER_FILE` (`.agentmail`) + binding file
  `BINDING_FILE` (`systems/{sid}/{cleaned_addr}/agentmail.json`)
  (`src/core/contract.ts`). **The pointer is the package-level identity's
  single source of truth** — no cross-system scanning, no env override
  (see `src/openclaw/identity.ts:1-13`, `src/pi/identity.ts:1-14`).
- **Known methods**:
  - openclaw: factory `ctx.agentId` → `~/.openclaw/.agentmail` →
    mail-core `loadConfigByAgentId`; when the pointer is missing but the
    machine has a system config, **auto-bind** (mail-core
    `ensureAgentBinding`, the three-host single source, batch 2026-10-16).
  - pi: same chain via `~/.pi/.agentmail`; the entry calls
    `ensureAgentBinding` **before** the inbound listener is gated up
    ("port up = binding written", see `src/pi/index.ts:135-147`).
  - dsh: identity taken from `exec.agent.id`.
  - hermes: the profile is the identity (`_resolve_profile_dir()`, see P6).
  - deer-flow: the `systems/{sid}/{cleaned_addr}/` directory IS the agent
    (`pysdk/deer-flow/aimail_deerflow.py:116-134`).
- **Agent-scope binding prefix**: bindings produced by agent-level
  conversational installs (address codes) live under a system with the
  **`shared_addr_`** prefix (the system the gateway auto-creates for
  address codes; implementation source of truth
  `AGENT_SCOPE_SYSTEM_PREFIX`, `src/core/poll-entry.ts:36`); system-level
  bindings use the system's own id. Adapter identity resolution
  distinguishes the two binding classes by the system_id prefix and never
  mixes them.
- **Outbound identity**: `setAgentIdentity()` /
  `_AGENT_IDENTITY_OVERRIDE`, version taken from the host install
  **actually measured** (walking up the installed package), never guessed.
- **Verification**: binding-file fields match the host environment; the
  `X-AIMail-Agent` header's `platform/ver` matches the host's real version.

### P4 Inbound Wiring

- **Responsibility**: plug the host's inbound traffic into the single entry
  function; attach ping/pong health probing.
- **Contract**: path `INBOUND_PATH = /aimail/inbound` (immutable on all
  platforms); hermes is the only exception,
  `HERMES_INBOUND_PATH = /webhooks/aimail-inbound` (mounted on the hermes
  gateway's own webhook route, route name `aimail-inbound`).
  Ports: ONE common default `inbound_port = 9099` for the hosts whose SDK
  owns the listener (dsh / pi) — **ports configurable, paths immutable**
  (`src/core/contract.ts`). On a port collision the runtime re-selects an
  available port (the ACTUAL bind result is the source of truth) and the
  confirmed callback carries that real endpoint. Hosts whose gateway owns
  the port (hermes, deer-flow, openclaw) carry NO contract default — the
  adapter discovers the actual port and the confirmed callback always
  carries the real URL.
- **Known methods**:
  - pi: in-extension node:http handler → `verifySignature` (HMAC,
    per-address `webhook_secret`) → `routeAddressFromHeaders` →
    `processInboundMail` (`src/pi/index.ts:66-114`).
  - dsh: bundle subpath `dsh-aimail/inbound` (node:http endpoint +
    delivery).
  - openclaw: HTTP route registered inside the plugin `register(api)`
    (`src/openclaw/index.ts:38`, inbound logic in
    `src/openclaw/inbound.ts`).
  - hermes: patch the hermes-agent distribution's
    `gateway/platforms/webhook.py` (external file, not in this repo) to
    add `PREPROCESS_REGISTRY` + `register_preprocessor()`, invoked by the
    webhook handler before prompt rendering
    (`pysdk/hermes/patch_webhook.py`); at the same time
    `ensure_config.py` idempotently ensures the profile's
    `platform_toolsets.webhook`/`cli` include `agentmail` (missing either
    one is the "can receive but cannot reply" break).
  - deer-flow: in the local gateway process, `POST /aimail/inbound` →
    `process_inbound_mail` → ping/pong interception (swallowed with 200,
    agent not triggered) → `start_run` (thread = `uuid5("aimail", email)`,
    session stable per address) → immediate 200
    (`pysdk/deer-flow/aimail_inbound.py:1-20`).
- **Verification**: gateway original → bodyproc → local entry full chain
  measured end-to-end; the ping/pong contract holds; inbound logs land in
  `~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.log`.

### P5 Tool Registration

- **Responsibility**: translate `MAIL_TOOLS` (14: send_mail,
  manage_contacts, contact_profile, set_contact_profile, email_summary,
  set_email_summary, search_mail, board_status, board_task_list,
  board_task_show, board_heartbeat, board_members, board_roles,
  activate_address_code — single source
  `src/core/tool-registry.ts:66`) into the host tool format and register
  them.
- **Known methods**:
  - openclaw: `definePluginEntry` factory form iterating `MAIL_TOOLS`
    (`src/openclaw/index.ts:38` header, `src/openclaw/tools.ts`).
  - dsh: `for (const tool of MAIL_TOOLS)` → dsh `defineTool`
    (`src/dsh/tools.ts:89-94`).
  - pi: same `MAIL_TOOLS` iteration (`src/pi/tools.ts`).
  - hermes: patch `hermes-agent/toolsets.py` to register the 7 mail tool
    names into `_HERMES_CORE_TOOLS`; `who patches, who unpatches` (exact-
    text stripping, `pysdk/hermes/toolsets.py:57-63`).
- **Verification**: tools are visible in the host with parameter schemas
  matching the registry; tool **counts** in comments drift as tools are
  added/removed (e.g. `src/dsh/index.ts:6` still says "15", while after
  whoami's removal the truth is 14) — for counts, trust the registry
  only; never hardcode numbers in comments.

#### P5b Toolset Provider (board ctx `{{TOOLSET_LIST}}`, 2026-10-25)

- **Responsibility**: the fill source for the board context template
  `{{TOOLSET_LIST}}` — the tool names **actually registered on this host
  for this agent**. The adapter injects the toolset provider; core invokes
  it in `build_ctx` (`src/core/preprocess.ts:384`,
  `pysdk/aimail_base.py:235`).
- **Contract** (owner ruling 2026-10-25): the value is **host actual
  state** — same semantics as SOUL/SKILLS. The adapter resolves it from the
  host's own state at render time; a host with no stable tool-introspection
  anchor leaves the provider **uninjected ⇒ `""`** (never a static constant
  fallback). `AGENT_TOOL_NAMES` (py `pysdk/aimail_agent.py` / ts
  `src/core/agent-tools.ts`) stays the registry-side single source for
  registration filtering — it is NOT the value source of
  `{{TOOLSET_LIST}}`.
- **Known anchors** (per host interface difference):
  - hermes: live registry introspection
    `registry.get_tool_names_for_toolset(_TOOLSET)` (the same live shape as
    `_read_skills` querying the host skills entry;
    `pysdk/hermes/aimail_hermes.py:160-182`, injection `:906`).
  - deer-flow: the host's own extensions config — the mcpServers entry for
    aimail and its exposed tool list (absent/disabled ⇒ `[]`),
    `pysdk/deer-flow/aimail_deerflow.py:167`, injection `:253`.
  - pi: `pi.getActiveTools()` ∩ the adapter's `registeredToolNames`
    (`src/pi/index.ts:258-272`).
  - openclaw: the factory reports the tools it actually produced when the
    host invoked registration (`src/openclaw/tools.ts:42-50`).
  - dsh: `apply()` reports the tools it actually registered
    (`src/dsh/tools.ts:108-116`).
- **Measured snapshot (2026-10-25, local)**: hermes live value = the
  registry's actual aimail set (14 names; `role_calibrator` not registered
  on that host ⇒ the value is NOT the 15-name constant — proof the source
  is live state); deer-flow enabled = 15 MCP tools / disabled = empty.
  Counts drift as registrations change — trust the live introspection.
- **Verification**: rendered `{{TOOLSET_LIST}}` == what the host's own
  introspection returns for this agent; a host where nothing is registered
  renders `""`.

### P6 SOUL (per-agent) Resolution

- **Responsibility**: the fill source for the board context template
  `{{SOUL_MD_CONTENT}}`. The adapter injects `_SOUL_PROVIDER`; core
  invokes it in `build_ctx` (`src/core/preprocess.ts:378`,
  `pysdk/aimail_base.py:230`).
- **Contract**: signature TS `(cfg: AgentConfig) => string`,
  py `(config: Optional[dict]) -> str`; **uninjected ⇒ empty string**
  (whoever has a SOUL fills it; core never touches the FS);
  **missing ⇒ ''** (the template renders empty, documented).
- **Known anchors** (per host layout; multi-agent platforms resolve by
  agent identity):
  - openclaw: `{resolveAgentWorkspaceDir(agentId)}/SOUL.md`,
    `agentId = cfg.agent_id ?? 'main'` (`src/openclaw/index.ts:78-87`).
  - dsh: `~/.dsh/profiles/{DSH_PROFILE}/SOUL.md` (env
    `DSH_HOME`/`DSH_PROFILE`, defaults `~/.dsh`/`web`,
    `src/dsh/inbound.ts:329-336`).
  - pi: `~/.pi/agent/SOUL.md` (`~/.pi/agent/` IS the per-agent dir,
    `src/pi/index.ts:184-193`).
  - hermes: `_resolve_profile_dir()/SOUL.md`, anchor chain
    `HERMES_PROFILE_DIR` env → live `HERMES_HOME` (contextvar
    `get_hermes_home()`, set per profile by the gateway spawner) →
    `~/.hermes` (`pysdk/hermes/aimail_hermes.py:103-160`).
  - deer-flow: `systems/{sid}/{cleaned_addr}/SOUL.md` (the directory is
    the agent, `pysdk/deer-flow/aimail_deerflow.py:116-121`).
- **Pitfall (fixed 2026-10-23)**: hermes's old anchor only honored
  `HERMES_PROFILE_DIR` (which hermes at runtime does **not** set) → fell
  back to the generic `~/.hermes/SOUL.md` (513B) instead of the
  per-profile SOUL (907B). The true per-agent anchor is `HERMES_HOME`.
- **Verification**: the SOUL read out matches, byte for byte, the file
  corresponding to "this agent's identity" (size + first line); hosts
  without an authored SOUL return ''.

### P7 Skills Enumeration

- **Responsibility**: the fill source for the board context template
  `{{SKILLS_LIST}}`. The adapter injects `_SKILLS_PROVIDER`; core does
  `", ".join(...)` in `build_ctx` (`src/core/preprocess.ts:379`,
  `pysdk/aimail_base.py:231`).
- **Contract**: signature TS `(cfg: AgentConfig) => string[]`,
  py `(config: Optional[dict]) -> list[str]`; **uninjected ⇒ []**.
- **Ruling (2026-10-23, owner)**: the reporting standard = "the skills
  list this agent's configuration can load"; skills in shared roots count,
  never return empty just because "the directory is shared".
- **Discovery granularity** (shared enumerator
  `src/core/skill-roots.ts` `listSkillNames` / `combineSkillNames`):
  top-level subdirectory containing a `SKILL.md`, or a bare `.md`;
  name = frontmatter `name:` ?? directory basename; `fs.statSync`
  **follows symlinks** (`readdir` `withFileTypes` `isDirectory()` does
  not — plugin-skills symlinks would all be missed).
- **Known methods** (each host mirrors the host runtime's own discovery
  chain):
  - openclaw: mirrors `resolveWorkspaceSkillSourcePlan` root order —
    extraDirs → bundled (+custodian, owner only) → workshop → managed →
    personal (default state only) → workspace (`.agents/skills` +
    `skills`) → plugin-skills; bundled anchored via
    `createRequire(...).resolve('openclaw/plugin-sdk/plugin-entry')` then
    walk up to `skills/` (`src/openclaw/skills.ts`).
  - dsh: mirrors `FileSystemSkillProvider.roots`: project (walk up from
    cwd to `.git` for `.dsh/skills` + `.agents/skills`) → user
    (`$DSH_HOME/skills` + `$DSH_AGENTS_HOME/skills`) → bundled
    (`$DSH_BUNDLED_SKILL_DIR`) (`src/dsh/skills.ts`).
  - pi: skills are naturally per-agent, single root
    `~/.pi/agent/skills/` (`src/pi/index.ts:196-200`).
  - hermes: does **not** re-implement walk+filter; directly imports the
    host's own authoritative entry, `tools/skills_tool` (the `skills_list`
    tool's underlying `_find_all_skills`: live profile-scoped
    `HERMES_HOME` + external_dirs + platform/env/disabled gates + dedupe,
    including plugin skills); import failure (non-hermes process) falls
    back to `[]` (`pysdk/hermes/aimail_hermes.py:121-158`).
- **Pitfall (fixed 2026-10-23)**: hermes's old implementation read the
  `config.yaml → skills:` field — that is the skills **subsystem config
  block** (dict: external_dirs / template_vars / inline_shell…), not a
  name list; `", ".join(dict)` spliced the key names into a garbage
  string in SKILLS_LIST.
- **Verification criterion**: cross-check against the host's own visible
  list. Measured locally (2026-10-23): openclaw 86, dsh 32, pi 0, hermes
  (agentmail profile) 147 (147 < raw walk's 151; the 4 missing are
  correctly filtered by the host's platform/env/disabled gates — the
  criterion is exactly "what the agent can actually see").

### P8 Resource Release (board / skill, idempotent)

- **Responsibility**: release SDK-shipped resources to host-readable
  locations, covering machines that install only the TS package and never
  the Python SDK/CLI.
- **Known methods** (`src/core/release-resources.ts` +
  `packageResourcesRoot()`):
  - board resources → `~/.aimail/systems/*/board/` (idempotent, **never
    overwrites** user-personalized files; `releaseAllSystems`).
  - agentmail skill resources → the host skills dir `agentmail/`
    (idempotent, identical-content skip): openclaw
    `~/.openclaw/skills/agentmail/`, dsh `<dshHome>/skills/agentmail/`,
    pi `~/.pi/agent/skills/agentmail/`, hermes
    `_release_hermes_skills` (`pysdk/install.py:265`, invoked by the
    install chain).
- **Verification**: a second install produces no diff (idempotent);
  user-modified files are not overwritten.

### P9 Injection Point Assignment (adapter ↔ core interface)

- **Responsibility**: after importing the shared core, assign the host
  implementations to core's injection points (isomorphic on both sides;
  TS via functions like `setSoulProvider`, py via module-level
  assignment): `_CONFIG_LOADER` (agent config loader `/ () ->
  Optional[dict]`), `_PROFILE_DIR_RESOLVER` (agent dir `/ () ->
  Optional[str]`), `_PERSONAS_PROVIDER` (personas config),
  `_SOUL_PROVIDER`, `_SKILLS_PROVIDER`, `_TOOLSET_PROVIDER` (toolset names
  actually registered on the host; uninjected ⇒ `""`, no constant
  fallback), `_BOARD_GATEWAY_SINK` (board
  gateway registration callback), `PERSONA_SUPPORTED` (capability switch;
  False → normalized base address), `_AGENT_IDENTITY_OVERRIDE` (outbound
  identity header), `_PERSONA_NAME_PROVIDER` (current persona name).
- **Known methods**:
  - TS: `setSoulProvider` / `setSkillsProvider` / `setToolsetProvider`
    (`src/core/preprocess.ts:392-412`) + the remaining assignments inside
    the entry (e.g. `src/openclaw/index.ts:38-111`).
  - py: module-level direct assignment, hermes visible in one screen
    (`pysdk/hermes/aimail_hermes.py:904-906`);
    deer-flow (`pysdk/deer-flow/aimail_deerflow.py:251-253`).
- **Discipline**: core-side injection-point defaults must be **parity with
  the worst case** (None → empty string/empty list); an uninjected host
  must not hard-fail.

### P10 Agent-level Pull

- **Responsibility**: agent-level pull (the dual of system-level push:
  **pull = agent-level, push = system-level**).
- **Known methods**: the decision lives in mail-core
  `startAgentPullEntries` (host-agnostic single source); host entries only
  call it (e.g. `src/pi/index.ts:147`, `src/dsh/inbound.ts`). The adapter
  does not implement pull logic.

## 3. Per-Host Cheat Sheet (local state 2026-10-23)

| Point | openclaw (TS) | dsh (TS) | pi (TS) | hermes (py) | deer-flow (py) |
|---|---|---|---|---|---|
| P1 entry | plugin entry + extensions | cordis.patch.yml self-mounts 3 subpaths | pi.extensions | host-source patch family | local gateway process |
| P2 self-check | activateHost('openclaw')@register | activateHost('dsh')@mail-service load | activateHost('pi')@entry | the process IS the host | the process IS the host |
| P3 identity | `~/.openclaw/.agentmail` + auto-bind | exec.agent.id | `~/.pi/.agentmail` + auto-bind | the profile is the identity | `systems/{sid}/{cleaned_addr}/` dir is the agent |
| P4 inbound | plugin HTTP route | bundle `/inbound` (node:http) | extension node:http (common :9099) | gateway webhook `/webhooks/aimail-inbound` | local gateway POST (discovered) |
| P5 tools | iterate MAIL_TOOLS (factory) | iterate MAIL_TOOLS (defineTool) | iterate MAIL_TOOLS | patch toolsets.py (7 mail tools into _HERMES_CORE_TOOLS) | core tool functions exposed directly |
| P6 SOUL | `{workspaceDir(agentId)}/SOUL.md` | `~/.dsh/profiles/{DSH_PROFILE}/SOUL.md` | `~/.pi/agent/SOUL.md` | `{HERMES_HOME}/SOUL.md` (env→live→~/.hermes) | `systems/{sid}/{cleaned}/SOUL.md` |
| P7 skills | mirrors resolveWorkspaceSkillSourcePlan (86 local) | mirrors FileSystemSkillProvider.roots (32 local) | `~/.pi/agent/skills/` (0 local) | imports host `tools/skills_tool` (147 local) | `systems/{sid}/{cleaned}/skills/` |
| P8 resources | `~/.openclaw/skills/agentmail/` | `<dshHome>/skills/agentmail/` | `~/.pi/agent/skills/agentmail/` | `_release_hermes_skills` | — |
| P9 injection | in index.ts register | in inbound.ts startup | in index.ts | aimail_hermes.py:876-889 | aimail_deerflow.py:199-200 |
| P10 pull | startAgentPullEntries | same | same | — (hermes uses webhook push) | — |

## 4. New-Host Onboarding Checklist

**TS host** (five steps):
1. Add one `HostSpec` entry in `src/hosts/registry.ts` (sdkSpecs/
   configDir/entry/registerCli/peer).
2. Create `src/{host}/`: `index.ts` (activateHost → identity → inbound →
   tools → resources → SOUL/skills providers), `identity.ts`,
   `inbound.ts`, `tools.ts`; if multiple skill roots are needed, add
   `{host}/skills.ts` mirroring the host runtime's discovery chain.
3. Add the peer in package.json (all optional at install) + the host
   extension manifest.
4. Tests: `test/{host}/` sandbox unit tests (build layouts in temp dirs,
   never depend on local machine state).
5. Verification: `npx tsc -b tsconfig.json` rc=0 + vitest all green +
   L0 contract gate + **real-layout cross-check** (the host-visible skills
   count matches item by item).

**Python host**:
1. Add a host entry in `pysdk/hosts/registry.py`.
2. Create `pysdk/{host}/`: `aimail_{host}.py` (provider definitions +
   injection assignments, signatures `(config) -> str | list[str]`),
   inbound wiring, tool registration; hosts that patch host source (like
   hermes) must provide a symmetric unpatch (who patches, who
   unpatches) and **version adaptation** (auto-detect the host commit and
   adjust insertion points).
3. Idempotent ensure-config (each break point lands in an
   `ensure_config`-style module).
4. Verification: pytest all green + measured in the host process
   environment (hermes = venv python + per-profile `HERMES_HOME`).

## 5. Experience & Rulings (dated)

1. **Read the host's own authoritative entry > re-implementing discovery
   logic** (2026-10-23, hermes): re-implementing walk+filter inside the
   SDK is guaranteed to drift; if you can import the host's authoritative
   entry, import it (hermes `tools/skills_tool`); on import failure, fall
   back to `[]` (parity with the uninjected worst case — no new hard
   failure). When the host deploys as a source tree + venv, note that the
   import only holds inside the host process — the fallback exists
   precisely for that.
2. **Config fields are not name lists** (2026-10-23, hermes): the
   `skills:` field in `config.yaml` is the subsystem config block (dict),
   not a skill list; verify the "config is the list" assumption against
   real config before coding.
3. **The per-agent anchor = the variable the host actually partitions
   directories by** (2026-10-23, hermes): hermes does not set
   `HERMES_PROFILE_DIR` at runtime; the true anchor is the live
   `HERMES_HOME` (contextvar) set per profile. A wrong anchor = silently
   reading someone else's SOUL.
4. **Shared directory ≠ not reported** (2026-10-23, owner ruling): the
   reporting standard is "the list this agent's configuration can load";
   shared roots count.
5. **Symlink trap** (2026-10-23, openclaw): plugin-skills are symlinks;
   `readdir withFileTypes` `isDirectory()` does not follow them → use
   `fs.statSync`.
6. **Trust the registry for tool counts**: counts in comments (e.g. dsh's
   "15") drift as tools are added/removed — stale after whoami's removal;
   never hardcode numbers in docs/comments.
7. **Verification must cross-check against the real layout**: unit tests
   (sandbox) prove only the logic; whether the criterion is right is
   settled by comparing item by item against the host's own visible list
   (local 86/32/0/147).
8. **L0 contract ratchet**: new contract literals go into the constants
   module; `agentmail.json` literal counts may only decrease; criteria
   may be fixed when wrong, but never turned into fake-green.
9. **Discipline for patching host source** (hermes): who patches, who
   unpatches; version adaptation (auto-detect insertion points);
   idempotent (no diff on a second run); every install-chain break point
   lands in an ensure module (root-cause sediment from the 2026-08-16
   break).
10. **Uninjected = parity with the worst case**: all injection-point
    defaults (empty string/empty list/None) guarantee that an unadapted
    host behaves exactly as before adaptation — no new failure surface.
