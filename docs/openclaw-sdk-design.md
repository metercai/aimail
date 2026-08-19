# OpenClaw SDK Adaptation Design (v2 — after architecture review)

Status: DRAFT for approval. Code changes start only after review sign-off (plan-first).
Supersedes: v1 design (2026-08-19, pre-review).

## 1. Decisions locked in review (2026-08-19)

| # | Decision | Note |
|---|----------|------|
| D1 | Retire `@aimail/tool-mail` as an independent package. Tool registration moves into `dsh-aimail`. | tool-mail's only real logic is dsh binding (exec.agent.id); the 12 tool functions already live in mail-core. |
| D2 | `@aimail/mail` stripped of dsh-specific traits (cordis `Context`/`apply`/`provide`); becomes a pure config-resolution package. | Keeps both integration packages symmetric. |
| D3 | OpenClaw integration package name: `openclaw-aimail` (bare name, pre-registered by us; npm view confirms 0.0.1 placeholder is ours). Symmetric with `dsh-aimail`. | v1's `@aimail/openclaw` proposal withdrawn. |
| D4 | Single repo for all packages; repo renamed `dsh-aimail` → `aimail-sdk-ts`. | Repo name is decoupled from package names. Core business code is one source by construction (same tree). |
| D5 | OpenClaw side needs exactly ONE integration package; everything else reuses `@aimail/*` business packages. No MCP, no standalone preprocess layer, no amail-poll.py. Bridge already solves push/pull — plugin only receives bridge push. | Capability surface = push inbound + toolset; nothing external required. |
| D6 | The 13-step inbound chain (`processInboundMail` in mail-core) is NOT "preprocess" — it IS inbound handling (ping/pong intercept, thread continuity, field stripping, [WHOAMI] marker). It stays, called from inside each plugin's inbound handler. | Clarifies D5: no *external* preprocess component; the chain itself is non-removable contract. |
| D7 | Tool semantic text (12 tool names/descriptions/parameter descriptions) centralized in mail-core as `MAIL_TOOLS`; adapters reference it at registration. | Single TS source of truth; dsh + openclaw register from the same array; Python `amail_mcp_server.py` stays the upstream contract reference. |

## 2. Corrections to v1 analysis (code-verified)

- **`@aimail/mail-inbound` is NOT generic.** Imports `@deepseek-ai/cordis` (Context), `@deepseek-ai/dsh-agent` (Agent, live/cold followup), `@deepseek-ai/dsh-llm` (createUserMessage). Its delivery half is dsh-specific. → Retired as a package; the node:http server + recipient loop + dsh delivery migrates into `dsh-aimail`. Clarification: the inbound *processing* chain (`processInboundMail` 13-step + `verifySignature`) was ALWAYS in mail-core — mail-inbound never contained it, so nothing is "merged into mail-core".
- **Persona-aware recipient routing is a real gap (found during review).** dsh TS inbound resolves recipients by EXACT match only (`resolveByEmail`); mail addressed to a persona alias (`support.alice@…`) is swallowed as `no_agent` — contradicting the dsh contract (PERSONA_SUPPORTED=false ⇒ normalize to base address, i.e. routing must first map alias → base agent). Python baseline has `route_agent_for_email` (exact match + `endsWith(".base")` persona strip) precisely for single-in-multi-out platforms. OpenClaw MUST have this (one gateway hosts many agents; mail can arrive at role aliases). → Add generic `resolveByRecipient` to `@aimail/mail` (exact first, then persona-strip fallback, mirrors Python semantics); both platforms use it; dsh switches over (fixes the swallow bug as a side effect).
- **Two "persona" namesakes (disambiguation).** (a) dsh `persona` (the cordis.patch.yml entry) = a dsh system-prompt text block via `@deepseek-ai/dsh-persona` that teaches the agent the mail workflow — unrelated to email addresses; no OpenClaw equivalent exists in the plugin. (b) AgentMail address persona = business-layer address convention `persona.profile[@sys_name]@domain`; each agent's REGISTERED address is the base (no prefix); aliases normalize to base (dsh) or trigger LLM role switching (Hermes, PERSONA_SUPPORTED=true).
- **`@aimail/mail` real role:** per-session config resolution service — `sessionId/email/agentId → agentmail.json → AgentConfig {system_id, email, webhook_secret, ...}`. The dsh-specific part is only the cordis host wiring (`apply(ctx)`, `ctx.provide`). The resolution logic itself is platform-neutral. → Strip the wiring, keep the logic.
- **`@aimail/mail-core` remains the only fully generic package** (zero deps, zero framework imports). Unchanged in this plan.
- **`dsh-aimail` current shape:** 7-line re-export entry + `cordis.patch.yml` mounting 3 external packages (mail / mail-inbound / tool-mail) + persona. After restructure it owns all dsh-side registration code internally.

## 3. Target architecture

