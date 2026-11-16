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
import { activateHost } from '../hosts/activate.js'
import { writeAgentmailPointer } from '../core/provision.js'
import { defaultWorkspaceOrCreate } from './workspace.js'
import type { Context } from '@deepseek-ai/cordis'
import {
  resolveByRecipient,
  resolveByEmail,
  resolveBySessionId,
  type MailToolCtx,
} from '../mail/index.js'
import {
  autoBind,
  emailForAgent,
  ensureAgentBinding,
  hasAnySystem,
  INBOUND_PATH,
  listAgentConfigs,
  INBOUND_PORT,
  listSystemDirs,
  readSystemConfig,
  releaseAllSystems,
  updateAgentConfig,
  type AgentConfig, packageResourcesRoot} from '../core/index.js'
import * as os from 'node:os'
import * as path from 'node:path'
import { promises as fs } from 'node:fs'


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
 * Path/port contract constants single copy = @aimail/mail-core (contract/aimail-contract.json). */
function inboundWebhookUrl(): string {
  const fromEnv = (process.env.AIMAIL_INBOUND_URL ?? '').trim()
  if (fromEnv) return fromEnv.replace(/\/+$/, '') + INBOUND_PATH
  const port = Number(process.env.AIMAIL_INBOUND_PORT ?? INBOUND_PORT)
  const p = Number.isInteger(port) && port > 0 ? port : INBOUND_PORT
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
      `[agent-aimail] session auto-bind failed for ${sessionId}: ${e instanceof Error ? e.message : String(e)}`,
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
export async function ensureDefaultAgentBinding(ctx?: Context): Promise<void> {
  // Single-source default-agent binding (mail-core ensureAgentBinding):
  // system readiness (owning system short-circuit, else CLI L1 reverse-call)
  // → address derivation → idempotent autoBind (adopt-existing by agent_id,
  // register+write binding otherwise). The `dsh plugin add` path never runs
  // the CLI's register_default step, so this is what gives a plugin-installed
  // host its default agent binding — the inbound listener is gated on it so
  // "port up" = "binding written".
  const r = await ensureAgentBinding({
    home: platformHome(),
    agentId: 'agent',
    writeAgentId: false,
    // SSE pilot: no local inbound listener. Registering/persisting an
    // empty webhook is the pull-mode contract; the hub reads the same
    // binding from agentmail.json.  // contract-allowed: mail-service ensures binding+pointer at startup (F8 pull-mode fix)
    webhookUrl: '',
    log: (l) => console.log(`[agent-aimail] ${l}`),
  })
  if (!r.ok) {
    console.warn(`[agent-aimail] default agent binding failed: ${r.error ?? 'unknown'}`)
    return
  }
  if (r.systemId && r.email) {
    // Stable address ↔ workspace: CLI install/register can fail soft and
    // writes no workspace. Bind the default workspace here; inbound mail must
    // resolve the mailbox by this durable workspace, never by session_id.
    if (ctx) {
      try {
        const ws = await defaultWorkspaceOrCreate(ctx)
        if (ws) {
          await updateAgentConfig(r.systemId, r.email, {
            workspace_id: ws.id,
            workspace_path: ws.path,
            ...(ws.title ? { workspace_title: ws.title } : {}),
          })
        } else {
          console.warn('[agent-aimail] default workspace not found; inbound workspace mapping may be incomplete')
        }
      } catch (e) {
        console.warn(`[agent-aimail] workspace binding failed: ${e instanceof Error ? e.message : String(e)}`)
      }
    }
    // Writing the pointer here keeps `aimail welcome` / `aimail check`
    // resolvable even when only the plugin path ran.
    try {
      await writeAgentmailPointer(r.systemId, r.email)
    } catch (e) {
      console.warn(`[agent-aimail] pointer write failed: ${e instanceof Error ? e.message : String(e)}`)
    }
  }
}

export function apply(ctx: Context, config: { systemId?: string } = {}): void {
  const systemId = config.systemId ?? process.env.AIMAIL_SYSTEM_ID ?? ''
  // SDK-shipped board resources (role prompts/souls) → local config dir,
  // so a dsh-only machine (no Python SDK/CLI) still gets them. Idempotent;
  // never overwrites user-personalized files.
  try {
    releaseAllSystems(path.join(packageResourcesRoot(), 'board'))
  } catch (e) {
    // non-fatal (resources are seeds, can be re-released explicitly), but never silent
    console.error(`[agent-aimail] board resource release failed: ${String(e)}`)
  }
  // install readiness (system activation + default agent binding) lives in the
  // inbound entry (`ensureDefaultAgentBinding`): the same atomic bundle mounts
  // mail-inbound whenever this service does, and gating the inbound listener on
  // the bind makes "port up" a reliable readiness signal (P2, 2026-10-15).

  /**
   * Resolve a session config; on an unbound miss with a machine system
   * config present, auto-bind that session once and retry.
   */
  /**
   * Stable address ↔ workspace mapping: a new inbound session is created with
   * the binding's workspace as its cwd. Resolve that binding from the
   * workspace, NOT from a persisted session_id (session ids are disposable and
   * must never drift into the binding file).
   */
  const resolveByWorkspace = async (sessionId: string): Promise<AgentConfig | undefined> => {
    const cwd = (ctx.get('sessions') as { get(id: string): { header?: { cwd?: string } } | undefined } | undefined)
      ?.get(sessionId)?.header?.cwd
    if (!cwd) return undefined
    let target: string
    try {
      target = await fs.realpath(cwd)
    } catch {
      target = path.resolve(cwd)
    }
    const sids = systemId ? [systemId] : await listSystemDirs()
    for (const sid of sids) {
      let configs: AgentConfig[]
      try {
        configs = await listAgentConfigs(sid)
      } catch {
        continue
      }
      for (const cfg of configs) {
        if (!cfg.api_key || cfg.paused || !cfg.workspace_path) continue
        let real: string
        try {
          real = await fs.realpath(cfg.workspace_path)
        } catch {
          continue
        }
        if (real === target) return cfg
      }
    }
    return undefined
  }

  const resolveOrAutoBindSession = async (sessionId: string): Promise<AgentConfig> => {
    if (!sessionId) throw new Error('no session id to resolve aimail config')
    try {
      return await resolveBySessionId(sessionId, { systemId })
    } catch (e) {
      if (!hasAnySystem()) throw e
      // New inbound sessions carry no session_id by design. Resolve their
      // address through the stable workspace mapping before any auto-bind.
      const byWorkspace = await resolveByWorkspace(sessionId)
      if (byWorkspace) return byWorkspace
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
      const cwd = (ctx.get('sessions') as { get(id: string): { header?: { cwd?: string } } | undefined } | undefined)?.get(sessionId)?.header?.cwd
      return { systemId: cfg.system_id, email: cfg.email, sessionId, ...(cwd ? { workspacePath: cwd } : {}) }
    },
    resolveByEmail: (email) => resolveByEmail(email),
    resolveByRecipient: (email) => resolveByRecipient(email),
  }
  ctx.provide('mail', service)
  // SSE pilot: this is the replacement for the removed mail-inbound entry's
  // ensure call. The host may start before CLI registration wrote a binding;
  // bind here, then nudge the hub to open the address stream.
  void ensureDefaultAgentBinding(ctx).then(() => {
    const hub = ctx.get('mailHub') as { reconcile?: () => Promise<void> } | undefined
    if (hub?.reconcile) void hub.reconcile().catch(() => undefined)
  })
}

// Host identification (#5): dsh loads this bundle's entries; assert the
// environment once (all three dsh entries share the dsh host).
activateHost('dsh')
