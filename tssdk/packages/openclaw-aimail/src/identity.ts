/**
 * openclaw-aimail identity — resolves the AIMail binding for an OpenClaw
 * agent id.
 *
 * Identity chain (mirrors dsh's exec.agent.id, Python detect_system_id):
 *   factory ctx.agentId
 *   → ~/.openclaw/.agentmail pointer (sole identity source; no env override,
 *     no cross-system scan — established convention)
 *   → system_id → @aimail/mail-core loadConfigByAgentId → AgentConfig
 *
 * Auto-bind (SDK auto-binding): when the pointer is missing but the machine
 * has a system config (~/.agentmail/systems/{sid}/agentmail_gateway.json), the
 * first resolution auto-registers the agent's address once per process
 * (register chain + agentmail.json + bridge route via mail-core autoBind),
 * writes the pointer and returns the config — instead of failing loud.
 * Unbound agents on pointer-less machines still fail loud with guidance.
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
  readSystemConfig,
  type AgentConfig,
} from '@aimail/mail-core'
import { INBOUND_PATH } from './inbound.js'

export interface SystemPointer {
  system_id?: string
  email?: string
}

function openclawHome(): string {
  return process.env.HOME ?? process.env.USERPROFILE ?? ''
}

/** ~/.openclaw/.agentmail (resolved per call — HOME may move in tests). */
export function pointerPath(): string {
  return path.join(openclawHome(), '.openclaw', '.agentmail')
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
    const raw = fs.readFileSync(pointerPath(), 'utf-8')
    const parsed = JSON.parse(raw) as SystemPointer
    if (parsed && typeof parsed === 'object') return parsed
  } catch {
    /* missing or unreadable → empty pointer */
  }
  return {}
}

/** Write the ~/.openclaw/.agentmail pointer (mkdir -p, JSON). */
export async function writePointer(ptr: SystemPointer): Promise<void> {
  const p = pointerPath()
  await fs.promises.mkdir(path.dirname(p), { recursive: true })
  await fs.promises.writeFile(
    p,
    JSON.stringify(
      { system_id: ptr.system_id ?? '', email: ptr.email ?? '' },
      null,
      2,
    ) + '\n',
    { mode: 0o600 },
  )
}

/** OpenClaw gateway HTTP port (openclaw.json gateway.port, default 18789). */
function gatewayPort(): number {
  try {
    const raw = fs.readFileSync(
      path.join(openclawHome(), '.openclaw', 'openclaw.json'),
      'utf-8',
    )
    const oc = JSON.parse(raw) as { gateway?: { port?: unknown } }
    const port = Number((oc.gateway as { port?: unknown } | undefined)?.port)
    if (Number.isInteger(port) && port > 0) return port
  } catch {
    /* not installed/readable → default */
  }
  return 18789
}

/**
 * Local receive endpoint: the plugin's in-gateway HTTP route
 * (registerHttpRoute in index.ts) lives on the OpenClaw gateway HTTP server.
 */
function openclawWebhookUrl(): string {
  return `http://127.0.0.1:${gatewayPort()}${INBOUND_PATH}`
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
 * One-shot auto-bind for an openclaw agent id. Adopts an existing binding
 * for the id when present (pointer lost but binding alive); otherwise
 * derives the address (python email_for_agent: main → agent), runs the
 * register chain + binding + bridge route, writes the pointer.
 * Never throws — failures warn and fall through to the caller's own error.
 */
async function tryAutoBindOnce(agentId: string): Promise<AgentConfig | undefined> {
  if (_autoBindAttempted) return undefined
  _autoBindAttempted = true
  try {
    const sids = await listSystemDirs()
    if (sids.length !== 1) return undefined
    const systemId = sids[0] as string
    const gw = await readSystemConfig(systemId)
    if (!gw.domain) return undefined

    // Binding already exists for this agent id (different/earlier address)?
    // Adopt it and repair the pointer — no network involved.
    const existing = await loadConfigByAgentId(systemId, agentId)
    if (existing && existing.api_key) {
      await writePointer({ system_id: systemId, email: existing.email })
      console.warn(
        `[openclaw-aimail] auto-bind: adopted existing binding ${existing.email} (system ${systemId})`,
      )
      return existing
    }

    const email = emailForAgent(agentId, gw.domain, gw.system_name ?? '', ['main'])
    const webhookUrl = openclawWebhookUrl()
    const res = await autoBind({
      systemId,
      email,
      webhookUrl,
      extraFields: { agent_id: agentId },
    })
    if (res.registered || res.exists) {
      await writePointer({ system_id: systemId, email })
      const cfg = await loadAgentConfig(systemId, email)
      if (cfg) return cfg
    }
    return undefined
  } catch (e) {
    console.warn(
      `[openclaw-aimail] auto-bind failed: ${e instanceof Error ? e.message : String(e)}`,
    )
    return undefined
  }
}

/**
 * Resolve the AIMail config for an openclaw agent id.
 * Missing pointer + machine has a system config → auto-bind once, then
 * resolve. Otherwise throws with setup guidance.
 */
export async function resolveConfigForAgent(
  agentId: string | undefined,
): Promise<AgentConfig> {
  const id = agentId && agentId.trim() ? agentId.trim() : 'main'
  const ptr = await readPointer()
  const systemId = ptr.system_id ?? ''
  if (!systemId) {
    if (hasAnySystem()) {
      const cfg = await tryAutoBindOnce(id)
      if (cfg) return cfg
    }
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
