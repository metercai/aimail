/**
 * @aimail/mail — platform-neutral AgentMail config resolution.
 *
 * Pure functions: sessionId / email / agentId → agentmail.json → AgentConfig.
 * No framework imports (no cordis, no dsh-sdk); each platform adapter
 * (dsh-aimail, openclaw-aimail) binds these to its own identity source.
 *
 * Identity = agentmail.json only (single source of truth); the optional
 * AIMAIL_SYSTEM_ID env narrows the scan scope. Unbound resolutions throw.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import {
  cleanAddr,
  loadConfigByAgentId,
  loadConfigByEmail,
  loadConfigBySessionId,
  systemDir,
  type AgentConfig,
} from '@aimail/mail-core'

/** Tool context handed to mail-core tool functions. */
export interface MailToolCtx {
  systemId: string
  email?: string
}

/** Resolution options shared by every resolver. */
export interface ResolveOptions {
  /** Explicit system scope; defaults to the AIMAIL_SYSTEM_ID env (empty = scan all). */
  systemId?: string
}

function systemIdFrom(opts: ResolveOptions): string {
  return opts.systemId ?? process.env.AIMAIL_SYSTEM_ID ?? ''
}

function unbound(what: string): Error {
  return new Error(`no agentmail binding for ${what} — run bind_agent.py first`)
}

/** Scan one system dir (or all systems) for every bound AgentConfig. */
async function scanAllConfigs(systemId: string): Promise<AgentConfig[]> {
  const root = systemId ? systemDir(systemId) : systemDir('')
  let names: string[]
  try {
    const entries = await fs.readdir(root, { withFileTypes: true })
    names = entries.filter(e => e.isDirectory()).map(e => e.name)
  } catch {
    return []
  }
  const out: AgentConfig[] = []
  for (const name of names) {
    const dir = systemId ? root : path.join(root, name)
    let agentDirs
    try {
      agentDirs = await fs.readdir(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const ent of agentDirs) {
      if (!ent.isDirectory()) continue
      const p = path.join(dir, ent.name, 'agentmail.json')
      try {
        out.push(JSON.parse(await fs.readFile(p, 'utf-8')) as AgentConfig)
      } catch {
        /* skip unreadable */
      }
    }
  }
  return out
}

/**
 * Resolve config for a platform session/agent id (dsh session uuid, or an
 * openclaw agentId). Session-file match first, then agent_id field match.
 * Throws when unbound.
 */
export async function resolveBySessionId(
  sessionId: string,
  opts: ResolveOptions = {},
): Promise<AgentConfig> {
  if (!sessionId) throw new Error('no session id to resolve agentmail config')
  const systemId = systemIdFrom(opts)
  let cfg = await loadConfigBySessionId(systemId, sessionId)
  if (!cfg && systemId) cfg = await loadConfigByAgentId(systemId, sessionId)
  if (!cfg) throw unbound(`session ${sessionId}`)
  return cfg
}

/** Resolve config by the agent's registered email address. Throws when unbound. */
export async function resolveByEmail(
  email: string,
  opts: ResolveOptions = {},
): Promise<AgentConfig> {
  const systemId = systemIdFrom(opts)
  const cfg = await loadConfigByEmail(email, systemId)
  if (!cfg) throw unbound(email)
  return cfg
}

/**
 * Inbound recipient routing (mirrors Python route_agent_for_email):
 *   1. exact registered-address match
 *   2. persona-prefix fallback: `persona.profile@…` → `profile@…`
 *      (single-in-multi-out platforms; PERSONA_SUPPORTED=false semantics)
 * Returns the bound AgentConfig, or undefined when the recipient matches
 * no agent (caller decides — typically `no_agent` intercept).
 */
export async function resolveByRecipient(
  email: string,
  opts: ResolveOptions = {},
): Promise<AgentConfig | undefined> {
  const addr = cleanAddr(email)
  if (!addr) return undefined
  // local part from the ORIGINAL address (cleanAddr maps '@' → '_')
  const local = email.includes('@') ? email.split('@')[0] ?? '' : ''
  const systemId = systemIdFrom(opts)
  const cfgs = await scanAllConfigs(systemId)
  if (!cfgs.length) return undefined

  const exact = cfgs.find(c => cleanAddr(c.email ?? '') === addr)
  if (exact) return exact

  // Persona strip: recipient local part ends with ".<registered local part>".
  if (!local) return undefined
  for (const cfg of cfgs) {
    const baseLocal = (cfg.email ?? '').includes('@')
      ? (cfg.email as string).split('@')[0] ?? ''
      : ''
    if (baseLocal && local.endsWith(`.${baseLocal}`)) return cfg
  }
  return undefined
}

/** Tool context for a session id: {systemId, email}. Throws when unbound. */
export async function resolveCtx(
  sessionId: string,
  opts: ResolveOptions = {},
): Promise<MailToolCtx> {
  const cfg = await resolveBySessionId(sessionId, opts)
  return { systemId: cfg.system_id, email: cfg.email }
}
