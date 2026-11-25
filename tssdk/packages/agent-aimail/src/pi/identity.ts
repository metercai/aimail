/**
 * pi-aimail identity — resolves the AIMail binding for a pi agent.
 *
 * Identity chain (single source of truth, mirrors dsh-aimail / openclaw-aimail):
 *   ~/.pi/POINTER_FILE pointer ({system_id, email})
 *   → system_id scoped → @aimail/mail-core loadConfigByAgentId/loadConfigByEmail
 *
 * Auto-bind: system readiness + default-agent binding live ONCE in
 * mail-core binding sources (dsh/pi/openclaw parity,
 * batch 2026-10-16). The host start entry calls it BEFORE the inbound
 * listener is gated up ("port up" = "binding written"); the lazy path here
 * is the same call for the tool-resolution flow. The pointer is package-
 * level: written here, never in mail-core.
 *
 * Outbound X-AIMail-Agent: walk up from this module to the installed pi
 * package.json (@earendil-works/pi-coding-agent); detect, never guess.
 */
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  hasAnySystem,
  POINTER_FILE,
  loadConfigByEmail,
  setAgentIdentity,
  type AgentConfig,
} from '../core/index.js'

const POINTER_PATH = path.join(
  process.env.HOME ?? process.env.USERPROFILE ?? '',
  '.pi',
  POINTER_FILE,
)

/**
 * pi's default agent id (owner ruling 2026-10-16: uniform 'agent' — the
 * default main-agent name, and it may later be renamed via CLI commands, so
 * no adapter may treat the NAME as a platform identity marker). pi is a
 * single-agent host: the binding is written/read under agent_id 'agent' and
 * the derived address is agent@domain (same as dsh). The CLI check
 * (cli/src/core/checks/adapters.rs pi_list_agents) identifies pi by the
 * PLATFORM POINTER (the ~/.pi pointer file), never by the name.
 */
export const DEFAULT_AGENT_ID = 'agent'


/**
 * pi's ANCHOR (contract §7.1) — the whole agent: `~/.pi/agent` holds SOUL, skills, sessions and
 * config, so it identifies "this agent" and nothing narrower.
 *
 * Rules this satisfies (owner ruling 2026-11-25 — pi is single-address with no workspace, but the
 * agent itself is the anchor):
 *   * STABLE: no cwd, no session id, no session file, no timestamp — a new session in another
 *     directory must not change it, otherwise the gateway would refuse every pull after a restart;
 *   * CANONICAL: `~` is realpath'd first, so `~/.pi/agent` and `/home/u/.pi/agent` hash the same;
 *   * EXISTENCE-INDEPENDENT: computed from the home dir, so activation (possibly before the agent
 *     dir is created) and every later pull agree by construction.
 *
 * It is also what lands in the binding's `workspace_path`, which is what `anchorHeader()` hashes —
 * one value, one source.
 */
export function piAnchor(): string {
  let home = ''
  try {
    home = fs.realpathSync(os.homedir())
  } catch {
    home = os.homedir() // no home dir to canonicalise: fall back to the raw value
  }
  return path.join(home, '.pi', 'agent')
}
export interface SystemPointer {
  system_id?: string
  email?: string
}

/** Outbound X-AIMail-Agent identity (cached on success). */
let _identity = ''
export function agentIdentity(): string {
  if (_identity) return _identity
  try {
    let dir = path.dirname(new URL(import.meta.url).pathname)
    for (let i = 0; i < 8; i++) {
      for (const pkg of ['@earendil-works/pi-coding-agent', 'pi']) {
        const p = path.join(dir, 'node_modules', pkg, 'package.json')
        if (fs.existsSync(p)) {
          const v = String(JSON.parse(fs.readFileSync(p, 'utf-8')).version ?? '')
          if (v) {
            _identity = `pi/${v}`
            return _identity
          }
        }
      }
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  } catch {
    /* fallthrough */
  }
  return 'pi/unknown'
}

/** Install the outbound identity once at extension load. */
export function initIdentity(): void {
  setAgentIdentity(agentIdentity())
}

/** Read the ~/.pi pointer. Missing/corrupt → {}. */
export function readPointer(): SystemPointer {
  try {
    const raw = fs.readFileSync(POINTER_PATH, 'utf-8')
    const parsed = JSON.parse(raw) as SystemPointer
    if (parsed && typeof parsed === 'object') return parsed
  } catch {
    /* missing or unreadable → empty pointer */
  }
  return {}
}

/** Write the ~/.pi pointer (mkdir -p, JSON). */
export function writePointer(ptr: SystemPointer): void {
  fs.mkdirSync(path.dirname(POINTER_PATH), { recursive: true })
  fs.writeFileSync(
    POINTER_PATH,
    JSON.stringify(
      { system_id: ptr.system_id ?? '', email: ptr.email ?? '' },
      null,
      2,
    ) + '\n',
    { mode: 0o600 },
  )
}

// No inbound endpoint: pi has no listener any more (retired 2026-11-25). Mail arrives by pulling
// the gateway stream, so a binding carries no webhook_url and no port to report.

// No auto-bind: a binding is created by an ACTIVATION CODE (aimail_address bind), never by the
// plugin starting up (owner 2026-11-25). The former once-guard + `tryAutoBindOnce` wrapper were
// deleted with the eager bind they served.

/**
 * Resolve the AIMail config for the running pi agent.
 * Order: binding file (agent_id → pointer email, both system-scoped) →
 * auto-bind once when the machine has a system config. The binding is the
 * authority (dsh parity); the pointer is only a system scope / rebuildable cache.
 * Throws loud when still unbound.
 */
export async function resolveConfig(): Promise<AgentConfig> {
  const ptr = readPointer()
  const systemId = ptr.system_id ?? ''
  // dsh style: **per-address binding file is the sole authority**; pointer is only for system scope and rebuildable cache.
  // Binding identity is the ADDRESS (+ workspace), never the host agent id.
  if (ptr.email && systemId) {
    const byEmail = await loadConfigByEmail(ptr.email, systemId)
    if (byEmail) return byEmail
  }
  throw new Error(
    hasAnySystem()
      ? `aimail not configured for pi — no binding for pointer ${POINTER_PATH} (email ${ptr.email || '-'}, system ${systemId || '-'}). ` +
        'Ask your gateway operator for an activation code and call the aimail_address tool with ' +
        '{"action":"bind","code":"<code>"}; nothing is bound automatically.'
      : 'This machine has no AIMail system yet. An address is created by consuming an activation code ' +
        '(aimail_address tool, action "bind"); there is no installer and no automatic binding.',
  )
}
