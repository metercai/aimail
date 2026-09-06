/**
 * openclaw-aimail — AIMail plugin for OpenClaw.
 *
 * definePluginEntry: 12 tools (factory form, iterating MAIL_TOOLS) + inbound
 * HTTP route + register/deregister/status commands. No plugin-level config
 * schema — identity stays pointer + agentmail.json (single source of truth).
 */
import { definePluginEntry, type OpenClawPluginDefinition } from 'openclaw/plugin-sdk/plugin-entry'
import { ensureSystem, releaseAllSystems, setAgentIdentity } from '@aimail/mail-core'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createMailTools } from './tools.js'
import { createInboundHandler, INBOUND_PATH } from './inbound.js'
import { createAimailCommands } from './commands.js'
import { agentIdentity } from './identity.js'

// SDK-shipped board resources (role prompts/souls) live at the package root.
const boardRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'board')

const entry: OpenClawPluginDefinition = definePluginEntry({
  id: 'openclaw-aimail',
  name: 'AIMail',
  description:
    'AIMail email capability for OpenClaw: 12 mail/board tools, inbound email delivery with HMAC verification, register/deregister/status commands.',
  register(api) {
    // Outbound X-AIMail-Agent header (real detected host version, no guess)
    setAgentIdentity(agentIdentity())

    // SDK-shipped board resources → ~/.aimail/systems/*/board/ (idempotent;
    // never overwrites user-personalized files). Covers openclaw-only
    // machines that never install the Python SDK/CLI.
    try {
      releaseAllSystems(boardRoot)
    } catch {
      // non-fatal seed; re-released on register/next start
    }

    // Install readiness: system activation lives ONCE, in `aimail
    // ensure-system` (L1 only) — reverse-call it when THIS platform has no
    // owning system yet. Never platform wiring → the install↔plugin call
    // graph stays acyclic. No env/code → actionable warn on stderr.
    {
      const platformHome =
        process.env.AIMAIL_SYSTEM_HOME?.trim() ||
        path.join(os.homedir(), '.openclaw')
      void ensureSystem({ systemHome: platformHome })
        .then((r) => {
          if (r.ok) {
            if (r.activated) {
              console.log(`[openclaw-aimail] system activated: ${r.systemId}`)
            }
          } else {
            const hint = r.hint ? ` (${r.hint})` : ''
            console.warn(`[openclaw-aimail] no aimail system yet — ${r.error ?? 'unknown'}` + hint)
          }
        })
        .catch((e) => {
          console.warn(
            `[openclaw-aimail] system ensure failed: ${e instanceof Error ? e.message : String(e)}`,
          )
        })
    }

    // 12 bare-name tools (MAIL_TOOLS single source; identity from ctx.agentId)
    api.registerTool(createMailTools)

    // Inbound delivery route (in-gateway, no new port; auth=plugin, HMAC is
    // the trust boundary per R2)
    api.registerHttpRoute({
      path: INBOUND_PATH,
      auth: 'plugin',
      match: 'exact',
      handler: createInboundHandler(api),
    })

    // Registration / status commands
    for (const command of createAimailCommands()) {
      api.registerCommand(command)
    }
  },
})

export default entry
