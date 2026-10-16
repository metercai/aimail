/**
 * ensureAgentBinding — single-source default-agent binding for host plugins.
 *
 * WHY THIS EXISTS (owner ruling 2026-10-16, dsh-aimail P2 + parity batch):
 * the `dsh plugin add` / `pi install` / `openclaw plugins install` install
 * path NEVER runs the CLI's `register_default` step, so nothing else writes
 * the default agent's binding file — a plugin-installed host has a system
 * but no binding, and the L3 readiness gate (inbound port up ⇒ binding
 * present) fails. This is the one implementation all nodejs host adapters
 * (dsh-aimail / pi-aimail / openclaw-aimail) call at host start so that
 * "inbound port up" is a reliable readiness signal (binding + listener).
 *
 * Semantics (idempotent, never throws):
 *   1) system readiness — `detectSystemForHome` short-circuits on an owning
 *      system; otherwise `ensureSystem` reverse-calls the CLI L1 ABI
 *      (`aimail install --system-only`).
 *   2) address derivation — `emailForAgent(agentId, domain, system_name, [])`
 *      (single rule; agentId default 'agent' ⇒ `agent@domain`, shared-domain
 *      ⇒ `agent.<system_name>@domain`).
 *   3) adopt — an existing binding for the agent id is adopted (no network);
 *      otherwise `autoBind` registers + writes the binding (short-circuits
 *      with {exists:true} when the email's binding already exists).
 *
 * The per-platform POINTER (~/.<platform>/.agentmail) is intentionally NOT
 * written here — it is package-level (dsh has none; pi/openclaw own theirs).
 * Callers that need a pointer write it after this resolves.
 */
import {
  autoBind,
  emailForAgent,
  readSystemConfig,
  type AdminClientLike,
  type AutoBindResult,
} from './auto-bind.js'
import { detectSystemForHome, ensureSystem } from './ensure-system.js'
import { loadAgentConfig, loadConfigByAgentId } from './config.js'
import type { AgentConfig } from './types.js'

export interface EnsureAgentBindingOptions {
  /** platform home root (e.g. ~/.dsh, ~/.pi, ~/.openclaw). */
  home: string
  /** agent id for address derivation + binding; default 'agent'. */
  agentId?: string
  /**
   * address-base aliases for emailForAgent (openclaw: ['main'] so the host id
   * 'main' derives the uniform address base 'agent'). dsh/pi pass none.
   */
  aliases?: readonly string[]
  /**
   * persist `agent_id` into the binding's extraFields (default true). dsh
   * bindings carry no agent_id (pre-existing behaviour) ⇒ dsh passes false.
   */
  writeAgentId?: boolean
  /** local inbound endpoint URL (registered + persisted into the binding). */
  webhookUrl?: string
  /** manager address (welcome recipient); defaults to env resolution. */
  managerAddress?: string
  /** log sink (defaults to a no-op). */
  log?: (line: string) => void
  /** test seams (passed through to autoBind: bypass network / override config). */
  transport?: AdminClientLike
  skipRegister?: boolean
  gatewayUrl?: string
  adminKey?: string
  /** test seam: exec override for the CLI reverse-call (ensureSystem). */
  exec?: (cmd: string, args: string[]) => Promise<{ code: number; stdout: string }>
}

export interface EnsureAgentBindingResult {
  ok: boolean
  error?: string
  systemId?: string
  /** derived/adopted address (set when ok). */
  email?: string
  /** true = an existing binding was adopted or already present (no fresh register). */
  exists?: boolean
  /** true = this call activated the system (no owning system before). */
  activated?: boolean
  /** resolved config (set when ok). */
  config?: AgentConfig
}

export async function ensureAgentBinding(
  opts: EnsureAgentBindingOptions,
): Promise<EnsureAgentBindingResult> {
  const log = opts.log ?? (() => undefined)
  const home = opts.home.trim()
  const agentId = (opts.agentId ?? 'agent').trim() || 'agent'
  if (!home) return { ok: false, error: 'home is required' }

  // 1) System readiness (same semantics as the pre-existing reverse-call:
  //    an owning system short-circuits; otherwise activate via CLI L1 ABI).
  let systemId = ''
  let activated = false
  try {
    const owned = await detectSystemForHome(home)
    if (owned) {
      systemId = owned
    } else {
      const r = await ensureSystem(
        opts.exec ? { systemHome: home, exec: opts.exec } : { systemHome: home },
      )
      if (!r.ok) {
        return { ok: false, error: r.error ?? 'system ensure failed' }
      }
      systemId = r.systemId ?? ''
      activated = r.activated === true
      if (r.activated) log(`system activated: ${systemId}`)
    }
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
  if (!systemId) return { ok: false, error: 'no system id resolved' }

  let gwDomain = ''
  let gwSystemName = ''
  try {
    const gw = await readSystemConfig(systemId)
    gwDomain = gw.domain ?? ''
    gwSystemName = gw.system_name ?? ''
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
  if (!gwDomain) return { ok: false, error: 'system config has no domain' }

  // 2) Address derivation (single rule; aliases let a host id map to the
  //    uniform address base, e.g. openclaw 'main' → 'agent').
  const email = emailForAgent(agentId, gwDomain, gwSystemName, opts.aliases ?? [])

  // 3) Adopt an existing binding for this agent id when present (no network).
  //    dsh bindings carry no agent_id ⇒ this lookup falls through harmlessly
  //    and the autoBind exists-guard (by email) handles the short-circuit.
  try {
    const byAgent = await loadConfigByAgentId(systemId, agentId)
    if (byAgent && byAgent.api_key) {
      log(`adopted existing binding ${byAgent.email} (system ${systemId})`)
      return {
        ok: true,
        systemId,
        email: byAgent.email,
        exists: true,
        activated,
        config: byAgent,
      }
    }
  } catch {
    // binding lookup failure is not fatal — fall through to autoBind.
  }

  // 4) Idempotent autoBind (register + write binding; exists-guard by email).
  let res: AutoBindResult
  try {
    const manager =
      opts.managerAddress?.trim() ||
      process.env.AIMAIL_MANAGER_ADDRESS?.trim() ||
      process.env.MANAGER?.trim() ||
      ''
    res = await autoBind({
      systemId,
      email,
      webhookUrl: opts.webhookUrl ?? '',
      ...(manager ? { managerAddress: manager } : {}),
      extraFields: opts.writeAgentId === false ? {} : { agent_id: agentId },
      ...(opts.transport ? { transport: opts.transport } : {}),
      ...(opts.skipRegister !== undefined ? { skipRegister: opts.skipRegister } : {}),
      ...(opts.gatewayUrl !== undefined ? { gatewayUrl: opts.gatewayUrl } : {}),
      ...(opts.adminKey !== undefined ? { adminKey: opts.adminKey } : {}),
    })
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : String(e) }
  }
  if (!(res.registered || res.exists)) {
    return { ok: false, error: 'autoBind produced neither registered nor exists' }
  }
  log(`default agent bound: ${email}${res.exists ? ' (existing)' : ''}`)

  const config = (await loadAgentConfig(systemId, email).catch(() => undefined)) ?? undefined
  return {
    ok: true,
    systemId,
    email,
    exists: res.exists === true,
    activated,
    ...(config ? { config } : {}),
  }
}
