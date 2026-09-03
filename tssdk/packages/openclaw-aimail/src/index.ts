/**
 * openclaw-aimail — AIMail plugin for OpenClaw.
 *
 * definePluginEntry: 12 tools (factory form, iterating MAIL_TOOLS) + inbound
 * HTTP route + register/deregister/status commands. No plugin-level config
 * schema — identity stays pointer + agentmail.json (single source of truth).
 */
import { definePluginEntry, type OpenClawPluginDefinition } from 'openclaw/plugin-sdk/plugin-entry'
import { releaseAllSystems, setAgentIdentity } from '@aimail/mail-core'
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
