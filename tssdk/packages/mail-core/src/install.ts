/**
 * System-level install/activation core (tssdk side) — PARITY with
 * pysdk/install_core.py.
 *
 * Architecture (2026-09 user ruling): install implementation lives in the
 * SDKs — pysdk (python hosts: hermes/deer-flow) and tssdk (TS hosts: dsh/pi/
 * openclaw) each carry a parity implementation of the same core (same wire
 * protocol, same semantics, same on-disk layout under
 * ~/.aimail/systems/{sid}/aimail_gateway.json). The CLI is a thin dispatcher.
 *
 * Function surface mirrors pysdk/install_core.py 1:1:
 *   installSystem          ↔ install_system          (dual path A/B + reset)
 *   saveSystemConfig       ↔ save_system_config
 *   createAgentAdminKey    ↔ create_agent_admin_key  (agent_admin downgrade)
 *   detectSystemForHome    ↔ detect_system_for_home  (unique-owner reuse)
 *   activateSystem         ↔ GatewayClient.activate_system + aimail_tools
 *                            activate_system user-facing shape
 *
 * Known divergence: webhook_host public-reachability probing (python
 * detect_webhook_host) is python-host only; TS hosts receive via the local
 * bridge route and pass webhookHost through when set.
 */
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { gatewayConfigPath } from './config.js'
import { listSystemDirs } from './auto-bind.js'
import type { AdminClientLike } from './auto-bind.js'
import { GatewayClient } from './gateway.js'

// ── Config persistence ─────────────────────────────────────────

export interface SystemConfigInput {
  gateway_url: string
  admin_key: string
  system_id: string
  domain?: string
  system_name?: string
  /** raw snapshot storage toggle (default true — mirrors python). */
  save_raw_snapshots?: boolean
  manager_address?: string
  webhook_host?: string
  /** platform/agent home root (hermes=~/.hermes, openclaw=~/.openclaw). */
  system_home?: string
}

/**
 * Write ~/.aimail/systems/{sid}/aimail_gateway.json atomically, 0600.
 * Mirrors python save_system_config field set exactly.
 */
export async function saveSystemConfig(cfg: SystemConfigInput): Promise<string> {
  const p = await gatewayConfigPath(cfg.system_id)
  await fs.mkdir(path.dirname(p), { recursive: true, mode: 0o700 })
  const body: Record<string, unknown> = {
    gateway_url: cfg.gateway_url,
    admin_key: cfg.admin_key,
    system_id: cfg.system_id,
    system_name: cfg.system_name ?? '',
    save_raw_snapshots: cfg.save_raw_snapshots ?? true,
  }
  if (cfg.domain) body['domain'] = cfg.domain
  if (cfg.manager_address) body['manager_address'] = cfg.manager_address
  if (cfg.webhook_host) body['webhook_host'] = cfg.webhook_host
  if (cfg.system_home) body['system_home'] = cfg.system_home
  const tmp = `${p}.tmp`
  await fs.writeFile(tmp, JSON.stringify(body, null, 2) + '\n', { mode: 0o600 })
  await fs.rename(tmp, p)
  await fs.chmod(p, 0o600)
  return p
}

// ── Activation (public — the code IS the credential) ───────────

export interface ActivateSystemOptions {
  gatewayUrl: string
  code: string
  systemName?: string
  domain?: string
  /** test seam (replaces the real GatewayClient). */
  transport?: AdminClientLike
  timeoutMs?: number
}

export interface ActivateSystemResult {
  success: boolean
  error?: string
  raw_key?: string
  system_id?: string
  system_name?: string
  domain?: string
}

/**
 * POST /api/v1/activate-system (no auth). User-facing shape mirrors
 * aimail_tools.activate_system: success requires a raw_key — the server has
 * no `success` field and a system can exist while raw_key is missing
 * (2026-08-18 python lesson), so raw_key presence is the authoritative gate.
 */
