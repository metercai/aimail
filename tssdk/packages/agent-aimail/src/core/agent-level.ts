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
import { packageResourcesRoot } from './resources-root.js'
import * as path from 'node:path'


import { AGENT_SKILL_NAME } from './contract.js'
import { AGENT_TOOL_NAMES } from './agent-tools.js'
import { saveInboundSnapshot, saveLocalMeta } from './meta.js'
import {
  processInboundMail,
} from './preprocess.js'
import { onBindingCreated } from './binding-hooks.js'
import {
  startAgentPullEntry,
  startAgentPullEntries,
  stopAgentPullEntries,
  type AgentPullHandle,
  type StartAgentPullEntryOptions,
  type AgentPullOverrides,
  type PulledMail,
  type StartAgentPullEntriesOptions,
} from './poll-entry.js'
import { MAIL_TOOLS, type MailToolDef } from './tool-registry.js'
import type { AgentConfig, EnrichedPayload, InboundPayload } from './types.js'

/** Tools that are meaningful for an agent-level mailbox (member-scope board). */
export { AGENT_TOOL_NAMES }

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
  const src = path.join(packageResourcesRoot(), 'skills', 'SKILL.md')
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
  /**
   * Live list: grows when a binding is activated while this process runs
   * (hot-add) — the host does not restart.
   */
  handles: AgentPullHandle[]
  tools: MailToolDef[]
  skill?: InstallAgentSkillResult | undefined
  /** Unregister the hot-add hook and stop every loop (idempotent). */
  stop: () => void
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
  const entryOpts: StartAgentPullEntryOptions = { onEmail: deliver }
  if (opts.env !== undefined) entryOpts.env = opts.env
  if (opts.overrides !== undefined) entryOpts.overrides = opts.overrides
  if (opts.log !== undefined) entryOpts.log = opts.log
  if (opts.clientFor !== undefined) entryOpts.clientFor = opts.clientFor

  const handles = await startAgentPullEntries(entryOpts)

  // Hot-add: the normal agent-level order is "host boots (0 bindings) →
  // activation later". Start a loop for the binding the moment it is persisted.
  const off = onBindingCreated(async (cfg: AgentConfig) => {
    if (handles.some((h) => h.email === cfg.email)) return
    const h = await startAgentPullEntry(cfg, entryOpts)
    if (h) handles.push(h)
  })

  const stop = () => {
    off()
    stopAgentPullEntries(handles)
    handles.length = 0
  }

  return { handles, tools: agentTools(), skill, stop }
}
