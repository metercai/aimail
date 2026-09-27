/**
 * openclaw-aimail — AIMail plugin for OpenClaw.
 *
 * definePluginEntry: 15 tools (factory form, iterating MAIL_TOOLS) + inbound
 * HTTP route + register/deregister/status commands. No plugin-level config
 * schema — identity stays pointer + agentmail.json (single source of truth).
 */
import { definePluginEntry, type OpenClawPluginDefinition } from 'openclaw/plugin-sdk/plugin-entry'
import {
  ensureSystem,
  ensureBridgeRoutesForSystem,
  formatBridgeRouteLine,
  isBridgeRouteWarning,
  releaseAllSystems,
  setAgentIdentity,
} from '@aimail/mail-core'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { createMailTools } from './tools.js'
import { createInboundHandler, startInboundPull, INBOUND_PATH } from './inbound.js'
import { createAimailCommands } from './commands.js'
import { agentIdentity } from './identity.js'

// SDK-shipped board resources (role prompts/souls) live at the package root.
const boardRoot = path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'board')

const entry: OpenClawPluginDefinition = definePluginEntry({
  id: 'openclaw-aimail',
  name: 'AIMail',
  description:
    'AIMail email capability for OpenClaw: 15 mail/board tools, inbound email delivery with HMAC verification, register/deregister/status commands.',
  register(api) {
    // Outbound X-AIMail-Agent header (real detected host version, no guess)
    setAgentIdentity(agentIdentity())

    // SDK-shipped board resources → ~/.aimail/systems/*/board/ (idempotent;
    // never overwrites user-personalized files). Covers openclaw-only
    // machines that never install the Python SDK/CLI.
    try {
      releaseAllSystems(boardRoot)
    } catch (e) {
      // non-fatal (register/next start retries), but never silent: missing resources = packaging defect
      console.error(`[openclaw-aimail] board resource release failed: ${String(e)}`)
    }

    // SDK-shipped skill → ~/.openclaw/skills/agentmail/ (idempotent; skips
    // copy when identical). Symmetric with hermes _release_hermes_skills —
    // the chat agent learns the mail protocol from this SKILL.md.
    try {
      const skillSrc = path.join(
        path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'skills')
      const skillDst = path.join(os.homedir(), '.openclaw', 'skills', 'agentmail')
      fs.mkdirSync(skillDst, { recursive: true })
      for (const f of ['SKILL.md', 'DESCRIPTION.md']) {
        const from = path.join(skillSrc, f)
        const to = path.join(skillDst, f)
        if (!fs.existsSync(from)) {
          // missing package resources = packaging defect: loud but must not block the host
          console.error(`[openclaw-aimail] skill resource missing: ${from} ` +
            '(repo: run scripts/materialize-resources.sh; installed: reinstall the package)')
          continue
        }
        if (fs.existsSync(to) && fs.readFileSync(from).equals(fs.readFileSync(to))) continue
        fs.copyFileSync(from, to)
      }
    } catch (e) {
      // non-fatal (next plugin start retries), but never silent
      console.error(`[openclaw-aimail] skill release failed: ${String(e)}`)
    }

    // Install readiness: system activation lives ONCE, in `aimail
    // install --system-only` (L1 only) — reverse-call it when THIS platform has no
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

    // 15 bare-name tools (MAIL_TOOLS single source; identity from ctx.agentId)
    api.registerTool(createMailTools)

    // Inbound delivery route (in-gateway, no new port; auth=plugin, HMAC is
    // the trust boundary per R2)
    api.registerHttpRoute({
      path: INBOUND_PATH,
      auth: 'plugin',
      match: 'exact',
      handler: createInboundHandler(api),
    })

    // Pull entry (2026-09-27): an address activated by an activation CODE has
    // no push path (the gateway stores webhook_url=NULL for it), so this host
    // must fetch its own mail on a timer. Started AFTER the inbound route is
    // live (the pulled mail enters the same chain through that handler's core),
    // and ONLY for agent-scope bindings — the decision lives in mail-core
    // (startAgentPullEntries: `shared_addr_*` activation source), never here.
    // OpenClaw's plugin API exposes no dispose hook: the poll timers are
    // unref'd by mail-core, so they die with the process and can never keep it
    // alive (see poll-entry.ts pollSleep).
    void startInboundPull(api, {
      log: (line: string) => console.log(line),
    })
      .then((handles) => {
        if (handles.length) {
          console.log(
            `[openclaw-aimail] pull entry armed for ${handles.length} agent-scope binding(s)`,
          )
        }
      })
      .catch((e) => {
        console.warn(
          `[openclaw-aimail] pull entry failed to start: ${e instanceof Error ? e.message : String(e)}`,
        )
      })

    // Route side (owner ruling 2026-09-27: registration and route pairing are
    // two separate outcomes). The in-gateway route above is live, so this is the
    // right moment to (re-)pair every address of this system: the bridge deletes
    // routes whose target stays unreachable (probe interval x fail_threshold,
    // ~30s x 6 = 180s) and registration-time pushes land before the gateway
    // serves the plugin — the cache of that was a permanently dead inbound after
    // a host restart (production 2026-09-21/09-26). Idempotent, never fatal.
    void ensureBridgeRoutesForSystem()
      .then((outcomes) => {
        for (const o of outcomes) {
          const line = `[openclaw-aimail] ${formatBridgeRouteLine(o)}`
          if (isBridgeRouteWarning(o)) console.warn(line)
          else console.log(line)
        }
      })
      .catch((e) => {
        console.warn(
          `[openclaw-aimail] route ensure failed: ${e instanceof Error ? e.message : String(e)}`,
        )
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
        const cmd = program.command('aimail [args...]') as unknown as {
          description: (d: string) => {
            allowUnknownOption: (v?: boolean) => {
              action: (fn: (...args: unknown[]) => void) => unknown
            }
          }
        }
        ;(cmd
          .description('AIMail registration and status: register|register-all|deregister|status')
          .allowUnknownOption(true) as {
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
