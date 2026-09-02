/**
 * dsh-aimail mail service — provides ctx.mail to this bundle's tools/inbound
 * entries. Thin dsh binding over the platform-neutral @aimail/mail resolvers
 * (sessionId/email/recipient → agentmail.json → AgentConfig).
 *
 * Identity = agentmail.json only; the AIMAIL_SYSTEM_ID env narrows scope.
 */
import type { Context } from '@deepseek-ai/cordis'
import {
  resolveByRecipient,
  resolveByEmail,
  resolveBySessionId,
  resolveCtx,
  type MailToolCtx,
} from '@aimail/mail'
import { AIMAIL_HOME, releaseAllSystems, type AgentConfig } from '@aimail/mail-core'
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
      console.warn('[dsh-aimail] no agentmail binding found — run `agentmail install`(dsh) first, then bind this session')
    } else if (!systemId) {
      console.warn('[dsh-aimail] no AIMAIL_SYSTEM_ID — mail resolution scans all bound systems')
    }
  } catch {
    // non-fatal
  }
  const service: MailService = {
    systemId,
    resolveConfig: (sessionId) => resolveBySessionId(sessionId),
    resolveCtx: (sessionId) => resolveCtx(sessionId),
    resolveByEmail: (email) => resolveByEmail(email),
    resolveByRecipient: (email) => resolveByRecipient(email),
  }
  ctx.provide('mail', service)
}
