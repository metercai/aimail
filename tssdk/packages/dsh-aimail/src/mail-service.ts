/**
 * dsh-aimail mail service — provides ctx.mail to this bundle's tools/inbound
 * entries. Thin dsh binding over the platform-neutral @aimail/mail resolvers
 * (sessionId/email/recipient → agentmail.json → AgentConfig).
 *
 * Identity = agentmail.json only; the AIMAIL_SYSTEM_ID env narrows scope.
 *
 * Auto-bind (SDK auto-binding): a session resolution that finds no binding
 * triggers one auto-bind attempt per (system, session) — register chain +
 * agentmail.json + bridge route via mail-core autoBind, deriving the address
 * `agent-<session8>.<system_name>@domain` — and retries the resolution. The
 * once-guard means a failed attempt (gateway unreachable, no system config)
 * never hammers the network on every tool call; the original unbound error
 * is rethrown for the caller to handle.
 */
import type { Context } from '@deepseek-ai/cordis'
import {
  resolveByRecipient,
  resolveByEmail,
  resolveBySessionId,
  type MailToolCtx,
} from '@aimail/mail'
import {
  autoBind,
  emailForAgent,
  ensureSystem,
  detectSystemForHome,
  hasAnySystem,
  INBOUND_PATH,
  INBOUND_PORTS,
  listSystemDirs,
  readSystemConfig,
  releaseAllSystems,
  type AgentConfig,
} from '@aimail/mail-core'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'

export const name = 'mail'
export const inject = []

/** The ctx.mail service surface (consumed by the tools + inbound entries). */
export interface MailService {
  /** Optional explicit system scope (AIMAIL_SYSTEM_ID); empty = scan all. */
  readonly systemId: string
  /** Resolve config for a dsh session id (uuid). Throws when unbound. */
  resolveConfig(sessionId: string): Promise<AgentConfig>
  /** Resolve a tool context for a session id. Throws when unbound. */
  resolveCtx(sessionId: string): Promise<MailToolCtx>
  /** Resolve config by the agent's registered email. Throws when unbound. */
  resolveByEmail(email: string): Promise<AgentConfig>
  /** Inbound recipient routing: exact match → persona-strip fallback. */
  resolveByRecipient(email: string): Promise<AgentConfig | undefined>
}

/** The local receive endpoint registered as this session's webhook_url.
 * 路径/端口契约常量唯一副本 = @aimail/mail-core(contract/aimail-contract.json)。 */
function inboundWebhookUrl(): string {
  const fromEnv = (process.env.AIMAIL_INBOUND_URL ?? '').trim()
  if (fromEnv) return fromEnv.replace(/\/+$/, '') + INBOUND_PATH
  const port = Number(process.env.AIMAIL_INBOUND_PORT ?? INBOUND_PORTS.dsh)
  const p = Number.isInteger(port) && port > 0 ? port : INBOUND_PORTS.dsh
  return `http://127.0.0.1:${p}${INBOUND_PATH}`
}

/** Process once-guard per (system, session): at most one auto-bind attempt. */
const _autoBindAttempted = new Set<string>()

/** Reset the once-guard (test hook / after an operator fixed the env). */
export function resetAutoBindOnce(): void {
  _autoBindAttempted.clear()
}

/**
 * One-shot per-session auto-bind. Never throws — failures warn and fall
 * through to the caller's original unbound error.
 */
async function tryAutoBindSession(
  systemId: string,
  sessionId: string,
): Promise<AgentConfig | undefined> {
  const key = `${systemId}:${sessionId}`
  if (_autoBindAttempted.has(key)) return undefined
  _autoBindAttempted.add(key)
  try {
    const gw = await readSystemConfig(systemId)
    if (!gw.domain) return undefined
    const short = sessionId.replace(/[^a-zA-Z0-9]/g, '').slice(0, 8) || 'session'
    const email = emailForAgent(`agent-${short}`, gw.domain, gw.system_name ?? '')
    const res = await autoBind({
      systemId,
      email,
      webhookUrl: inboundWebhookUrl(),
      extraFields: {
        session_id: sessionId,
        preset: process.env.AIMAIL_PRESET ?? 'mail',
      },
    })
    if (!(res.registered || res.exists)) return undefined
    // Re-resolve: the binding now carries this session_id.
    return await resolveBySessionId(sessionId, { systemId })
  } catch (e) {
    console.warn(
      `[dsh-aimail] session auto-bind failed for ${sessionId}: ${e instanceof Error ? e.message : String(e)}`,
    )
    return undefined
  }
}

/** Self-determined platform/agent home root (the single source the plugin uses). */
function platformHome(): string {
  return (
    process.env.AIMAIL_SYSTEM_HOME?.trim() ||
    process.env.DSH_HOME?.trim() ||
    path.join(os.homedir(), '.dsh')
  )
}