export async function activateSystem(
  opts: ActivateSystemOptions,
): Promise<ActivateSystemResult> {
  const client: AdminClientLike =
    opts.transport ??
    new GatewayClient(opts.gatewayUrl, '', opts.timeoutMs ?? 30_000)
  const body: Record<string, unknown> = { code: opts.code }
  if (opts.systemName) body['system_name'] = opts.systemName
  if (opts.domain) body['domain'] = opts.domain
  const result = await client.request('POST', '/api/v1/activate-system', body)
  const rawKey = String(result.raw_key ?? '')
  if (!rawKey) {
    const msg =
      String(result.error ?? '') + String(result.detail ?? '') ||
      `Activation failed (HTTP ${result.status})`
    return { success: false, error: msg }
  }
  return {
    success: true,
    raw_key: rawKey,
    system_id: String(result.system_id ?? ''),
    system_name: String(result.system_name ?? ''),
    domain: String(result.domain ?? ''),
  }
}

// ── Agent-admin key downgrade ──────────────────────────────────

export interface CreateAgentAdminKeyOptions {
  gatewayUrl: string
  systemAdminKey: string
  systemId: string
  managerAddress?: string
  transport?: AdminClientLike
  timeoutMs?: number
}

/**
 * Create an agent_admin key and replace admin_key in the system config.
 * Returns the agent_admin key on success, or the ORIGINAL system key when
 * creation failed (system key stays usable). Mirrors python
 * create_agent_admin_key.
 */
