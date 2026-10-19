/**
 * Agent-level runtime — scenario 1 (address-code mailbox, no CLI, no adapter).
 *
 * The prompt-driven path needs three things a host must not hand-roll: the
 * agent-level toolset, the skill file, and the pull loop that feeds the host's
 * inbound processing. They live here so every TypeScript host calls the same,
 * tested code. Python counterpart: `pysdk/aimail_agent.py`.
 *
 * Board tools ARE part of the toolset: an address-code mailbox can be a board
 * MEMBER — the invite mail carries its token and the inbound chain stores it
 * (board_creds.json), after which the board routes accept the Bearer token.
 * Creating a board is not an agent-level action (the address-code product
 * grants 0 boards), so the tools are member-scope only.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'

import { AGENT_SKILL_NAME } from './contract.js'
import { saveInboundSnapshot, saveLocalMeta } from './meta.js'
import {
  processInboundMail,
} from './preprocess.js'
import {
  startAgentPullEntries,
  type AgentPullHandle,
  type AgentPullOverrides,
  type PulledMail,
  type StartAgentPullEntriesOptions,
} from './poll-entry.js'
import { MAIL_TOOLS, type MailToolDef } from './tool-registry.js'
import type { AgentConfig, EnrichedPayload, InboundPayload } from './types.js'

/** Tools that are meaningful for an agent-level mailbox (member-scope board). */
export const AGENT_TOOL_NAMES = [
  'activate_address_code',
  'send_mail',
  'manage_contacts',
  'contact_profile',
  'set_contact_profile',
  'email_summary',
  'set_email_summary',
  'search_mail',
  'set_public_whoami',
  'board_status',
  'board_task_list',
  'board_task_show',
  'board_members',
  'board_roles',
  'board_heartbeat',
] as const

const AGENT_TOOL_SET: ReadonlySet<string> = new Set(AGENT_TOOL_NAMES)

/**
 * The agent-level toolset, ready to register with the host: same entries as
 * `MAIL_TOOLS` (name/description/parameters/handler), filtered to the tools an
 * address-code mailbox can actually execute.
 */
export function agentTools(): MailToolDef[] {
  return MAIL_TOOLS.filter((tool) => AGENT_TOOL_SET.has(tool.name))
}

export interface InstallAgentSkillResult {
  path: string
  installed: boolean
}

/**
 * Copy the shipped skill into `<skillsDir>/<skill name>/SKILL.md`.
 * Idempotent: rewrites only when the content differs.
 */
export async function installAgentSkill(
  skillsDir: string,
  opts: { subdir?: string } = {},
): Promise<InstallAgentSkillResult> {
  const subdir = opts.subdir ?? AGENT_SKILL_NAME
  const src = fileURLToPath(new URL('../resources/skills/SKILL.md', import.meta.url))
  const text = await fs.readFile(src, 'utf-8')
  const dest = path.join(skillsDir, subdir, 'SKILL.md')
  let same = false
  try {
    same = (await fs.readFile(dest, 'utf-8')) === text
  } catch {
    same = false
  }
  if (!same) {
    await fs.mkdir(path.dirname(dest), { recursive: true })
    await fs.writeFile(dest, text)
  }
  return { path: dest, installed: !same }
}

export interface AgentLevelOptions {
  /** When given, install the shipped skill there before starting the loop. */
  skillsDir?: string | undefined
  /**
   * Your agent turn. The default inbound chain runs the SDK's shared pipeline
   * (`processInboundMail` — the same chain push feeds) and hands the enriched
   * payload here; ping/pong frames are consumed by the pipeline and never
   * reach this callback.
   */
  onAgent?: ((enriched: EnrichedPayload, cfg: AgentConfig, mail: PulledMail) => void | Promise<void>) | undefined
  /**
   * Raw-delivery escape hatch: replaces the whole inbound chain with your own
   * (no preprocessing).
   */
  onEmail?: ((cfg: AgentConfig, mail: PulledMail) => void | Promise<void>) | undefined
  env?: NodeJS.ProcessEnv | undefined
  overrides?: AgentPullOverrides | undefined
  log?: ((line: string) => void) | undefined
  /** Test seam: build the request client for a binding. */
  clientFor?: StartAgentPullEntriesOptions['clientFor'] | undefined
}

export interface AgentLevelRuntime {
  handles: AgentPullHandle[]
  tools: MailToolDef[]
  skill?: InstallAgentSkillResult | undefined
}

/**
 * Start the agent-level runtime: optional skill install + toolset + pull loop.
 * Never opens a port: each pulled mail is handed to the inbound processing
 * inside this process.
 */
export async function startAgentLevel(
  opts: AgentLevelOptions = {},
): Promise<AgentLevelRuntime> {
  const skill = opts.skillsDir ? await installAgentSkill(opts.skillsDir) : undefined
  const deliver =
    opts.onEmail ??
    (async (cfg: AgentConfig, mail: PulledMail) => {
      const enriched = await processInboundMail(
        (mail.body ?? {}) as InboundPayload,
        (mail.headers ?? {}) as Record<string, string>,
        { systemId: cfg.system_id, email: cfg.email },
      )
      // null = the pipeline consumed the frame (ping/pong): no agent turn.
      if (enriched === null) return
      // Snapshot parity with the Python chain (store_inbound_message): local
      // meta always, the snapshot when save_raw_snapshots is on. Without it the
      // local search tool could never see inbound mail.
      const mid = String((enriched as Record<string, unknown>)['message_id'] ?? '')
      if (mid) {
        if (cfg.save_raw_snapshots !== false) {
          await saveInboundSnapshot(
            cfg.email, mid, enriched as unknown as Record<string, unknown>,
            (enriched as Record<string, unknown>)['references'] as string | string[] | undefined,
            cfg.email,
          )
        } else {
          // snapshot off ⇒ still record meta (threading depends on it)
          await saveLocalMeta(cfg.email, mid,
            (enriched as Record<string, unknown>)['references'] as string | string[] | undefined,
            cfg.email, 'inbound')
        }
      }
      if (opts.onAgent) await opts.onAgent(enriched, cfg, mail)
    })
  const pull: Parameters<typeof startAgentPullEntries>[0] = { onEmail: deliver }
  if (opts.env !== undefined) pull.env = opts.env
  if (opts.overrides !== undefined) pull.overrides = opts.overrides
  if (opts.log !== undefined) pull.log = opts.log
  if (opts.clientFor !== undefined) pull.clientFor = opts.clientFor
  const handles = await startAgentPullEntries(pull)
  return { handles, tools: agentTools(), skill }
}