```
aimail-sdk-ts/                      (renamed repo, pnpm monorepo)
├── packages/mail-core/             @aimail/mail-core      ADDITIVE (rc.9)
│     GatewayClient · 12 tool fns · processInboundMail (13-step) ·
│     verifySignature · agentmail.json loaders (by session/email/agent id)
│     + MAIL_TOOLS: the 12 semantic tool definitions (name/description/
│       param descriptions) — single TS source of truth (D7)
│
├── packages/mail/                  @aimail/mail           STRIPPED (rc.9, breaking)
│     Pure resolution: resolveBySessionId / resolveByEmail / resolveByAgentId
│     + resolveByRecipient (exact match → persona-strip fallback, mirrors
│       Python route_agent_for_email — inbound routing for single-in-multi-out)
│     + AMAIL_SYSTEM_ID scope narrowing + unbound error semantics.
│     No cordis, no dsh-sdk, no apply(). Plain exported functions/factory.
│
├── packages/dsh-aimail/            dsh-aimail             RESTRUCTURED (rc.12)
│     src/index.ts      entry re-export (compat)
│     src/tools.ts      ← migrated from tool-mail: registerTools() via dsh-sdk,
│                         iterating MAIL_TOOLS; exec.agent.id → @aimail/mail
│                         resolution → 12 bare tools
│     src/inbound.ts    ← migrated from mail-inbound: node:http server,
│                         recipient→resolveByEmail, HMAC, processInboundMail,
│                         live followup / cold resume (dsh-agent)
│     cordis.patch.yml  persona + own service entries (self-mount; see R1)
│     deps: @aimail/mail-core, @aimail/mail
│
└── packages/openclaw-aimail/       openclaw-aimail        NEW (0.1.0-rc.1)
      src/index.ts      definePluginEntry: 12 tools (factory form, iterating
                        MAIL_TOOLS) + inbound HTTP route +
                        register/deregister/status commands
      src/identity.ts   factory ctx.agentId → ~/.openclaw/.agentmail pointer →
                        system_id → @aimail/mail resolution → AgentConfig
      src/inbound.ts    registerHttpRoute handler: HMAC verify →
                        processInboundMail → agent turn delivery
      openclaw.plugin.json  id=openclaw-aimail, contracts.tools (12 bare names)
      deps: @aimail/mail-core, @aimail/mail · peer/dev: openclaw
```

Dependency graph (after):

```
dsh-aimail      ──> @aimail/mail ──> @aimail/mail-core
openclaw-aimail ──> @aimail/mail ──> @aimail/mail-core
```

Symmetry: both integration packages have exactly two layers — (a) platform
registration (tools + inbound mount, platform-specific, ~200 lines each),
(b) business via shared packages (identical on both sides). Nothing dsh- or
openclaw-specific below the integration layer.

Retired from npm (deprecate, keep versions): `@aimail/tool-mail`,
`@aimail/mail-inbound`.

## 4. OpenClaw side design (capability = push inbound + toolset)

