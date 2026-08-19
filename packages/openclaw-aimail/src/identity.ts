/**
 * openclaw-aimail identity — resolves the AgentMail binding for an OpenClaw
 * agent id.
 *
 * Identity chain (mirrors dsh's exec.agent.id, Python detect_system_id):
 *   factory ctx.agentId
 *   → ~/.openclaw/.agentmail pointer (sole identity source; no env override,
 *     no cross-system scan — established convention)
 *   → system_id → @aimail/mail-core loadConfigByAgentId → AgentConfig
 * Unbound agents fail loud ("agentmail not configured for this agent").
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { loadConfigByAgentId, type AgentConfig } from '@aimail/mail-core'

/** OpenClaw agent pointer file: {system_id, email}. */
const POINTER_PATH = path.join(
  process.env.HOME ?? process.env.USERPROFILE ?? '',
  '.openclaw',
  '.agentmail',
)

export interface SystemPointer {
  system_id?: string
  email?: string
}

/** Read the ~/.openclaw/.agentmail pointer. Missing/corrupt → {}. */
export async function readPointer(): Promise<SystemPointer> {
  try {
    const raw = await fs.readFile(POINTER_PATH, 'utf-8')
    const parsed = JSON.parse(raw) as SystemPointer
    if (parsed && typeof parsed === 'object') return parsed
  } catch {
    /* missing or unreadable → empty pointer */
  }
  return {}
}

/**
 * Resolve the AgentMail config for an openclaw agent id.
 * Throws when the pointer is missing or the agent is unbound.
 */
export async function resolveConfigForAgent(
  agentId: string | undefined,
): Promise<AgentConfig> {
  const id = agentId && agentId.trim() ? agentId.trim() : 'main'
  const ptr = await readPointer()
  const systemId = ptr.system_id ?? ''
  if (!systemId) {
    throw new Error(
      'agentmail not configured for this agent — no ~/.openclaw/.agentmail pointer. Run: openclaw aimail register',
    )
  }
  const cfg = await loadConfigByAgentId(systemId, id)
  if (!cfg) {
    throw new Error(
      `agentmail not configured for agent '${id}' (system ${systemId}). Run: openclaw aimail register`,
    )
  }
  return cfg
}
