/**
 * Bridge route pairing — the ROUTE side of the chain, deliberately decoupled
 * from registration.
 *
 * Owner rulings 2026-09-27:
 *  - the route must be pushed when the host inbound is actually serving, not at
 *    registration time (registration happens before the host restarts; pushing
 *    earlier is what produced "Route created 15:53:17 / removed 15:56:07" in
 *    production on 2026-09-26);
 *  - "registration succeeded" and "route added" are two separate outcomes with
 *    their own success rates — never couple them, never let one gate the other;
 *  - a route the bridge pruned must come back by itself: the bridge health check
 *    deletes routes whose target stays unreachable (probe interval ×
 *    fail_threshold, default ~30s × 6 = 180s) and nothing used to re-add them,
 *    so a host restart left inbound permanently dead (production 2026-09-21).
 *    Every host therefore upserts on startup, once its inbound listener is up.
 *
 * The route target is always the binding's own webhook_url (agentmail.json is
 * the single trusted source for the local receive endpoint) — not a URL the
 * caller recomputes.
 */
import { createConnection } from 'node:net'
import { listSystemDirs, readSystemConfig, registerBridgeRoute } from './auto-bind.js'
import { listAgentConfigs } from './config.js'
import type { AgentConfig } from './types.js'

export type BridgeRouteState = 'ok' | 'no_bridge' | 'host_not_serving' | 'failed'

export interface BridgeRouteOutcome {
  state: BridgeRouteState
  /** address this outcome is about ('' when the outcome covers the whole system). */
  email: string
  /** local receive endpoint the address routes to. */
  target?: string
  adminPort?: number
  /** how many addresses the outcome covers (system-wide outcomes). */
  count?: number
  detail?: string
}

export interface EnsureBridgeRouteOptions {
  systemId: string
  email: string
  /** local receive endpoint (agentmail.json webhook_url wins when present). */
  webhookUrl: string
  bridgeAdminPort?: number
  /**
   * "the local receive endpoint is already serving" — passed as true by hosts
   * that call this from inside their listen callback. Undefined => probed.
   */
  hostServing?: boolean
}

/** TCP probe: is something accepting connections on host:port? */
export async function tcpReachable(host: string, port: number, timeoutMs = 500): Promise<boolean> {
  return await new Promise<boolean>((resolve) => {
    let settled = false
    const sock = createConnection({ host, port })
    const done = (v: boolean) => {
      if (settled) return
      settled = true
      sock.destroy()
      resolve(v)
    }
    sock.setTimeout(timeoutMs)
    sock.once('connect', () => done(true))
    sock.once('timeout', () => done(false))
    sock.once('error', () => done(false))
  })
}

/** The bridge admin API always lives on loopback. */
export function bridgeListening(port: number, timeoutMs = 500): Promise<boolean> {
  return tcpReachable('127.0.0.1', port, timeoutMs)
}

/** Is the local receive endpoint reachable? A remote endpoint is unprobeable locally. */
export async function inboundServing(target: string, timeoutMs = 1500): Promise<boolean> {
  if (!target) return false
  let host = ''
  let port = 80
  try {
    const u = new URL(target)
    host = u.hostname
    port = Number(u.port || (u.protocol === 'https:' ? 443 : 80))
  } catch {
    return false
  }
  if (host !== '127.0.0.1' && host !== 'localhost' && host !== '::1' && host !== '[::1]') return false
  return await tcpReachable(host, port, timeoutMs)
}

export async function resolveBridgeAdminPort(systemId: string): Promise<number> {
  try {
    const gw = await readSystemConfig(systemId)
    return Number(gw.bridge_admin_port ?? 38081) || 38081
  } catch {
    return 38081
  }
}

/**
 * Idempotent route upsert for ONE address, with an explicit outcome.
 * Never throws — a startup hook must not break the host.
 */
