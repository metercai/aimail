/**
 * auto-bind — SDK-side agent binding when the machine already has a system
 * config (aimail_gateway.json) but no per-agent binding (agentmail.json).
 *
 * Chain (mirrors Python `register_agent_email` + `register_bridge_route`,
 * and the TS openclaw `registerAgentEmail` 4-step port — but independent of
 * any platform host, signing with the SYSTEM admin key read from
 * `~/.aimail/systems/{sid}/aimail_gateway.json`):
 *
 *   1. readSystemConfig(systemId)          — gateway_url/admin_key/domain/...
 *   2. registerAddress(...)                — 4-step idempotent register chain
 *   3. saveBinding(...)                    — atomic agentmail.json write (0600)
 *   4. registerBridgeRoute(...)            — local bridge route upsert (warn-only)
 *
 * Every step is guarded: autoBind() returns {exists:true} without touching
 * the network when a binding file for the address already exists.
 */
import { randomUUID } from 'node:crypto'
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { AIMAIL_HOME, gatewayConfigPath } from './config.js'
import { agentConfigPath, loadAgentConfig } from './config.js'
import { GatewayClient } from './gateway.js'
import type { GatewayResponse } from './types.js'

/** ~/.aimail/systems/{sid}/aimail_gateway.json (system-level facts). */
export interface SystemGatewayConfig {
  system_id?: string
  gateway_url?: string
  admin_key?: string
  domain?: string
  system_name?: string
  manager_address?: string
  /** local bridge admin port (default 38081). */
  bridge_admin_port?: number
  [k: string]: unknown
}

/** Minimal admin-client surface the register chain depends on (testable). */
export interface AdminClientLike {
  request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
    headers?: Record<string, string>,
    rawBody?: Uint8Array,
  ): Promise<GatewayResponse>
}

/** Enumerate system ids under ~/.aimail/systems/ (missing → []). */
export async function listSystemDirs(): Promise<string[]> {
  const root = path.join(AIMAIL_HOME(), 'systems')
  try {
    const entries = await fs.readdir(root, { withFileTypes: true })
    return entries
      .filter(e => e.isDirectory())
      .map(e => e.name)
      .sort()
  } catch {
    return []
  }
}

/** Read the system gateway config. Throws when missing/unreadable. */
export async function readSystemConfig(systemId: string): Promise<SystemGatewayConfig> {
  // canonical aimail_gateway.json, legacy agentmail_gateway.json auto-migrated
  // on first read (pysdk gateway_api parity)
  const p = await gatewayConfigPath(systemId)
  try {
    return JSON.parse(await fs.readFile(p, 'utf-8')) as SystemGatewayConfig
  } catch {
    throw new Error(
      `gateway config not found (aimail_gateway.json) for system ${systemId} — run 'aimail install' or set AIMAIL_URL + AIMAIL_PRODUCT_CODE (auto-install)`,
    )
  }
}

/**
 * Agent address derivation (Python `email_for_agent` port — cross-system
 * single rule): default agent names (per-platform aliases) normalize to
 * "agent"; every other id keeps its name. Non-atext-no-dot chars (incl. '.')
 * → '_'; empty result falls back to "agent". On shared domains (system_name
 * set) the address is `{base}.{system_name}@{domain}`, else `{base}@{domain}`.
 */
