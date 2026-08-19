/**
 * @aimail/mail — host-layer AgentMail service (ctx.mail).
 *
 * Provides per-session agentmail config resolution for tool-mail / mail-inbound:
 *   session_id (exec.agent.id) → agentmail.json → ToolCtx {systemId, email}.
 * Config identity = agentmail.json only (single source of truth); the optional
 * AMAIL_SYSTEM_ID env narrows the scan scope.
 */
import type { Context } from '@deepseek-ai/cordis'
import {
  loadConfigByAgentId,
  loadConfigByEmail,
  loadConfigBySessionId,
  type AgentConfig,
} from '@aimail/mail-core'

export const name = 'mail'
export const inject = []

/** Session-level tool context handed to mail-core tool functions. */
export interface MailToolCtx {
  systemId: string
  email?: string
}

/** The ctx.mail service surface. */
export interface MailService {
  /** Optional explicit system scope (AMAIL_SYSTEM_ID); empty = scan all. */
  readonly systemId: string
  /** Resolve config for a dsh session id (uuid). Throws when unbound. */
  resolveConfig(sessionId: string): Promise<AgentConfig>
  /** Resolve config by agent email address (inbound routing). Throws when unbound. */
  resolveByEmail(email: string): Promise<AgentConfig>
  /** Resolve a tool context for a session id. Throws when unbound. */
  resolveCtx(sessionId: string): Promise<MailToolCtx>
}

/** Service identity used by consumers (ctx.get('mail')). */
export const MAIL_SERVICE = 'mail'

export function apply(ctx: Context, config: { systemId?: string } = {}): void {
  const systemId = config.systemId ?? process.env.AMAIL_SYSTEM_ID ?? ''
  const service: MailService = {
    systemId,
    async resolveConfig(sessionId: string): Promise<AgentConfig> {
      if (!sessionId) throw new Error('no session id to resolve agentmail config')
      let cfg = systemId
        ? await loadConfigBySessionId(systemId, sessionId)
        : await loadConfigBySessionId('', sessionId)
      if (!cfg && systemId) {
        cfg = await loadConfigByAgentId(systemId, sessionId)
      }
      if (!cfg) {
        throw new Error(`no agentmail binding for session ${sessionId} — run bind_agent.py first`)
      }
      return cfg
    },
    async resolveCtx(sessionId: string): Promise<MailToolCtx> {
      const cfg = await service.resolveConfig(sessionId)
      return { systemId: cfg.system_id, email: cfg.email }
    },
    async resolveByEmail(email: string): Promise<AgentConfig> {
      const cfg = systemId
        ? await loadConfigByEmail(email, systemId)
        : await loadConfigByEmail(email)
      if (!cfg) {
        throw new Error(`no agentmail binding for ${email} — run bind_agent.py first`)
      }
      return cfg
    },
  }
  ctx.provide(MAIL_SERVICE, service)
}
