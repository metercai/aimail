/**
 * auto-bind — SDK-side agent binding when the machine already has a system
 * config (aimail_gateway.json) but no per-agent binding (agentmail.json).
 *
 * Chain (the TS port of Python `register_agent_email`; independent of any
 * platform host, signing with the SYSTEM admin key read from
 * `~/.aimail/systems/{sid}/aimail_gateway.json`):
 *
 *   1. readSystemConfig(systemId)          — gateway_url/admin_key/domain/...
 *   2. registerAddress(...)                — 4-step idempotent register chain
 *   3. saveBinding(...)                    — atomic agentmail.json write (0600)
 *
 * There is no fourth step: the SDK is bridge-agnostic (owner ruling 2026-09-28).
 * Building the local environment (routes included) belongs to the CLI; the host
 * only notifies it that its inbound is live (see inbound-notify.ts).
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
  /** callback entry point the gateway should push to (environment-declared).
   *  tri-state: non-empty = push; explicit '' = pull; absent = local endpoint. */
  webhook_host?: string
  /** 曾有的 bridge_admin_port / webhook_register_url 键随 SDK 去桥化退役
   *  (2026-09-28): 磁盘上残留的值一律忽略, SDK 不再读写桥配置。 */
  [k: string]: unknown
}

/**
 * webhook_host 三态 → 地址注册参数 webhook_url (1:1 with Python
 * aimail_base.resolve_register_webhook_url):
 * - webhook_host 是可投递的绝对 http(s) URL → 用它(环境主控声明的 push 入口)
 * - webhook_host 显式空串 ''               → 空:注册值 = "" (云端不回调;pull)
 * - 无 webhook_host 键                     → 本绑定的本机接收端点(直连/独立场景)
 * - 键在但值不可投递(裸 host / host:port)→ 大声告警 + 退回本机端点(SDK 绝不把
 *   网关无法 POST 的值注册出去)
 * agentmail.json 的 webhook_url 恒为本地端点(注册值与绑定值是两个值)。
 * 2026-09-28 (SDK 去桥化): 解析只看 `webhook_host` 一个来源 —— 经桥响应自采的
 * webhook_register_url 第三来源已退役(磁盘残留值忽略), SDK 不写环境配置。
 */