export function emailForAgent(
  agentId: string,
  domain: string,
  systemName = '',
  defaultAliases: readonly string[] = ['default'],
): string {
  let base = defaultAliases.includes(agentId) ? 'agent' : agentId
  base = base.replace(/[^A-Za-z0-9!#$%&'*+\-/=?^_`{|}~]/g, '_')
  if (!base) base = 'agent'
  return systemName ? `${base}.${systemName}@${domain}` : `${base}@${domain}`
}

export interface RegisterAddressOptions {
  systemId: string
  email: string
  /** address receive endpoint (registration parameter; "" = pull mode). */
  webhookUrl: string
  webhookSecret: string
  managerAddress?: string
  /** overrides (defaults read from the system config). */
  gatewayUrl?: string
  adminKey?: string
  /** test seam: replaces the real GatewayClient. */
  transport?: AdminClientLike
  timeoutMs?: number
}

export interface RegisterAddressResult {
  api_key?: string
  activation_code?: string
  /** remote address already existed — webhook config refreshed, no key. */
  exists?: boolean
}

/**
 * 4-step idempotent registration chain, self-contained (system admin key).
 * Returns {api_key} when activation completed; {exists:true} when the address
 * already existed (webhook refreshed); throws otherwise (pending/network…).
 */
export async function registerAddress(
  opts: RegisterAddressOptions,
): Promise<RegisterAddressResult> {
  const gw = await readSystemConfig(opts.systemId)
  const gatewayUrl = opts.gatewayUrl ?? gw.gateway_url ?? ''
  const adminKey = opts.adminKey ?? gw.admin_key ?? ''
  if (!gatewayUrl || !adminKey) {
    throw new Error(
      `auto-bind unavailable: aimail_gateway.json for system ${opts.systemId} has no gateway_url/admin_key`,
    )
  }
  const client: AdminClientLike =
    opts.transport ??
    new GatewayClient(gatewayUrl, adminKey, opts.timeoutMs ?? 30_000, opts.systemId)

  const result = await client.request(
    'POST',
    `/api/v1/admin/systems/${opts.systemId}/addresses?generate_code=true`,
    {
      id: `addr-${opts.email.replace('@', '-at-')}-${Math.floor(Date.now() / 1000)}`,
      email: opts.email,
      webhook_url: opts.webhookUrl,
      webhook_secret: opts.webhookSecret,
      manager_address: opts.managerAddress ?? '',
    },
  )
  const activationCode = String(result.activation_code ?? '')
  const status = String(result.status ?? '')
  if (status && !['created', '200', '201'].includes(status)) {
    const msg = String(result.error ?? '') + String(result.detail ?? '')
    if (/already exists|exists/i.test(msg)) {
      // Idempotent: refresh the webhook config of the existing address.
      const domains = await client.request(
        'GET',
        `/api/v1/admin/systems/${opts.systemId}/domains`,
      )
      const entries = Array.isArray(domains.data)
        ? domains.data
        : (domains.entries as unknown[] | undefined) ?? []
      for (const d of entries) {
        const row = d as Record<string, unknown>
        if (row.domain === opts.email) {
          await client.request(
            'PUT',
            `/api/v1/admin/system-domains/${String(row.id)}`,
            { webhook_url: opts.webhookUrl, webhook_secret: opts.webhookSecret },
          )
          break
        }
      }
      return { exists: true }
    }
    throw new Error(`register failed: ${JSON.stringify(result)}`)
  }
  if (!activationCode) {
    throw new Error(
      `address ${opts.email} registered but no activation code returned (${JSON.stringify(result)})`,
    )
  }
  const act = await client.request('POST', '/api/v1/activate-address', {
    code: activationCode,
    email_address: opts.email,
  })
  const apiKey = act.raw_key as string | undefined
  if (!apiKey) {
    throw new Error(
      `activate-address returned no raw_key for ${opts.email} (${JSON.stringify(act)})`,
    )
  }
  return { api_key: apiKey, activation_code: activationCode }
}

export interface SaveBindingOptions {
  systemId: string
  email: string
  apiKey: string
  webhookUrl?: string
  webhookSecret?: string
  managerAddress?: string
  /** platform fields merged into the binding (agent_id/session_id/preset/...). */
  extra?: Record<string, unknown>
  /** explicit gateway facts (defaults: readSystemConfig). */
  gateway?: SystemGatewayConfig
}

/**
 * Atomic agentmail.json write (0600, tmp+rename) at the address-key path
 * shared with loadConfigByEmail/loadConfigByAgentId. System facts come from
 * the gateway config; platform fields ride along in `extra`.
 * Returns the written config path.
 */
export async function saveBinding(opts: SaveBindingOptions): Promise<string> {
  const gw = opts.gateway ?? (await readSystemConfig(opts.systemId))
  const cfg: Record<string, unknown> = {
    email: opts.email,
    gateway_url: gw.gateway_url ?? '',
    domain: gw.domain ?? '',
    system_id: opts.systemId,
    api_key: opts.apiKey,
  }
  if (gw.system_name) cfg['system_name'] = gw.system_name
  if (gw.manager_address) cfg['manager_address'] = gw.manager_address
  if (opts.managerAddress) cfg['manager_address'] = opts.managerAddress
  if (opts.webhookUrl) cfg['webhook_url'] = opts.webhookUrl
  if (opts.webhookSecret) cfg['webhook_secret'] = opts.webhookSecret
  if (opts.extra) {
    for (const [k, v] of Object.entries(opts.extra)) {
      if (v !== undefined) cfg[k] = v
    }
  }
  const p = agentConfigPath(opts.systemId, opts.email)
  await fs.mkdir(path.dirname(p), { recursive: true, mode: 0o700 })
  const tmp = `${p}.tmp`
  await fs.writeFile(tmp, JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
  await fs.rename(tmp, p)
  await fs.chmod(p, 0o600)
  return p
}

export interface BridgeRouteOptions {
  systemId: string
  email: string
  /** local receive endpoint (full URL incl. path) — the bridge route target. */
  webhookUrl: string
  bridgeAdminPort?: number
  timeoutMs?: number
}

export interface BridgeRouteResult {
  ok: boolean
  status?: number
  error?: string
}

/**
 * Local bridge admin route upsert (POST /api/v1/routes, port=80 placeholder —
 * the bridge ignores it when host is a full URL). Idempotent; every failure
 * is warn-only — a down bridge is repaired later via `aimail repair`.
 */
export async function registerBridgeRoute(
  opts: BridgeRouteOptions,
): Promise<BridgeRouteResult> {
  let port = opts.bridgeAdminPort
  if (!port) {
    try {
      const gw = await readSystemConfig(opts.systemId)
      port = Number(gw.bridge_admin_port ?? 38081) || 38081
    } catch {
      port = 38081
    }
  }
  try {
    const resp = await fetch(`http://127.0.0.1:${port}/api/v1/routes`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email: opts.email, host: opts.webhookUrl, port: 80 }),
      signal: AbortSignal.timeout(opts.timeoutMs ?? 5000),
    })
    if (!resp.ok) {
      const err = `bridge route HTTP ${resp.status}`
      console.warn(`[auto-bind] ${err} for ${opts.email} — run 'aimail repair' later`)
      return { ok: false, status: resp.status, error: err }
    }
    return { ok: true, status: resp.status }
  } catch (e) {
    const err = e instanceof Error ? e.message : String(e)
    console.warn(
      `[auto-bind] bridge route skipped for ${opts.email}: ${err} (bridge not reachable — inbound pairing deferred)`,
    )
    return { ok: false, error: err }
  }
}

