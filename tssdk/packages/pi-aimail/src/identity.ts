/**
 * pi-aimail identity — resolves the AIMail binding for a pi agent.
 *
 * Identity chain (single source of truth, mirrors openclaw-aimail):
 *   ~/.pi/.agentmail pointer ({system_id, email})
 *   → system_id scoped → @aimail/mail-core loadConfigByAgentId/loadConfigByEmail
 *
 * Auto-bind (SDK auto-binding): when the pointer is missing (or empty) but
 * the machine has a system config, the first resolution auto-registers the
 * pi address once per process (register chain + agentmail.json + bridge
 * route via mail-core autoBind), writes the pointer and returns the config.
 * Failures fall back to the loud unbound error with guidance.
 *
 * Outbound X-AIMail-Agent: walk up from this module to the installed pi
 * package.json (@earendil-works/pi-coding-agent); detect, never guess.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import {
  autoBind,
  emailForAgent,
  hasAnySystem,
  listSystemDirs,
  loadAgentConfig,
  loadConfigByAgentId,
  loadConfigByEmail,
  readSystemConfig,
  setAgentIdentity,
  type AgentConfig,
} from '@aimail/mail-core'

const POINTER_PATH = path.join(
  process.env.HOME ?? process.env.USERPROFILE ?? '',
  '.pi',
  '.agentmail',
)

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

/** Read the ~/.pi/.agentmail pointer. Missing/corrupt → {}. */
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

/** Write the ~/.pi/.agentmail pointer (mkdir -p, JSON). */
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

function inboundWebhookUrl(): string {
  const fromEnv = (process.env.AIMAIL_INBOUND_URL ?? '').trim()
  if (fromEnv) return fromEnv.replace(/\/+$/, '') + '/aimail/inbound'
  if (_inboundEndpoint) return _inboundEndpoint
  return 'http://127.0.0.1:9101/aimail/inbound'
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
 * One-shot auto-bind for the pi agent. Derives the pi address (python
 * email_for_agent: 'pi' keeps its name), adopts an existing binding when
 * one is present, otherwise runs the register chain + binding + bridge
 * route and writes the pointer. Never throws — failures warn and fall
 * through to the caller's own error.
 */
async function tryAutoBindOnce(): Promise<AgentConfig | undefined> {
  if (_autoBindAttempted) return undefined
  _autoBindAttempted = true
  try {
    const ptr = readPointer()
    const scope = ptr.system_id ?? ''
    const sids = scope ? [scope] : await listSystemDirs()
    if (sids.length !== 1) return undefined
    const systemId = sids[0] as string
    const gw = await readSystemConfig(systemId)
    if (!gw.domain) return undefined

    const email = emailForAgent('pi', gw.domain, gw.system_name ?? '')
    // Binding already exists (any earlier registration)? Adopt + repair pointer.
    const existing =
      (await loadConfigByEmail(email, systemId)) ??
      (await loadConfigByAgentId(systemId, 'main'))
    if (existing && existing.api_key) {
      writePointer({ system_id: systemId, email: existing.email })
      console.warn(
        `[pi-aimail] auto-bind: adopted existing binding ${existing.email} (system ${systemId})`,
      )
      return existing
    }

    const webhookUrl = inboundWebhookUrl()
    const res = await autoBind({
      systemId,
      email,
      webhookUrl,
      extraFields: { agent_id: 'main' },
    })
    if (res.registered || res.exists) {
      writePointer({ system_id: systemId, email })
      const cfg = await loadAgentConfig(systemId, email)
      if (cfg) return cfg
    }
    return undefined
  } catch (e) {
    console.warn(
      `[pi-aimail] auto-bind failed: ${e instanceof Error ? e.message : String(e)}`,
    )
    return undefined
  }
}

/**
 * Resolve the AIMail config for the running pi agent.
 * Order: pointer email (system-scoped) → agent_id 'main' within the
 * pointer's system → auto-bind once when the machine has a system config.
 * Throws loud when still unbound.
 */
export async function resolveConfig(): Promise<AgentConfig> {
  const ptr = readPointer()
  const systemId = ptr.system_id ?? ''
  if (ptr.email) {
    const byEmail = await loadConfigByEmail(ptr.email, systemId)
    if (byEmail) return byEmail
  }
  if (systemId) {
    const byAgent = await loadConfigByAgentId(systemId, 'main')
    if (byAgent) return byAgent
  }
  if (hasAnySystem()) {
    const cfg = await tryAutoBindOnce()
    if (cfg) return cfg
  }
  throw new Error(
    (hasAnySystem()
      ? `aimail not configured for pi — no binding for pointer ${POINTER_PATH} (email ${ptr.email || '-'}, system ${systemId || '-'}). Run: aimail install --home ~/.pi`
      : 'Machine has no aimail environment yet. Run: aimail init, then aimail install --home ~/.pi'),
  )
}
