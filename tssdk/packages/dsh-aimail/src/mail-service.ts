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
  AIMAIL_HOME,
  autoBind,
  emailForAgent,
  hasAnySystem,
  listSystemDirs,
  readSystemConfig,
  releaseAllSystems,
  type AgentConfig,
} from '@aimail/mail-core'
import * as fs from 'node:fs'
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

/** Local inbound path the dsh-aimail/inbound entry listens on (default). */
const INBOUND_PATH = '/aimail/inbound'

/** The local receive endpoint registered as this session's webhook_url. */
function inboundWebhookUrl(): string {
  const fromEnv = (process.env.AIMAIL_INBOUND_URL ?? '').trim()
  if (fromEnv) return fromEnv.replace(/\/+$/, '') + INBOUND_PATH
  const port = Number(process.env.AIMAIL_INBOUND_PORT ?? 9099)
  const p = Number.isInteger(port) && port > 0 ? port : 9099
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

export function apply(ctx: Context, config: { systemId?: string } = {}): void {
  const systemId = config.systemId ?? process.env.AIMAIL_SYSTEM_ID ?? ''
  // SDK-shipped board resources (role prompts/souls) → local config dir,
  // so a dsh-only machine (no Python SDK/CLI) still gets them. Idempotent;
  // never overwrites user-personalized files.
  try {
    releaseAllSystems(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'board'))
  } catch {
    // non-fatal: resources are a seed; explicit release can re-run later
  }
  // env self-check: without any binding the mail tools resolve nothing —
  // point the operator at the CLI instead of failing silently later.
  try {
    const sysRoot = path.join(AIMAIL_HOME(), 'systems')
    if (!fs.existsSync(sysRoot) || fs.readdirSync(sysRoot).length === 0) {
      const fix = hasAnySystem()
        ? 'run `aimail install`(dsh) first, then bind this session'
        : 'run `aimail init`, then `aimail install`(dsh) to build the environment first'
      console.warn('[dsh-aimail] no aimail binding found — ' + fix)
    } else if (!systemId) {
      console.warn('[dsh-aimail] no AIMAIL_SYSTEM_ID — mail resolution scans all bound systems')
    }
  } catch {
    // non-fatal
  }

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
