/**
 * openclaw-aimail — AIMail plugin for OpenClaw.
 *
 * definePluginEntry: 15 tools (factory form, iterating MAIL_TOOLS) + inbound
 * HTTP route + register/deregister/status commands. No plugin-level config
 * schema — identity stays pointer + agentmail.json (single source of truth).
 */
import { installActivationSkillSync } from '../core/agent-level.js'
import type { OpenClawPluginDefinition } from 'openclaw/plugin-sdk/plugin-entry'
import {
  AGENT_TOOL_DEFS,
  releaseAllSystems,
  onBindingCreated,
  setProfileDirResolver,
  setAgentHomeResolver,
  setAgentIdentity,
  setSoulProvider,
  setSkillsProvider,
  combineSkillNames,
  packageResourcesRoot} from '../core/index.js'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { createMailTools } from './tools.js'
import { startInboundPull } from './inbound.js'
import { agentIdentity, ensureHooksWiring } from './identity.js'
import { resolveOpenclawSkillRoots } from './skills.js'
import { activateHost } from '../hosts/activate.js'

// SDK-shipped board resources (role prompts/souls) live at the package root.
const boardRoot = path.join(packageResourcesRoot(), 'board')

function definePluginEntry<T extends object & { configSchema?: unknown }>(entry: T): T {
  return {
    ...entry,
    get configSchema() {
      return entry.configSchema ?? { type: 'object', properties: {}, additionalProperties: false }
    },
  } as T
}

