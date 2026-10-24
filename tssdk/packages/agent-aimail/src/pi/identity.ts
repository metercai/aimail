/**
 * pi-aimail identity — resolves the AIMail binding for a pi agent.
 *
 * Identity chain (single source of truth, mirrors dsh-aimail / openclaw-aimail):
 *   ~/.pi/POINTER_FILE pointer ({system_id, email})
 *   → system_id scoped → @aimail/mail-core loadConfigByAgentId/loadConfigByEmail
 *
 * Auto-bind: system readiness + default-agent binding live ONCE in
 * mail-core `ensureAgentBinding` (single source; dsh/pi/openclaw parity,
 * batch 2026-10-16). The host start entry calls it BEFORE the inbound
 * listener is gated up ("port up" = "binding written"); the lazy path here
 * is the same call for the tool-resolution flow. The pointer is package-
 * level: written here, never in mail-core.
 *
 * Outbound X-AIMail-Agent: walk up from this module to the installed pi
 * package.json (@earendil-works/pi-coding-agent); detect, never guess.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import {
  ensureAgentBinding,
  hasAnySystem,
  INBOUND_PATH,
  INBOUND_PORTS,
  POINTER_FILE,
  loadConfigByAgentId,
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

/**
 * pi inbound receive endpoint (the bridge push target). The listener is
 * owned by index.ts (default 127.0.0.1:9101, path /aimail/inbound); the
 * extension sets the actual URL via setInboundEndpoint at startup.
 * AIMAIL_INBOUND_URL env overrides (python parity).
 */
let _inboundEndpoint = ''

/** Record the listener URL the extension actually started on. */
export function setInboundEndpoint(url: string): void {
  _inboundEndpoint = url.replace(/\/+$/, '')
}

export function inboundWebhookUrl(): string {
  const fromEnv = (process.env.AIMAIL_INBOUND_URL ?? '').trim()
  if (fromEnv) return fromEnv.replace(/\/+$/, '') + INBOUND_PATH
  if (_inboundEndpoint) return _inboundEndpoint
  return `http://127.0.0.1:${INBOUND_PORTS.pi}${INBOUND_PATH}`
}

/** Process once-guard: auto-bind at most once per run. */
let _autoBindAttempted = false

/**
 * Reset the once-guard (test hook / after an operator fixed the environment
 * in the same process).
 */
export function resetAutoBindOnce(): void {
  _autoBindAttempted = false
}

/**
 * One-shot auto-bind for the default pi agent — thin wrapper over the
 * mail-core single source (system readiness + idempotent binding + pointer
 * write). Never throws — failures warn and fall through to the caller's
 * own error.
 */
export async function tryAutoBindOnce(): Promise<AgentConfig | undefined> {
  if (_autoBindAttempted) return undefined
  _autoBindAttempted = true
  const r = await ensureAgentBinding({
    home: path.dirname(POINTER_PATH),
    agentId: DEFAULT_AGENT_ID,
    webhookUrl: inboundWebhookUrl(),
    log: (l) => console.warn(`[agent-aimail] ${l}`),
  })
  if (!r.ok || !r.email || !r.systemId) {
    if (r.error) {
      console.warn(`[agent-aimail] auto-bind failed: ${r.error}`)
    }
    return undefined
  }
  writePointer({ system_id: r.systemId, email: r.email })
  return r.config ?? (await loadConfigByEmail(r.email, r.systemId).catch(() => undefined))
}

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
  if (systemId) {
    const byAgent = await loadConfigByAgentId(systemId, DEFAULT_AGENT_ID)
    if (byAgent) return byAgent
  }
  if (ptr.email && systemId) {
    const byEmail = await loadConfigByEmail(ptr.email, systemId)
    if (byEmail) return byEmail
  }
  if (hasAnySystem()) {
    const cfg = await tryAutoBindOnce()
    if (cfg) return cfg
  }
  throw new Error(
    (hasAnySystem()
      ? `aimail not configured for pi — no binding for pointer ${POINTER_PATH} (email ${ptr.email || '-'}, system ${systemId || '-'}). Run: aimail install --home ~/.pi`
      : 'Machine has no aimail environment yet. Run: aimail install --home ~/.pi (or restart the host so auto-ensure via `aimail install --system-only` kicks in)'),
  )
}
