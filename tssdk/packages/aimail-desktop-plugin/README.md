# @aimail/desktop-plugin — AIMail as a PI-Desktop plugin

The multi-address (`M`) face of AIMail: `pi.net.fetch` pulls the gateway queue,
and each thread lands in the session the host already owns.

## Why a bundled plugin instead of an npm dependency

PI-Desktop plugins are not `node_modules` consumers: the host evaluates one
`main.js` inside its own plugin process and injects a single global, `pi`. So the
SDK is bundled at build time (`agent-aimail/desktop` + `agent-aimail/core`) and
the artifact is a `.piplug` — a store-only zip whose root holds `manifest.json`
and `main.js`.

## Build and smoke

```bash
pnpm install                                  # once, at tssdk/
pnpm --filter @aimail/desktop-plugin build    # → packages/aimail-desktop-plugin/dist/
node packages/aimail-desktop-plugin/scripts/smoke.mjs
```

`build` produces the three files the host needs: `main.js` (bundled CommonJS),
`manifest.json`, and a `package.json` marker so Node reads the bundle as CJS.

`smoke` loads the built bundle into plain Node with a fake `pi` and asserts the
wire: pull one queue round → allocate the thread's session → deliver → ack; then
the same round with the host refusing the write → **no ack**. It covers the
payload shapes and the ack rule, not the host's own permissions or sandbox.

## Packaging belongs to QA

The installable artifact and the pipeline that builds it are owned by 质检, not
by this package. What this package guarantees is the **input directory**
(`dist/`) plus the constraints below — verified against the host source so they
do not have to be re-derived:

| Constraint | Value / rule | Source |
|---|---|---|
| Archive | zip, **store only** (method 0); any other method is rejected | `crates/host-core` `extract_zip_bytes`; PI-Desktop ADR 0007 |
| Layout | `manifest.json` **at the archive root**, beside `main.js` | ADR 0007 |
| Caps | ≤ 2000 files, ≤ 50 MiB, no symlinks | `plugin-devkit/src/walk.ts` |
| Never ship | `.git/`, `node_modules/`, `.env*`, `.npmrc`, `*.pem`, `*.key` | same |
| Integrity | sha256 over the archive bytes | `plugin-devkit/src/pack.ts` |
| Directory to pack | `packages/aimail-desktop-plugin/dist/` (build output only) | this package |
| Version | `manifest.version` must equal `package.json` version — the build refuses otherwise | this package |

## Host contract this plugin relies on

| Need | Host API | Permission |
|---|---|---|
| pull mail | `pi.net.fetch` (request/response only ⇒ `mode: 'poll'`) | `net.fetch`, `net.anyHost` |
| find the thread's session | `pi.session.list` | `session.read.own` |
| deliver into a session | `pi.session.appendMessage` | `session.update.all` |
| allocate a session for a new thread | `pi.session.import` (no `session.create` exists) | `session.import` |
| stay resident | `contributes.services` | `background.service` |

The delivery and routing rules — including "never acknowledge a mail the host did
not accept" — live in `agent-aimail/desktop` and are covered by
`packages/agent-aimail/test/desktop/`. Contract §11.5 records the one deliberate
compromise: a session allocated by `import` is `originKind: "imported"`, and its
title carries the thread root because the host exposes no thread index.

## Known limits

- A user who renames an imported session detaches it from its thread; the next
  mail of that thread starts a new session (no host API maps a title back).
- Direct-reply routing (contract §9 step 3) needs the outbound index, which the
  agent-side face owns. Without it a mail whose parent was ours still lands in
  the thread's session, never in a stranger's.
- `apiKey` lives in the plugin's settings file, which the host keeps in plain
  text until secure storage exists (the manifest schema rejects `secret`
  settings for that reason).