Identity chain (mirrors dsh's exec.agent.id):
  plugin factory context {agentId, sessionKey}
  → ~/.openclaw/.agentmail pointer (sole identity source; no env override,
    no cross-system scan — established convention)
  → system_id → @aimail/mail resolution → AgentConfig
  unbound agent → fail loud ("agentmail not configured for this agent").

Tools: 12 bare names (send_mail, manage_contacts, ...) registered via
`api.registerTool`, factory form (identity available). Both adapters iterate
the SAME `MAIL_TOOLS` array from mail-core (D7) — semantic text defined once,
each adapter only binds the platform's execute/identity context. Execution
calls the same mail-core functions as dsh. No prefix — SKILL.md bare names
resolve exactly on both platforms; the Python MCP route (amail__ prefix)
retires after P2 acceptance.

Inbound: `api.registerHttpRoute` inside the gateway process (no new port;
bridge push target unchanged). Handler = recipient resolution
(`resolveByRecipient`: exact address match → persona-strip fallback, so mail
to a role alias routes to the owning agent) → verifySignature (byte-exact,
mail-core) → processInboundMail (13-step chain + ping/pong intercept) →
agent turn with full enriched JSON payload (rendering parity = json.dumps
equivalence, established acceptance bar). Delivery mechanism (P0.3):
`api.runtime.subagent.run` (session-scoped run) as primary,
`api.runtime.gateway.request` (explicit agent targeting) as fallback —
decided empirically in P2. `runEmbeddedAgent` does not exist in the SDK.

Registration: `api.registerCommand` → `openclaw aimail register|deregister|status`
(4-step idempotent chain ported from Python register_agent_email; existing
gateway APIs only, no new API).

Config surface: none. Identity via pointer + agentmail.json (single source of
truth); gateway_url/api_key/webhook_secret all live in agentmail.json. The
plugin adds zero new config keys.

## 5. Phasing and acceptance

P0 — Verification (no code changes to packages)
  1. cordis loader: can a bundle mount its own sub-entries (self-mount in
     cordis.patch.yml)? Read cordis source; fallback R1 defined below.
  2. openclaw registerHttpRoute auth semantics (registry-registrators-network.ts).
  3. runEmbeddedAgent vs subagent.run session semantics (probe with a scratch plugin).
  4. Confirm mail-core loadConfigByAgentId semantics for openclaw agentId mapping.

P1 — Restructure (dsh side)
  - @aimail/mail-core: add MAIL_TOOLS registry (12 semantic defs, D7); text
    migrated verbatim from tool-mail; parity vitest vs amail_mcp_server.py
  - @aimail/mail strip → rc.9 (breaking: no apply/Context; consumers = this repo only)
    + new resolveByRecipient (exact → persona-strip); dsh inbound switches to it
    (fixes current alias-mail swallow as a side effect)
  - tool-mail + mail-inbound code → dsh-aimail src/tools.ts + src/inbound.ts
    (extract inbound handler into a testable pure function during migration)
  - cordis.patch.yml → persona + self-entries (or R1 fallback)
  - dsh-aimail rc.12, local install to ~/.dsh/profiles/web, full regression:
    22 vitest cases green + ping-pong E2E + real mail round-trip + unbound fail-loud.

P2 — openclaw-aimail
  - Skeleton (manifest, tsconfig, definePluginEntry) + 12 tools + identity chain
    Accept: openclaw plugins install (local tarball) → inspect lists 12 bare tools;
    agent trajectory shows send_mail bare-name call; unbound agent fails loud.
  - Inbound route + HMAC + delivery
    Accept: ping-pong (200 intercepted + three-stage logs); real inbound mail →
    agent run → reply with correct In-Reply-To/References; thread continuity via
    email_summary; mail addressed to a persona alias routes to the owning agent
    (resolveByRecipient fallback) instead of no_agent.
  - register/deregister/status commands
    Accept: new agentId register → receive loop closed; deregister → 3-step
    idempotent chain (MockClient unit test).

P3 — Wrap-up
  - Repo rename dsh-aimail → aimail-sdk-ts (GitHub redirect; update every
    package.json `repository` field + remote URL; npm coordinates unchanged)
  - READMEs (English, install + functionality only) for openclaw-aimail + root
  - Deprecate @aimail/tool-mail, @aimail/mail-inbound with pointer message
  - pnpm publish all (rc tags); verify npm view dist-tags

## 6. Risks and fallbacks

R1 (dsh self-mount): RESOLVED (P0.1). Entry `name` is a bare module specifier
    resolved by Node from the profile dir — the same mechanism that resolves
    `@aimail/mail` today. dsh-aimail declares subpath exports
    (`./mail-service`, `./tools`, `./inbound`) and its own cordis.patch.yml
    entries reference `dsh-aimail/<subpath>`. No shim packages needed.
R2 (registerHttpRoute auth): RESOLVED (P0.2). Two tiers: `"gateway"` (gateway
    auth enforced before routing) / `"plugin"` (no enforcement, handler owns
    auth). Use `"plugin"`; handler HMAC is the trust boundary; curl-verify in P2.
R3 (delivery mechanism): CORRECTED (P0.3). `runEmbeddedAgent` does NOT exist in
    the plugin SDK. Real surfaces: `api.runtime.subagent.run({sessionKey,
    message, toolsAlsoAllow, idempotencyKey, ...})` (session-scoped, no explicit
    agentId) and `api.runtime.gateway.request(method, params)` (dispatch a
    gateway method as the trusted plugin — explicit agent targeting). Try
    subagent.run first; if per-agent targeting is not achievable, switch to
    gateway.request. Decide empirically in P2.
R4 (openclaw as devDep): large install surface; devDependencies only, never
    published as a dependency of openclaw-aimail (peer).
R5 (repo rename): GitHub auto-redirects old URLs; update CI remote if any;
    no npm impact. Rollback = rename back (redirects are bidirectional).

## 7. Version matrix (post-approval)

| Package | From | To | Change |
|---------|------|----|--------|
| @aimail/mail-core | 0.1.0-rc.8 | 0.1.0-rc.9 | additive: MAIL_TOOLS semantic registry (D7) |
| @aimail/mail | 0.1.0-rc.8 | 0.1.0-rc.9 | breaking strip (internal consumers only) |
| dsh-aimail | 0.1.0-rc.11 | 0.1.0-rc.12 | internal restructure, behavior identical |
| openclaw-aimail | — | 0.1.0-rc.1 | new |
| @aimail/tool-mail | 0.1.0-rc.8 | DEPRECATED | code moved into dsh-aimail |
| @aimail/mail-inbound | 0.1.0-rc.8 | DEPRECATED | code moved into dsh-aimail |

## 8. Explicit non-goals

- No gateway API additions. No MCP. No pull/poll implementation in the plugin.
- No plugin-level config schema (identity stays pointer + agentmail.json).
- No changes to the agentmail Python repo in this plan (MCP retirement there
  is a separate action after P2 acceptance).
- mail-core stays behavior-identical except the additive MAIL_TOOLS registry
  (D7); anything else added there requires separate review.
- Semantic text lives in exactly ONE place (mail-core MAIL_TOOLS). A vitest
  parity case asserts the 12 entries match the Python `amail_mcp_server.py`
  registry (names + descriptions) so the TS↔Python contract cannot drift.
