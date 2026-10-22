/**
 * openclaw-aimail — AIMail plugin for OpenClaw.
 *
 * definePluginEntry: 15 tools (factory form, iterating MAIL_TOOLS) + inbound
 * HTTP route + register/deregister/status commands. No plugin-level config
 * schema — identity stays pointer + agentmail.json (single source of truth).
 */
import { definePluginEntry, type OpenClawPluginDefinition } from 'openclaw/plugin-sdk/plugin-entry'
import {
  notifyInboundForSystem,
  formatInboundNotifyLine,
  isInboundNotifyWarning,
  releaseAllSystems,
  setAgentIdentity, packageResourcesRoot} from '../core/index.js'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { createMailTools } from './tools.js'
import { createInboundHandler, startInboundPull, INBOUND_PATH } from './inbound.js'
import { createAimailCommands } from './commands.js'
import { agentIdentity, resolveAgentId, tryAutoBindOnce, DEFAULT_AGENT_ID } from './identity.js'
import { activateHost } from '../hosts/activate.js'

// SDK-shipped board resources (role prompts/souls) live at the package root.
const boardRoot = path.join(packageResourcesRoot(), 'board')

const entry: OpenClawPluginDefinition = definePluginEntry({
  id: 'agent-aimail',
  name: 'AIMail',
  description:
    'AIMail email capability for OpenClaw: 15 mail/board tools, inbound email delivery with HMAC verification, register/deregister/status commands.',
  register(api) {
    // Host identification (#5): this entry is loaded by OpenClaw's plugin
    // loader; verify the environment matches before activating.
    activateHost('openclaw')

    // Outbound X-AIMail-Agent header (real detected host version, no guess)
    setAgentIdentity(agentIdentity())

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
    } catch (e) {
      // non-fatal (next plugin start retries), but never silent
      console.error(`[agent-aimail] skill release failed: ${String(e)}`)
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

    // Eager default-agent binding (dsh parity, 2026-10-16 batch): the
    // `openclaw plugins install` path never runs the CLI's register_default
    // step, so nothing else writes the default agent's binding — without a
    // proactive bind a plugin-installed host has a system but no binding, and
    // the gateway can dispatch/welcome no mail. One call covers system
    // readiness (owning system short-circuit, else CLI L1 reverse-call) + the
    // idempotent bind. It runs AFTER the in-gateway route below is registered
    // (the pulled mail enters that same chain), and arms the pull entry + live
    // notify only once the default agent is bound. The agent id is the host's
    // own (resolveAgentId; default 'main') — the same id the inbound dispatch
    // sends to /hooks/agent. Never fatal: on failure the host degrades to the
    // unbound path (the lazy tool path retries).
    {
      void (async () => {
        const agentId = resolveAgentId() || DEFAULT_AGENT_ID
        await tryAutoBindOnce(agentId)
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
          // 诊断行 ⇒ stderr:op 路径 stdout 恰一行 JSON(契约 §4.1(2));stdout 污染 ⇒ CLI Transport 判"门输出异常"
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

        // Inbound notification (owner ruling 2026-09-28, SDK 去桥化): the
        // in-gateway route is live AND the default agent is bound, so this
        // plugin tells the environment master (the `aimail` CLI), per address
        // it serves, that inbound is live: `aimail address -a <addr>
        // --inbound-live`. Best-effort and never fatal (argv, 4s timeout, no
        // CLI ⇒ one log line).
        void notifyInboundForSystem('live')
          .then((outcomes) => {
            for (const o of outcomes) {
              const line = `[agent-aimail] ${formatInboundNotifyLine(o, 'live')}`
              // 诊断行 ⇒ stderr(警告/普通分流,契约"failure 是 warning line"):op 路径 stdout 恰一行 JSON(§4.1(2))
              if (isInboundNotifyWarning(o)) console.warn(line)
              else console.error(line)
            }
          })
          .catch((e: unknown) => {
            console.warn(
              `[agent-aimail] inbound notify failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`,
            )
          })
      })().catch((e: unknown) => {
        console.warn(
          `[agent-aimail] eager binding gate failed (degraded to unbound path): ${e instanceof Error ? e.message : String(e)}`,
        )
      })
    }

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