// 2026-11-25: all push-era / CLI-era switches are gone (no listener, no in-gateway route, no
// `aimail` subcommand). A binding is created by an ACTIVATION CODE via `aimail_address`, and nothing
// at startup can create one.
let pullHandles: Array<{ stop: () => void }> = []
const entry: OpenClawPluginDefinition = definePluginEntry({
  id: 'agent-aimail',
  name: 'AIMail',
  description:
    'AIMail email capability for OpenClaw: 15 mail/board tools, inbound email delivery with HMAC verification, register/deregister/status commands.',
  register(api) {
    // Host identification (#5): this entry is loaded by OpenClaw's plugin
    // loader; verify the environment matches before activating.
    activateHost('openclaw')
    setProfileDirResolver(() => path.join(os.homedir(), '.openclaw'))
    // CR-1: AgentSystemId is per agent instance, not the OS user.
    setAgentHomeResolver(() => path.join(os.homedir(), '.openclaw'))

    // Outbound X-AIMail-Agent header (real detected host version, no guess)
    setAgentIdentity(agentIdentity())

    // B3 layer fix (2026-11-21, QA findings B3): the inbound→session hop
    // dispatches to the host's internal /hooks/agent, whose endpoint EXISTs only
    // when the host config has hooks.enabled=true (openclaw dist
    // resolveHooksConfig: hooks.enabled !== true ⇒ null ⇒ 404 fall-through).
    // On a CLI-registered host the `aimail register`/`status` commands call
    // ensureHooksWiring(), but the SSE profile registers NO CLI commands (see
    // below: the legacy CLI flag), so a host in normal SSE mode never
    // gets hooks.token + hooks.enabled written — pulled mail is read (pending
    // 200) but the dispatch POST 404s and is never acked (re-pulled forever).
    // Wire it here, exactly as the CLI path does (idempotent: 'kept' when
    // already present, never clobbers an admin's enabled=false).
    try {
      const w = ensureHooksWiring()
      if (w === 'created') {
        console.error('[agent-aimail] hooks wiring created (hooks.token + hooks.enabled=true) — /hooks/agent dispatch now enabled')
      } else if (w === 'disabled') {
        console.error('[agent-aimail] hooks DISABLED by host config (hooks.enabled=false) — inbound mail cannot reach the agent; enable it in openclaw.json')
      } else if (w === 'no-config') {
        console.error('[agent-aimail] no openclaw.json found — hooks wiring skipped (inbound dispatch will 404 until the host config exists)')
      }
    } catch (e) {
      // Non-fatal (a config race at boot): the next register/restart retries.
      console.error(`[agent-aimail] hooks wiring check failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`)
    }

    // Per-agent SOUL.md: resolve from the OWNING agent's workspace, mirroring
    // openclaw's own resolveAgentWorkspaceDir(cfg, agentId) contract:
    //   1. explicit per-agent agents.{id}.workspace
    //   2. legacy data-owner (main) inherits agents.defaults.workspace
    //   3. any other agent: {defaults.workspace}/{agentId}
    // SOUL.md is a bootstrap file OF the agent workspace (openclaw
    // skipOptionalBootstrapFiles: SOUL.md/USER.md/IDENTITY.md), so the
    // per-agent SOUL lives at <resolved workspace>/SOUL.md. Missing ⇒ ''
    // (a fresh agent workspace has no SOUL.md until its bootstrap runs).
    const ocHome = path.join(os.homedir(), '.openclaw')
    const readOcConfig = (): Record<string, any> => {
      try {
        return JSON.parse(fs.readFileSync(path.join(ocHome, 'openclaw.json'), 'utf-8'))
      } catch {
        return {}
      }
    }
    const resolveAgentWorkspaceDir = (agentId: string): string => {
      const cfg = readOcConfig()
      const agents = cfg.agents ?? {}
      const entry = agents[agentId]
      const explicit = entry?.workspace?.trim?.()
      if (explicit) return explicit.replace(/^~(?=\/|$)/, os.homedir())
      const defaults = agents.defaults ?? {}
      const fallback = defaults.workspace?.trim?.()
      if (agentId === 'main') {
        if (fallback) return fallback.replace(/^~(?=\/|$)/, os.homedir())
        return path.join(ocHome, 'workspace')
      }
      if (fallback) return path.join(fallback.replace(/^~(?=\/|$)/, os.homedir()), agentId)
      return path.join(ocHome, 'workspace', agentId)
    }
    setSoulProvider((cfg) => {
      const agentId = (cfg.agent_id ?? 'main').trim() || 'main'
      const p = path.join(resolveAgentWorkspaceDir(agentId), 'SOUL.md')
      try {
        return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''
      } catch {
        return ''
      }
    })
    // OpenClaw skills: report every skill THIS AGENT's configuration can
    // load (owner ruling 2026-10-23 — shared roots included, not just
    // per-agent ones). Root set + order mirrors openclaw's runtime
    // resolveWorkspaceSkillSourcePlan; names via the shared enumerator.
    setSkillsProvider((cfg) => {
      const agentId = (cfg.agent_id ?? 'main').trim() || 'main'
      return combineSkillNames(resolveOpenclawSkillRoots(agentId))
    })

    // SDK-shipped board resources → ~/.aimail/systems/*/board/ (idempotent;
    // never overwrites user-personalized files). Covers openclaw-only
    // machines that never install the Python SDK/CLI.
    try {
      releaseAllSystems(boardRoot)
    } catch (e) {
      // non-fatal (register/next start retries), but never silent: missing resources = packaging defect
      console.error(`[agent-aimail] board resource release failed: ${String(e)}`)
    }

    // SDK-shipped skill → ~/.openclaw/skills/agentmail/ (idempotent; skips
    // copy when identical). Symmetric with hermes _release_hermes_skills —
    // the chat agent learns the mail protocol from this SKILL.md.
    try {
      const skillSrc = path.join(packageResourcesRoot(), 'skills')
      const skillDst = path.join(os.homedir(), '.openclaw', 'skills', 'agentmail')
      fs.mkdirSync(skillDst, { recursive: true })
      for (const f of ['SKILL.md', 'DESCRIPTION.md']) {
        const from = path.join(skillSrc, f)
        const to = path.join(skillDst, f)
        if (!fs.existsSync(from)) {
          // missing package resources = packaging defect: loud but must not block the host
          console.error(`[agent-aimail] skill resource missing: ${from} ` +
            '(repo: stage it via the package prepack; installed: reinstall the package)')
          continue
        }
        if (fs.existsSync(to) && fs.readFileSync(from).equals(fs.readFileSync(to))) continue
        fs.copyFileSync(from, to)
      }
      installActivationSkillSync(path.join(os.homedir(), '.openclaw', 'skills'))
    } catch (e) {
      // non-fatal (next plugin start retries), but never silent
      console.error(`[agent-aimail] skill release failed: ${String(e)}`)
    }

    // 15 bare-name tools. The host needs explicit `names`
    // metadata when a factory returns an array; without it every name is
    // registered as an empty tool surface entry.
    api.registerTool((ctx) => createMailTools(ctx, resolveAgentWorkspaceDir), {
      names: [...AGENT_TOOL_DEFS.map((t) => t.name)],
    })

    // No inbound HTTP route: the in-gateway push endpoint was retired 2026-11-25. Mail arrives
    // through `startInboundPull` below (SSE/pull), so there is nothing to register and no HMAC step.

    // Binding comes from an ACTIVATION CODE (aimail_address bind), never from installing the plugin:
    // there is no eager default-agent bind any more (owner 2026-11-25: "不存在自动默认绑定，都需要激活码").
    // What remains here is arming the pull entry for whatever bindings exist.
    {
      void (async () => {
        // Startup never creates a mailbox; an activation code does.
        // Pull entry (2026-09-27): an address activated by an activation CODE
        // has no push path (the gateway stores webhook_url=NULL for it), so
        // this host must fetch its own mail on a timer. Started AFTER the
        // inbound route is live and the default agent is bound; ONLY for
        // agent-scope bindings — the decision lives in mail-core
        // (startAgentPullEntries: `shared_addr_*` activation source), never
        // here. OpenClaw's plugin API exposes no dispose hook: the poll timers
        // are unref'd by mail-core, so they die with the process and can never
        // keep it alive (see poll-entry.ts pollSleep).
        void startInboundPull(api, {
          // Diagnostic line ⇒ stderr: op path stdout exactly one line JSON (Contract §4.1(2)); stdout pollution ⇒ CLI Transport judges "gate output abnormal"
          log: (line: string) => console.error(line),
        }).then((handles) => {
          if (handles.length) {
            console.error(
              `[agent-aimail] pull entry armed for ${handles.length} agent-scope binding(s)`,
            )
          }
        }).catch((e: unknown) => {
          console.warn(
            `[agent-aimail] pull entry failed to start: ${e instanceof Error ? e.message : String(e)}`,
          )
        })

        // Inbound notification (owner ruling 2026-09-28, SDK de-bridging): the
        // in-gateway route is live AND the default agent is bound, so this
        // plugin tells the environment master (the `aimail` CLI), per address
        // it serves, that inbound is live: `aimail address -a <addr>
        // --inbound-live --inbound-url <url>`. The confirmed URL is the
        // in-gateway endpoint this plugin's route actually serves on (the
        // OpenClaw gateway HTTP port + the contract inbound path — owner
        // ruling 2026-10-25: every host carries its real serving endpoint, no
        // exceptions). Best-effort and never fatal (argv, 4s timeout, no CLI ⇒
        // one log line).
      })().catch((e: unknown) => {
        console.warn(
          `[agent-aimail] eager binding gate failed (degraded to unbound path): ${e instanceof Error ? e.message : String(e)}`,
        )
      })
    }

    // Hot-add after in-agent address/system activation.
    onBindingCreated(async () => {
      for (const h of pullHandles) h.stop()
      pullHandles = []
      try {
        pullHandles = await startInboundPull(api, { log: (line: string) => console.error(line) })
      } catch { /* next restart retries */ }
    })

  },
})

export default entry
