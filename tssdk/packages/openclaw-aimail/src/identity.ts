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
 * has a system config (~/.aimail/systems/{sid}/aimail_gateway.json), the
 * first resolution auto-registers the agent's address once per process
 * (register chain + agentmail.json + bridge route via mail-core autoBind),
 * writes the pointer and returns the config — instead of failing loud.
 * Unbound agents on pointer-less machines still fail loud with guidance.
 */
import * as fs from 'node:fs'
import { randomBytes } from 'node:crypto'
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

/**
 * Ensure the host config carries `hooks.token` — the credential openclaw's internal
 * /hooks/agent endpoint requires, and the only prerequisite of THIS adapter's own wake
 * path (inbound.ts::readHooksToken; without it inbound mail never reaches the agent and
 * nothing says so).
 *
 * Scope (owner ruling 2026-09-27): the SDK only manages config it can derive by itself.
 * Generating a token qualifies. Choosing an LLM provider/model/key or binding an agent
 * needs operator input and stays the administrator's job. A missing or unparsable
 * openclaw.json is left untouched — writing one from scratch would look "clobbered" to
 * openclaw itself (it refuses to start without gateway.mode).
 */
export function ensureHooksWiring(): 'kept' | 'created' | 'no-config' | 'disabled' {
  const p = path.join(openclawHome(), '.openclaw', 'openclaw.json')
  let cfg: Record<string, unknown>
  try {
    cfg = JSON.parse(fs.readFileSync(p, 'utf-8')) as Record<string, unknown>
  } catch {
    return 'no-config'
  }
  const hooks = (cfg.hooks ?? {}) as Record<string, unknown>
  if (hooks.enabled === false) return 'disabled' // 管理员显式关掉: 不越权改, 但要报出来
  let changed = false
  if (!(typeof hooks.token === 'string' && hooks.token.trim())) {
    hooks.token = randomBytes(24).toString('hex')
    changed = true
  }
  // 2026-09-27: token 只是认证; 端点本身要 hooks.enabled=true 才存在
  // (宿主文档 /gateway/config-hooks: "404 ... Disabled hooks fall through").
  if (hooks.enabled !== true) {
    hooks.enabled = true
    changed = true
  }
  if (!changed) return 'kept'
  cfg.hooks = hooks
  fs.writeFileSync(p, JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
  return 'created'
}

/** hooks 前缀(宿主配置 hooks.path, 默认 /hooks): 派发 URL 必须用它, 不能写死。 */
export function hooksPath(): string {
  try {
    const p = path.join(openclawHome(), '.openclaw', 'openclaw.json')
    const cfg = JSON.parse(fs.readFileSync(p, 'utf-8')) as { hooks?: { path?: unknown } }
    const raw = String(cfg?.hooks?.path ?? '/hooks').trim() || '/hooks'
    const withSlash = raw.startsWith('/') ? raw : '/' + raw
    return withSlash.replace(/\/+$/, '')
  } catch {
    return '/hooks'
  }
}

/** 宿主已配置的 agent id（S1b, 2026-09-27）: 派发不带给合同时宿主直接 400 ——
 * 宿主文档 /gateway/config-hooks: "agentId ... Must name a configured agent when supplied
 * directly. Required when no implicit/retained owner can be resolved"，且
 * "Direct request agent ids must exist."。键位以宿主文档为准: agents.entries.<id>,
 * agents.defaults.sessionStore.agentId / agents.defaults.systemAgent.agentId。
 * 解析不到就返回空串 —— 由调用方明确报错, 不猜一个 id。 */
export function resolveAgentId(): string {
  try {
    const p = path.join(openclawHome(), '.openclaw', 'openclaw.json')
    const cfg = JSON.parse(fs.readFileSync(p, 'utf-8')) as {
      agents?: {
        entries?: Record<string, unknown>
        defaults?: { sessionStore?: { agentId?: unknown }; systemAgent?: { agentId?: unknown } }
      }
    }
    const a = cfg.agents ?? {}
    const explicit = a.defaults?.sessionStore?.agentId ?? a.defaults?.systemAgent?.agentId
    if (typeof explicit === 'string' && explicit.trim()) return explicit.trim()
    const ids = Object.keys(a.entries ?? {})
    if (ids.includes('main')) return 'main'
    return ids.length ? ids[0] : ''
  } catch {
    return ''
  }
}

/**
 * Host-visible agent roster — the ids the host itself accepts as a directly
 * supplied `agentId` ("Direct request agent ids must exist", host docs
 * /gateway/config-hooks). Source: openclaw.json `agents.entries` keys (the
 * multi-agent roster) plus the ids declared under `agents.defaults.*`
 * (sessionStore/systemAgent — the same two keys resolveAgentId reads).
 * Read-only, never guessed; empty when the host config is absent/unreadable.
 */
export function configuredAgentIds(): string[] {
  try {
    const p = path.join(openclawHome(), '.openclaw', 'openclaw.json')
    const cfg = JSON.parse(fs.readFileSync(p, 'utf-8')) as {
      agents?: {
        entries?: Record<string, unknown>
        defaults?: { sessionStore?: { agentId?: unknown }; systemAgent?: { agentId?: unknown } }
      }
    }
    const a = cfg.agents ?? {}
    const ids = Object.keys(a.entries ?? {})
    for (const declared of [a.defaults?.sessionStore?.agentId, a.defaults?.systemAgent?.agentId]) {
      if (typeof declared === 'string' && declared.trim() && !ids.includes(declared.trim())) {
        ids.push(declared.trim())
      }
    }
    return ids
  } catch {
    return []
  }
}

/** OpenClaw gateway HTTP port (openclaw.json gateway.port, default 18789). */
export function gatewayPort(): number {
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
export function openclawWebhookUrl(): string {
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
      ? 'Run: aimail install --home ~/.openclaw (或 openclaw aimail register)'
      : 'Machine has no aimail environment yet. Run: aimail install --home ~/.openclaw (or restart the host so auto-ensure via `aimail install --system-only` kicks in)'
    throw new Error(
      `aimail not configured for this agent — no ~/.openclaw/.agentmail pointer. ${fix}`,
    )
  }
  const cfg = await loadConfigByAgentId(systemId, id)
  if (!cfg) {
    throw new Error(
      `aimail not configured for agent '${id}' (system ${systemId}). Run: openclaw aimail register`,
    )
  }
  return cfg
}