export interface AutoBindOptions {
  /** target system; omitted on single-system machines (auto-detected). */
  systemId?: string
  email: string
  /** local receive endpoint (registered + persisted + bridge-routed). */
  webhookUrl: string
  /** default: fresh 64-hex secret. */
  webhookSecret?: string
  managerAddress?: string
  /** platform fields persisted into agentmail.json (agent_id/session_id/...). */
  extraFields?: Record<string, unknown>
  /** test seams (bypass network / override config). */
  transport?: AdminClientLike
  skipBridge?: boolean
  gatewayUrl?: string
  adminKey?: string
}

export interface AutoBindResult {
  email: string
  system_id?: string
  /** local binding already existed — nothing re-registered, no network. */
  exists?: boolean
  /** fresh registration completed + binding written (+ bridge route tried). */
  registered?: boolean
  api_key?: string
  config_path?: string
}

/**
 * Read config → registerAddress → saveBinding → registerBridgeRoute.
 * Exists guard: an agentmail.json binding for the address short-circuits to
 * {exists:true} before any network call.
 */
export async function autoBind(opts: AutoBindOptions): Promise<AutoBindResult> {
  const sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
  if (sids.length === 0) {
    throw new Error(
      'auto-bind: no aimail system on this machine (~/.aimail/systems/) — provide AIMAIL_URL + AIMAIL_PRODUCT_CODE and restart (auto-install), or run `aimail install` first',
    )
  }
  if (sids.length > 1 && !opts.systemId) {
    throw new Error(
      `auto-bind: multiple aimail systems (${sids.join(', ')}) — pass an explicit systemId`,
    )
  }
  const systemId = sids[0] as string

  // exists guard: binding already present → skip registration entirely.
  const existing = await loadAgentConfig(systemId, opts.email)
  if (existing && existing.api_key) {
    const out: AutoBindResult = {
      email: opts.email,
      system_id: systemId,
      exists: true,
      api_key: existing.api_key,
    }
    if (existing._config_path) out.config_path = existing._config_path
    return out
  }

  const gw = await readSystemConfig(systemId)
  const webhookSecret =
    opts.webhookSecret ??
    randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
  const managerAddress = opts.managerAddress ?? gw.manager_address ?? ''
  const reg = await registerAddress({
    systemId,
    email: opts.email,
    webhookUrl: opts.webhookUrl,
    webhookSecret,
    managerAddress,
    ...(opts.gatewayUrl !== undefined ? { gatewayUrl: opts.gatewayUrl } : {}),
    ...(opts.adminKey !== undefined ? { adminKey: opts.adminKey } : {}),
    ...(opts.transport !== undefined ? { transport: opts.transport } : {}),
  })
  if (reg.exists) {
    // Remote address exists but no local binding/api_key is recoverable.
    throw new Error(
      `auto-bind: address ${opts.email} already exists on system ${systemId} but no local ` +
        `binding with an api_key was found — deregister the address first ` +
        `(aimail deregister --email ${opts.email}, or the host-side register/deregister ` +
        `command) or restore agentmail.json`,
    )
  }
  const apiKey = reg.api_key
  if (!apiKey) {
    throw new Error(
      `auto-bind for ${opts.email}: registration did not yield an api_key (${JSON.stringify(reg)})`,
    )
  }
  const configPath = await saveBinding({
    systemId,
    email: opts.email,
    apiKey,
    webhookUrl: opts.webhookUrl,
    webhookSecret,
    managerAddress,
    ...(opts.extraFields !== undefined ? { extra: opts.extraFields } : {}),
    gateway: gw,
  })
  if (!opts.skipBridge) {
    await registerBridgeRoute({ systemId, email: opts.email, webhookUrl: opts.webhookUrl })
  }
  return { email: opts.email, system_id: systemId, registered: true, api_key: apiKey, config_path: configPath }
}