export async function createAgentAdminKey(
  opts: CreateAgentAdminKeyOptions,
): Promise<string> {
  const client: AdminClientLike =
    opts.transport ??
    new GatewayClient(
      opts.gatewayUrl,
      opts.systemAdminKey,
      opts.timeoutMs ?? 30_000,
      opts.systemId, // system-level keys: identity IS the system_id
    )
  const result = await client.request('POST', '/api/v1/admin/api-keys', {
    system_id: opts.systemId,
    email_address: opts.managerAddress ?? '',
    scopes: ['agent_admin'],
    category: 'agent_admin',
  })
  const raw = String(result.raw_key ?? '')
  if (!raw) {
    return opts.systemAdminKey // keep the system key — still usable
  }
  // Replace admin_key in the config file.
  const p = await gatewayConfigPath(opts.systemId)
  try {
    const cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, unknown>
    cfg['admin_key'] = raw
    const tmp = `${p}.tmp`
    await fs.writeFile(tmp, JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
    await fs.rename(tmp, p)
    await fs.chmod(p, 0o600)
  } catch {
    /* config missing/unreadable — key still returned */
  }
  return raw
}

// ── Install orchestration — dual path (A: admin_key reset / B: code) ──

export interface InstallSystemOptions {
  gatewayUrl: string
  systemId?: string
  /** Path A — already-activated system (reset semantics). */
  adminKey?: string
  /** Path B — new system activation. */
  productCode?: string
  systemName?: string
  domain?: string
  saveRawSnapshots?: boolean
  managerAddress?: string
  webhookHost?: string
  systemHome?: string
  /** test seam. */
  transport?: AdminClientLike
  timeoutMs?: number
}

export interface InstallSystemResult {
  success: boolean
  error?: string
  system_id?: string
  admin_key?: string
  gateway_url?: string
  domain?: string
  system_name?: string
  path?: 'admin_key' | 'activation'
}

/** Read raw cfg JSON, tolerating absence (→ {}). */
async function readCfg(systemId: string): Promise<Record<string, unknown>> {
  try {
    const p = await gatewayConfigPath(systemId)
    return JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, unknown>
  } catch {
    return {}
  }
}

/** Merge back prev-only business fields (default_agent_name/bridge_port/mode…). */
async function mergeBackPrev(systemId: string, prev: Record<string, unknown>): Promise<void> {
  const written = new Set([
    'gateway_url', 'admin_key', 'system_id', 'system_name',
    'save_raw_snapshots', 'domain', 'manager_address', 'webhook_host',
    'system_home',
  ])
  const p = await gatewayConfigPath(systemId)
  try {
    const cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, unknown>
    let changed = false
    for (const [k, v] of Object.entries(prev)) {
      if (!(k in cfg) && !written.has(k)) {
        cfg[k] = v
        changed = true
      }
    }
    if (changed) {
      const tmp = `${p}.tmp`
      await fs.writeFile(tmp, JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
      await fs.rename(tmp, p)
      await fs.chmod(p, 0o600)
    }
  } catch {
    /* best effort */
  }
}

/**
 * Unified system install — parity with python install_system.
 * Provide gatewayUrl + systemId + ONE of (adminKey, productCode).
 */
export async function installSystem(
  opts: InstallSystemOptions,
): Promise<InstallSystemResult> {
  if (!opts.gatewayUrl) {
    return { success: false, error: 'gateway_url is required' }
  }

  // ── Path A: admin_key provided (already-activated system) ──
  if (opts.adminKey) {
    if (!opts.systemId) {
      return { success: false, error: 'system_id is required for admin_key path' }
    }
    // reset semantics: empty params inherit existing business fields; only
    // core connection params are rewritten (mirrors python exactly).
    const prev = await readCfg(opts.systemId)
    const prevSave = prev.save_raw_snapshots
    await saveSystemConfig({
      gateway_url: opts.gatewayUrl,
      admin_key: opts.adminKey,
      system_id: opts.systemId,
      domain: opts.domain || String(prev.domain ?? 'admin.local'),
      system_name: opts.systemName || String(prev.system_name ?? ''),
      save_raw_snapshots:
        opts.saveRawSnapshots !== undefined || prevSave === undefined
          ? (opts.saveRawSnapshots ?? true)
          : Boolean(prevSave),
      manager_address: opts.managerAddress || String(prev.manager_address ?? ''),
      webhook_host: opts.webhookHost || String(prev.webhook_host ?? ''),
      system_home: opts.systemHome || String(prev.system_home ?? ''),
    })
    await mergeBackPrev(opts.systemId, prev)
    const agentKey = await createAgentAdminKey({
      gatewayUrl: opts.gatewayUrl,
      systemAdminKey: opts.adminKey,
      systemId: opts.systemId,
      ...(opts.managerAddress !== undefined
        ? { managerAddress: opts.managerAddress }
        : {}),
      ...(opts.transport !== undefined ? { transport: opts.transport } : {}),
      ...(opts.timeoutMs !== undefined ? { timeoutMs: opts.timeoutMs } : {}),
    })
    return {
      success: true,
      system_id: opts.systemId,
      path: 'admin_key',
      admin_key: agentKey,
    }
  }

  // ── Path B: product_code provided (new system activation) ──
  if (opts.productCode) {
    const client: AdminClientLike =
      opts.transport ??
      new GatewayClient(opts.gatewayUrl, '', opts.timeoutMs ?? 30_000)
    const body: Record<string, unknown> = { code: opts.productCode }
    if (opts.systemName) body['system_name'] = opts.systemName
    if (opts.domain) body['domain'] = opts.domain
    const result = await client.request('POST', '/api/v1/activate-system', body)
    const status = result.status
    const ok =
      result.success === true ||
      result.success === 'true' ||
      result.success === 'ok' ||
      String(status).toLowerCase() === 'activated' ||
      status === 200 ||
      status === 201 ||
      Boolean(result.raw_key)
    if (!ok) {
      const msg =
        String(result.error ?? '') + String(result.detail ?? '') ||
        `Activation failed (HTTP ${status})`
      return { success: false, error: msg }
    }
    const adminKey = String(result.raw_key ?? '')
    const createdSystemId = String(result.system_id ?? opts.systemId ?? '')
    const createdDomain = String(result.domain ?? opts.domain ?? '')
    if (!adminKey) {
      return { success: false, error: 'No admin_key returned from server' }
    }
    await saveSystemConfig({
      gateway_url: opts.gatewayUrl,
      admin_key: adminKey,
      system_id: createdSystemId,
      domain: createdDomain,
      system_name: opts.systemName || String(result.system_name ?? ''),
      save_raw_snapshots: opts.saveRawSnapshots ?? true,
      manager_address: opts.managerAddress ?? '',
      webhook_host: opts.webhookHost ?? '',
      system_home: opts.systemHome ?? '',
    })
    const agentKey = await createAgentAdminKey({
      gatewayUrl: opts.gatewayUrl,
      systemAdminKey: adminKey,
      systemId: createdSystemId,
      ...(opts.managerAddress !== undefined
        ? { managerAddress: opts.managerAddress }
        : {}),
      ...(opts.transport !== undefined ? { transport: opts.transport } : {}),
      ...(opts.timeoutMs !== undefined ? { timeoutMs: opts.timeoutMs } : {}),
    })
    return {
      success: true,
      system_id: createdSystemId,
      admin_key: agentKey,
      gateway_url: opts.gatewayUrl,
      domain: createdDomain,
      system_name: opts.systemName || String(result.system_name ?? ''),
      path: 'activation',
    }
  }

  return {
    success: false,
    error: 'Either admin_key or product_code is required',
  }
}

// ── Reuse detection (unique home ownership) ────────────────────

function normHome(home: string): string {
  let h = home.trim()
  if (h.startsWith('~/')) h = path.join(os.homedir(), h.slice(2))
  return path.resolve(h).replace(/\/+$/, '')
}

/**
 * system_home → owning system id: scan every systems/{sid} config; a UNIQUE
 * match returns that sid; zero or multiple → '' (never guess). Mirrors python
 * detect_system_for_home.
 */
export async function detectSystemForHome(systemHome: string): Promise<string> {
  if (!systemHome) return ''
  const target = normHome(systemHome)
  if (!target) return ''
  let found = ''
  for (const sid of await listSystemDirs()) {
    let cfg: Record<string, unknown>
    try {
      const p = await gatewayConfigPath(sid)
      cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, unknown>
    } catch {
      continue
    }
    const sh = String(cfg.system_home ?? '')
    if (sh && normHome(sh) === target) {
      if (found) return '' // second owner → ambiguous, don't guess
      found = sid
    }
  }
  return found
}

// ── Install readiness (env-driven self-activation) ─────────────

export interface EnsureInstalledOptions {
  /** env overrides (defaults read from process.env). */
  gatewayUrl?: string
  productCode?: string
  systemName?: string
  domain?: string
  managerAddress?: string
  /** platform/agent home root — narrows reuse to the owning system. */
  systemHome?: string
  /** test seam. */
  transport?: AdminClientLike
  timeoutMs?: number
}

export interface EnsureInstalledResult {
  ok: boolean
  /** owning/sole system id when determinable ('' = systems exist, scope open). */
  systemId?: string
  /** true when this call performed the activation. */
  activated?: boolean
  error?: string
}

/**
 * Install readiness for TS hosts: no system on this machine + AIMAIL_URL +
 * AIMAIL_PRODUCT_CODE present → self-activate (parity path without the python
 * CLI, so `dsh plugin add dsh-aimail` alone is a complete install). Systems
 * already present → ok (scope left to the resolver). Never throws.
 */
export async function ensureSystemInstalled(
  opts: EnsureInstalledOptions = {},
): Promise<EnsureInstalledResult> {
  const env = (k: string): string => process.env[k]?.trim() ?? ''
  const gatewayUrl = opts.gatewayUrl ?? env('AIMAIL_URL')
  const productCode = opts.productCode ?? env('AIMAIL_PRODUCT_CODE')
  const systemHome = opts.systemHome ?? ''

  // 1) A system already exists → done (scope resolution is the caller's job).
  let sid = ''
  if (systemHome) sid = await detectSystemForHome(systemHome)
  const sids = await listSystemDirs()
  if (sid || sids.length > 0) {
    if (!sid) {
      const scope = env('AIMAIL_SYSTEM_ID')
      if (scope && sids.includes(scope)) sid = scope
      else if (sids.length === 1) sid = sids[0] as string
    }
    return { ok: true, systemId: sid, activated: false }
  }

  // 2) No system — activation env required (the code IS the credential).
  if (!gatewayUrl || !productCode) {
    return {
      ok: false,
      error:
        'no aimail system on this machine — provide AIMAIL_URL + AIMAIL_PRODUCT_CODE and restart, or run `aimail install`',
    }
  }
  const r = await installSystem({
    gatewayUrl,
    productCode,
    systemName: opts.systemName ?? env('AIMAIL_SYSTEM_NAME'),
    domain: opts.domain ?? env('AIMAIL_DOMAIN'),
    managerAddress: opts.managerAddress ?? env('AIMAIL_MANAGER_ADDRESS'),
    ...(systemHome !== undefined && systemHome !== ''
      ? { systemHome }
      : {}),
    ...(opts.transport !== undefined ? { transport: opts.transport } : {}),
    ...(opts.timeoutMs !== undefined ? { timeoutMs: opts.timeoutMs } : {}),
  })
  if (!r.success) {
    return { ok: false, error: r.error ?? 'system activation failed' }
  }
  return {
    ok: true,
    ...(r.system_id !== undefined ? { systemId: r.system_id } : {}),
    activated: true,
  }
}