/**
 * Proactively ensure the system + the **default** agent binding for this home.
 *
 * The `dsh plugin add` install path never runs the CLI's `register_default`
 * step, so nothing else writes the default agent's binding file — without
 * this, a plugin-installed host has a system but no binding and the L3
 * readiness gate (`dsh binding not found`) fails. Idempotent: the system check
 * short-circuits on an owning system and autoBind short-circuits when the
 * binding already exists, so both install methods (CLI `aimail install` and
 * `dsh plugin add`) converge on the same bound state.
 *
 * Never throws — failures warn and leave the host to its usual unbound path.
 */
export async function ensureDefaultAgentBinding(): Promise<void> {
  const home = platformHome()
  // 1) System readiness (same semantics as the pre-existing reverse-call: an
  //    owning system short-circuits; otherwise activate via the CLI's L1 ABI).
  let systemId = ''
  try {
    const owned = await detectSystemForHome(home)
    if (owned) {
      systemId = owned
    } else {
      const r = await ensureSystem({ systemHome: home })
      if (!r.ok) {
        const hint = r.hint ? `\n  hint: ${r.hint}` : ''
        console.warn(
          `[dsh-aimail] no AIMail system is bound to this dsh home yet: ${r.error ?? 'unknown'}${hint}` +
            `\n  to activate: aimail install --home ${home} -c <activation-code> -m <manager@address>`,
        )
        return
      }
      systemId = r.systemId ?? ''
      if (r.activated) console.log(`[dsh-aimail] system activated: ${systemId}`)
    }
  } catch (e) {
    console.warn(
      `[dsh-aimail] AIMail system check failed: ${e instanceof Error ? e.message : String(e)}`,
    )
    return
  }
  if (!systemId) return

  // 2) Default agent binding (idempotent autoBind). The email mirrors the CLI
  //    register step's default agent (`agent@domain`); the webhook is the local
  //    inbound endpoint; the manager mirrors the CLI's resolution (env first).
  try {
    const gw = await readSystemConfig(systemId)
    const email = emailForAgent('agent', gw.domain ?? '', gw.system_name ?? '', [])
    const manager =
      process.env.AIMAIL_MANAGER_ADDRESS?.trim() || process.env.MANAGER?.trim() || ''
    const res = await autoBind({
      systemId,
      email,
      webhookUrl: inboundWebhookUrl(),
      ...(manager ? { managerAddress: manager } : {}),
    })
    if (res.registered) console.log(`[dsh-aimail] default agent bound: ${email}`)
  } catch (e) {
    console.warn(
      `[dsh-aimail] default agent auto-bind failed: ${e instanceof Error ? e.message : String(e)}`,
    )
  }
}

export function apply(ctx: Context, config: { systemId?: string } = {}): void {
  const systemId = config.systemId ?? process.env.AIMAIL_SYSTEM_ID ?? ''
  // SDK-shipped board resources (role prompts/souls) → local config dir,
  // so a dsh-only machine (no Python SDK/CLI) still gets them. Idempotent;
  // never overwrites user-personalized files.
  try {
    releaseAllSystems(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'board'))
  } catch (e) {
    // non-fatal (resources are seeds, can be re-released explicitly), but never silent
    console.error(`[dsh-aimail] board resource release failed: ${String(e)}`)
  }
  // install readiness (system activation + default agent binding) lives in the
  // inbound entry (`ensureDefaultAgentBinding`): the same atomic bundle mounts
  // mail-inbound whenever this service does, and gating the inbound listener on
  // the bind makes "port up" a reliable readiness signal (P2, 2026-10-15).

  /**
   * Resolve a session config; on an unbound miss with a machine system
   * config present, auto-bind that session once and retry.
   */
  const resolveOrAutoBindSession = async (sessionId: string): Promise<AgentConfig> => {
    if (!sessionId) throw new Error('no session id to resolve aimail config')
    try {
      return await resolveBySessionId(sessionId, { systemId })
    } catch (e) {
      if (!hasAnySystem()) throw e
      let target = systemId || process.env.AIMAIL_SYSTEM_ID || ''
      if (!target) {
        const sids = await listSystemDirs()
        if (sids.length !== 1) throw e // ambiguous scope — caller's error stands
        target = sids[0] as string
      }
      const cfg = await tryAutoBindSession(target, sessionId)
      if (cfg) return cfg
      throw e
    }
  }

  const service: MailService = {
    systemId,
    resolveConfig: (sessionId) => resolveOrAutoBindSession(sessionId),
    resolveCtx: async (sessionId) => {
      const cfg = await resolveOrAutoBindSession(sessionId)
      return { systemId: cfg.system_id, email: cfg.email }
    },
    resolveByEmail: (email) => resolveByEmail(email),
    resolveByRecipient: (email) => resolveByRecipient(email),
  }
  ctx.provide('mail', service)
}