export async function ensureBridgeRoute(
  opts: EnsureBridgeRouteOptions,
): Promise<BridgeRouteOutcome> {
  const adminPort = opts.bridgeAdminPort ?? (await resolveBridgeAdminPort(opts.systemId))
  const target = (opts.webhookUrl || '').trim()
  const base: BridgeRouteOutcome = { state: 'failed', email: opts.email, target, adminPort }
  if (!target) {
    return {
      ...base,
      state: 'host_not_serving',
      detail: 'binding has no webhook_url (local receive endpoint)',
    }
  }
  if (!(await bridgeListening(adminPort))) {
    return {
      ...base,
      state: 'no_bridge',
      detail: `no listener on 127.0.0.1:${adminPort} (direct push, or start it with 'aimail bridge --restart')`,
    }
  }
  const serving = opts.hostServing ?? (await inboundServing(target))
  if (!serving) {
    return {
      ...base,
      state: 'host_not_serving',
      detail: `the local receive endpoint is not serving yet (${target})`,
    }
  }
  const res = await registerBridgeRoute({
    systemId: opts.systemId,
    email: opts.email,
    webhookUrl: target,
    bridgeAdminPort: adminPort,
  })
  if (res.ok) return { ...base, state: 'ok' }
  return { ...base, state: 'failed', detail: res.error ?? `HTTP ${res.status ?? '?'}` }
}

/**
 * Startup hook payload: upsert the route for EVERY binding of the system.
 *
 * Called by a host once its inbound listener is up (pi/dsh inside the listen
 * callback, openclaw right after the in-gateway HTTP route is registered).
 * Returns [] when there is nothing to do (no system / no binding), and a single
 * system-wide outcome when the machine has no bridge at all.
 */
export async function ensureBridgeRoutesForSystem(
  systemId?: string,
  opts: { bridgeAdminPort?: number } = {},
): Promise<BridgeRouteOutcome[]> {
  let sids: string[] = []
  if (systemId) sids = [systemId]
  else {
    try {
      sids = await listSystemDirs()
    } catch {
      sids = []
    }
  }
  if (sids.length !== 1) return []
  const sid = sids[0] as string

  let bindings: AgentConfig[] = []
  try {
    bindings = await listAgentConfigs(sid)
  } catch {
    return []
  }
  const rows = bindings.filter(b => (b.webhook_url || '').trim())
  if (rows.length === 0) return []

  const adminPort = opts.bridgeAdminPort ?? (await resolveBridgeAdminPort(sid))
  if (!(await bridgeListening(adminPort))) {
    return [{
      state: 'no_bridge',
      email: '',
      count: rows.length,
      adminPort,
      detail: `no listener on 127.0.0.1:${adminPort} (direct push, or start it with 'aimail bridge --restart')`,
    }]
  }

  const out: BridgeRouteOutcome[] = []
  for (const b of rows) {
    out.push(await ensureBridgeRoute({
      systemId: sid,
      email: b.email,
      webhookUrl: String(b.webhook_url),
      bridgeAdminPort: adminPort,
      hostServing: true,
    }))
  }
  return out
}

/** One-line English status for a route outcome (every host prints the same text). */
export function formatBridgeRouteLine(o: BridgeRouteOutcome): string {
  const who = o.email || `${o.count ?? 0} address(es)`
  switch (o.state) {
    case 'ok':
      return `route: ${who} -> ${o.target}`
    case 'no_bridge':
      return `route skipped for ${who}: ${o.detail ?? 'no local bridge'}`
    case 'host_not_serving':
      return `route skipped for ${who}: ${o.detail ?? 'local receive endpoint not serving'} (registered when the host starts)`
    case 'failed':
      return `route FAILED for ${who}: ${o.detail ?? 'unknown'} -- run 'aimail repair'`
  }
}

/** True when the outcome deserves a warning line rather than an info line. */
export function isBridgeRouteWarning(o: BridgeRouteOutcome): boolean {
  return o.state === 'failed'
}
