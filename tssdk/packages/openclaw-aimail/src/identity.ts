/**
 * openclaw-aimail identity — resolves the AIMail binding for an OpenClaw
 * agent id.
 *
 * Identity chain (mirrors dsh's exec.agent.id, Python detect_system_id):
 *   factory ctx.agentId
 *   → ~/.openclaw/.agentmail pointer (sole identity source; no env override,
 *     no cross-system scan — established convention)
 *   → system_id → @aimail/mail-core loadConfigByAgentId → AgentConfig
 * Unbound agents fail loud ("agentmail not configured for this agent").
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { hasAnySystem, loadConfigByAgentId, type AgentConfig } from '@aimail/mail-core'

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

/**
 * Outbound X-AIMail-Agent identity: walk up from this module to the host
 * `openclaw` package.json (managed installs link the host peer at
 * <plugin>/node_modules/openclaw). Detect, never guess; cached on success;
 * falls back to 'openclaw/unknown' only when the host cannot be located.
 */
let _identity = ''
export function agentIdentity(): string {
  if (_identity) return _identity
  try {
    let dir = path.dirname(new URL(import.meta.url).pathname)
    for (let i = 0; i < 8; i++) {
      const p = path.join(dir, 'node_modules', 'openclaw', 'package.json')
      if (fs.existsSync(p)) {
        const v = String(JSON.parse(fs.readFileSync(p, 'utf-8')).version ?? '')
        if (v) {
          _identity = `openclaw/${v}`
          return _identity
        }
      }
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  } catch {
    /* fallthrough */
  }
  return 'openclaw/unknown'
}

/** Read the ~/.openclaw/.agentmail pointer. Missing/corrupt → {}. */
export async function readPointer(): Promise<SystemPointer> {
  try {
    const raw = fs.readFileSync(POINTER_PATH, 'utf-8')
    const parsed = JSON.parse(raw) as SystemPointer
    if (parsed && typeof parsed === 'object') return parsed
  } catch {
    /* missing or unreadable → empty pointer */
  }
  return {}
}

/**
 * Resolve the AIMail config for an openclaw agent id.
 * Throws when the pointer is missing or the agent is unbound.
 */
export async function resolveConfigForAgent(
  agentId: string | undefined,
): Promise<AgentConfig> {
  const id = agentId && agentId.trim() ? agentId.trim() : 'main'
  const ptr = await readPointer()
  const systemId = ptr.system_id ?? ''
  if (!systemId) {
    const fix = hasAnySystem()
      ? 'Run: agentmail install --home ~/.openclaw (或 openclaw aimail register)'
      : 'Machine has no agentmail environment yet. Run: agentmail init, then agentmail install --home ~/.openclaw'
    throw new Error(
      `agentmail not configured for this agent — no ~/.openclaw/.agentmail pointer. ${fix}`,
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