export function resolveRegisterWebhook(gw: SystemGatewayConfig, localWebhookUrl: string): string {
  const whh = gw === null || gw === undefined ? undefined : gw.webhook_host
  if (whh !== undefined && whh !== null) {
    const declared = String(whh).trim()
    if (!declared) return '' // 显式空 = pull(云端不回调): 注册值就是空
    if (declared.startsWith('http://') || declared.startsWith('https://')) return declared
    // 裸 host / host:port ⇒ 网关交付必然 builder error: 退回本机端点(绝不注册不可投递值)
    console.warn(
      `[auto-bind] webhook_host ${JSON.stringify(declared)} is not an absolute http(s) URL — ` +
        `the cloud cannot POST to it, so the local endpoint (${localWebhookUrl}) is registered instead`,
    )
    return localWebhookUrl
  }
  return localWebhookUrl
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
  const p = await gatewayConfigPath(systemId)
  try {
    return JSON.parse(await fs.readFile(p, 'utf-8')) as SystemGatewayConfig
  } catch {
    throw new Error(
      `gateway config not found (aimail_gateway.json) for system ${systemId} — run 'aimail install' or let the host plugin ensure it (aimail install --system-only)`,
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
    // identity 留空:网关按签名自选 key(empty-identity fallback)。传用户侧
    // systemId 会让网关 list_api_keys_by_identity 查不到(admin key 的 identity
    // 是网关内部 system id)→ 401 Invalid X-Api-Signature(实测 docker 回归暴露)
    new GatewayClient(gatewayUrl, adminKey, opts.timeoutMs ?? 30_000)

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
      // Idempotent: refresh the webhook config of the existing address (single
      // implementation shared with autoBind's exists branch).
      await syncAddressWebhook({
        systemId: opts.systemId,
        email: opts.email,
        webhookUrl: opts.webhookUrl,
        webhookSecret: opts.webhookSecret,
        ...(opts.gatewayUrl !== undefined ? { gatewayUrl: opts.gatewayUrl } : {}),
        ...(opts.adminKey !== undefined ? { adminKey: opts.adminKey } : {}),
      })
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

/**
 * Atomic binding write: JSON indent=2 + trailing newline, mode 0600 (tmp+rename),
 * directory 0700 — byte-identical in shape to the Python `write_binding_config`
 * (pysdk/aimail_base.py) and to the previous inline implementation.
 */
async function writeBindingFile(p: string, cfg: Record<string, unknown>): Promise<void> {
  await fs.mkdir(path.dirname(p), { recursive: true, mode: 0o700 })
  const tmp = `${p}.tmp`
  await fs.writeFile(tmp, JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
  await fs.rename(tmp, p)
  await fs.chmod(p, 0o600)
}

/**
 * Ensure a binding carries a local webhook secret — the SINGLE SOURCE OF TRUTH — and
 * return it. An existing secret is reused, never overwritten (overwriting would break a
 * host route that was already reconciled to it). Missing ⇒ a fresh 64-hex secret is
 * generated and written back into the binding file (0600).
 *
 * Mirrors the Python `ensure_binding_webhook_secret` (same mechanism: two UUIDs joined,
 * same 64-hex format, same placement right after `api_key`) so the pull (Python) and
 * push (this) paths share one secret-source semantics.
 */
export async function ensureBindingWebhookSecret(
  existing: Record<string, unknown>,
  cfgPath: string,
): Promise<{ secret: string; provisioned: boolean }> {
  const current = String(existing['webhook_secret'] ?? '').trim()
  if (current) return { secret: current, provisioned: false }
  const secret = randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
  const merged: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(existing)) {
    if (k.startsWith('_')) continue
    merged[k] = v
    if (k === 'api_key') merged['webhook_secret'] = secret
  }
  if (!('webhook_secret' in merged)) merged['webhook_secret'] = secret
  await writeBindingFile(cfgPath, merged)
  return { secret, provisioned: true }
}

export interface SyncWebhookOptions {
  systemId: string
  email: string
  /** the registration value (mode-dependent) — NOT the local receive endpoint. */
  webhookUrl: string
  webhookSecret: string
  gatewayUrl?: string
  adminKey?: string
  /** test seam: replaces the real GatewayClient. */
  transport?: AdminClientLike
  timeoutMs?: number
}

/**
 * Re-pair ONE address's cloud-side webhook (url + secret) with the local binding.
 * Idempotent: locate the address row and PUT both values. Never throws on a lookup
 * miss — returns {ok:false, reason} so the caller reports it instead of pretending
 * success. A missing webhook_secret is never echoed by GET, so "re-pair from the
 * binding" is the only way to guarantee sign-side == verify-side.
 */
export async function syncAddressWebhook(
  opts: SyncWebhookOptions,
): Promise<{ ok: boolean; reason: string }> {
  if (!opts.webhookUrl && !opts.webhookSecret) {
    return { ok: false, reason: 'nothing-to-sync' }
  }
  let gw: SystemGatewayConfig
  try {
    gw = await readSystemConfig(opts.systemId)
  } catch {
    return { ok: false, reason: 'no-system-config' }
  }
  const gatewayUrl = opts.gatewayUrl ?? gw.gateway_url ?? ''
  const adminKey = opts.adminKey ?? gw.admin_key ?? ''
  if (!gatewayUrl || !adminKey) return { ok: false, reason: 'no-admin-credentials' }
  const client: AdminClientLike =
    opts.transport ?? new GatewayClient(gatewayUrl, adminKey, opts.timeoutMs ?? 30_000)
  try {
    const domains = await client.request(
      'GET',
      `/api/v1/admin/systems/${opts.systemId}/domains`,
    )
    const entries = Array.isArray(domains.data)
      ? domains.data
      : ((domains.entries as unknown[] | undefined) ?? [])
    for (const d of entries) {
      const row = d as Record<string, unknown>
      if (row.domain === opts.email) {
        await client.request(
          'PUT',
          `/api/v1/admin/system-domains/${String(row.id)}`,
          { webhook_url: opts.webhookUrl, webhook_secret: opts.webhookSecret },
        )
        return { ok: true, reason: 'refreshed' }
      }
    }
    return { ok: false, reason: 'address-not-found' }
  } catch (e) {
    return { ok: false, reason: `request-failed:${e instanceof Error ? e.message : String(e)}` }
  }
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
  await writeBindingFile(p, cfg)
  return p
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
  /** test seam: skip the cloud-side (url, secret) re-pair inside the exists branch. */
  skipRegister?: boolean
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
  /** the exists branch minted a secret into the binding this run. */
  secret_provisioned?: boolean
  /**
   * the binding's secret was pushed to the cloud registration this run.
   * false + secret_detail ⇒ NOT silently skipped (the reason is reported).
   */
  secret_synced?: boolean
  secret_detail?: string
}

/**
 * Read config → registerAddress → saveBinding (no route step: SDK 去桥化).
 * Exists guard: an agentmail.json binding for the address short-circuits to
 * {exists:true} before any network call.
 */
export async function autoBind(opts: AutoBindOptions): Promise<AutoBindResult> {
  const sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
  if (sids.length === 0) {
    throw new Error(
      'auto-bind: no aimail system on this machine (~/.aimail/systems/) — run `aimail install` (or restart the host plugin to auto-ensure via `aimail install --system-only`)',
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

    // ── webhook secret: single source of truth + idempotent sync (2026-09-28) ──
    // 此前该分支**整体短路**: 绑定里缺 webhook_secret 就永远缺。网关按**注册侧**的
    // secret 签名, 而插件(openclaw-aimail inbound.ts)按**本地绑定**的 secret 验签
    // (`verifySignature(rawBody, sig, cfg.webhook_secret ?? '')`) ⇒ 两边不一致 = 必然
    // 401 bad_signature(生产 J4 实测: 重试 6 次全 401)。修法: **本地绑定即真源** ——
    // 缺就自供(0600 写回), 存在就复用它, 然后把 (url, secret) **幂等同步**到注册侧。
    // 绝不静默跳过: 同步不做/失败都写进返回值(secret_synced / secret_detail)。
    const localWebhook = String(existing.webhook_url || opts.webhookUrl || '').trim()
    let secret = String(existing.webhook_secret ?? '').trim()
    const cfgPath = existing._config_path
    if (cfgPath) {
      const ensured = await ensureBindingWebhookSecret(
        existing as unknown as Record<string, unknown>,
        cfgPath,
      )
      secret = ensured.secret
      out.secret_provisioned = ensured.provisioned
    } else if (!secret) {
      out.secret_detail = 'binding has no path — cannot persist a secret'
    }
    if (secret && !opts.skipRegister) {
      try {
        const gw = await readSystemConfig(systemId)
        const regWebhook = resolveRegisterWebhook(gw, localWebhook)
        // A declared pull deployment registers "" on purpose, so an empty value is a
        // real target here; an empty value with no declaration means there is nothing
        // to register (no local endpoint) — reported, never silently dropped.
        const declaredPull =
          Object.prototype.hasOwnProperty.call(gw, 'webhook_host') &&
          !String(gw.webhook_host ?? '').trim()
        if (regWebhook || declaredPull) {
          const sync = await syncAddressWebhook({
            systemId,
            email: opts.email,
            webhookUrl: regWebhook,
            webhookSecret: secret,
            ...(opts.gatewayUrl !== undefined ? { gatewayUrl: opts.gatewayUrl } : {}),
            ...(opts.adminKey !== undefined ? { adminKey: opts.adminKey } : {}),
            ...(opts.transport !== undefined ? { transport: opts.transport } : {}),
          })
          out.secret_synced = sync.ok
          if (!sync.ok) {
            out.secret_detail = sync.reason
            console.warn(
              `[auto-bind] webhook secret not synced for ${opts.email}: ${sync.reason} ` +
                `— run 'aimail repair' later`,
            )
          }
        } else {
          out.secret_synced = false
          out.secret_detail = 'no registration target (no local endpoint, no pull declaration)'
        }
      } catch (e) {
        out.secret_synced = false
        out.secret_detail = `sync unavailable: ${e instanceof Error ? e.message : String(e)}`
        console.warn(`[auto-bind] ${out.secret_detail} for ${opts.email}`)
      }
    } else if (!opts.skipRegister) {
      out.secret_synced = false
    }

    return out
  }

  const gw = await readSystemConfig(systemId)
  // 注册参数与本地端点两值分离:云端注册参数按 webhook_host 三态
  // (resolveRegisterWebhook);agentmail.json webhook_url 与 bridge route
  // 恒用本地端点(AUDIT-1 P1-8)。
  const localWebhook = opts.webhookUrl
  const regWebhook = resolveRegisterWebhook(gw, localWebhook)
  const webhookSecret =
    opts.webhookSecret ??
    randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
  const managerAddress = opts.managerAddress ?? gw.manager_address ?? ''
  const reg = await registerAddress({
    systemId,
    email: opts.email,
    webhookUrl: regWebhook,
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
    webhookUrl: localWebhook,
    webhookSecret,
    managerAddress,
    ...(opts.extraFields !== undefined ? { extra: opts.extraFields } : {}),
    gateway: gw,
  })
  return { email: opts.email, system_id: systemId, registered: true, api_key: apiKey, config_path: configPath }
}
