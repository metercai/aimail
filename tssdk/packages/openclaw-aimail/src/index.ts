/**
 * openclaw-aimail — AIMail plugin for OpenClaw.
 *
 * definePluginEntry: 13 tools (factory form, iterating MAIL_TOOLS) + inbound
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

    // CLI surface: manifest cliCommands is help-metadata only — the actual
    // `openclaw aimail ...` dispatch registers via api.registerCli.
    if (typeof (api as { registerCli?: unknown }).registerCli === 'function') {
      api.registerCli(
        (cliCtx) => {
        const program = cliCtx.program as unknown as {
          command: (name: string, opts?: { hidden?: boolean }) => {
            description: (d: string) => unknown
            action: (fn: (...args: unknown[]) => void) => unknown
          }
        }
        const cmd = program.command('aimail')
        ;(cmd.description('AIMail registration and status: register|register-all|deregister|status') as {
          action: (fn: (...args: unknown[]) => void) => unknown
        }).action(async (...args: unknown[]) => {
            const { handleCommand } = await import('./commands.js')
            const argv = (args[0] as string[] | undefined) ?? []
            // CLI 调用与 chat 命令同构:args 字符串 + senderIsOwner(本机操作者)
            const result = await handleCommand({
              args: argv.join(' '),
              channel: 'cli',
              isAuthorizedSender: true,
              senderIsOwner: true,
            } as never)
            const text = result && typeof result === 'object' && 'text' in (result as Record<string, unknown>)
              ? String((result as Record<string, unknown>).text)
              : JSON.stringify(result)
            process.stdout.write(text + '\n')
          })
        },
        {
          commands: ['aimail'],
          descriptors: [{
            name: 'aimail',
            description: 'AIMail registration and status: register|register-all|deregister|status',
            hasSubcommands: true,
          }],
        },
      )
    }
  },
})

export default entry
