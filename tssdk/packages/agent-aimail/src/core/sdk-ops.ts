/**
 * sdk-ops — node-side executable op entry (single-line JSON ABI).
 *
 * Contract v1.0 §4.1(2): node transport and Python gate (`python3 -m aimail.sdk_ops <op>`)
 * semantics and envelope are consistent, only transport differs. CLI dispatches and reads results based on registry (typed constants) `ops.argv`:
 *
 *   in :  --op <assemble|update|teardown> --args '<single JSON object>'
 *   out : stdout exactly one line
 *         ok   → {"ok":true,"result":{ ...flat payload... }}
 *         fail → {"ok":false,"kind":"usage"|"import"|"call","exc":"<Cls>","error":"<Cls: msg>"}
 *   exit: 0 ok / 2 usage / 1 other. SDK's own printing goes to stderr, never polluting stdout.
 *
 * Semantics mirror pysdk/sdk_ops.py's _op_assemble / _op_update / _op_teardown item by item
 * (payload keys are read verbatim by CLI: `email`, `filled`, `reason`, `secret`,
 * `actions[].deregister_agent_email`、`actions[].cleanup_system_whitelists.per_key`…)。
 * Judgment and persistence are within this module; callers (CLI/platform entry) only pass parameters and receive single-line JSON.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { randomUUID } from 'node:crypto'
import { ensureSkillToolExposure } from './skill-exposure.js'
import {
  GatewayClient,
  BINDING_FILE,
  cleanAddr,
  emailForAgent,
  ensureBindingWebhookSecret,
  listAgentConfigs,
  loadAgentConfig,
  promptRuleNameOk,
  promptRuleMatches,
  readPromptRules,
  readSystemConfig,
  resolveRegisterWebhook,
  saveAgentConfig,
  saveBinding,
  registerAddress,
  systemDir,
  type AgentConfig,
} from './index.js'

export class UsageError extends Error {}

// ── Envelope ───────────────────────────────────────────────────────────────────

let _sdkVersion = ''
async function sdkVersion(): Promise<string> {
  if (_sdkVersion) return _sdkVersion
  try {
    // Find @aimail/mail-core's package.json upwards from this module's directory (node-side SDK identity)
    let dir = path.dirname(new URL(import.meta.url).pathname)
    for (let i = 0; i < 8; i++) {
      try {
        const pj = JSON.parse(await fs.readFile(path.join(dir, 'package.json'), 'utf-8')) as {
          name?: string
          version?: string
        }
        if (pj.name === '../core/index.js') {
          _sdkVersion = String(pj.version ?? '')
          return _sdkVersion
        }
      } catch {
        /* Find upwards */
      }
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  } catch {
    /* fall through */
  }
  _sdkVersion = ''
  return ''
}

// ── System-level configuration (single source of truth: CLI writes, SDK read-only)─────────────────────────────────────

async function loadSystem(
  systemId: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (!systemId) throw new UsageError('System-level configuration not found: missing system_id')
  // Explicit system_cfg (CLI already holds this system-level configuration) ⇒ use directly, no self-location
  const given = args['system_cfg']
  if (given && typeof given === 'object') {
    const g = { ...(given as Record<string, unknown>) }
    if (g['gateway_url'] && g['admin_key']) return g
  }
  // Self-location: ~/.aimail/systems/<sid>/aimail_gateway.json
  try {
    const cfg = (await readSystemConfig(systemId)) as unknown as Record<string, unknown>
    if (cfg && (cfg['gateway_url'] || cfg['admin_key'])) return cfg
  } catch {
    /* Fall back to gw override */
  }
  // Explicit gw override (test fixture)
  const gw = String(args['gw'] || args['gateway_url'] || '').trim()
  if (gw) {
    return {
      gateway_url: gw,
      admin_key: String(args['admin_key'] || ''),
      system_id: systemId,
      domain: String(args['domain'] || ''),
      system_name: String(args['system_name'] || ''),
    }
  }
  throw new UsageError(`System-level configuration not found system_id=${systemId} (must run aimail install first)`)
}

function gwClient(syscfg: Record<string, unknown>): GatewayClient {
  const url = String(syscfg['gateway_url'] || '').replace(/\/+$/, '')
  const key = String(syscfg['admin_key'] || '')
  if (!url || !key) {
    throw new UsageError('System-level configuration missing gateway_url/admin_key — run aimail install first')
  }
  // identity left blank: gateway picks key by signature (same convention as auto-bind registerAddress)
  return new GatewayClient(url, key, 30_000)
}

// ── admin endpoints (mirrors pysdk/aimail_tools.py _GatewayClient)──────────────────

async function listSystemDomains(c: GatewayClient, sid: string): Promise<Array<Record<string, unknown>>> {
  const r = await c.request('GET', `/api/v1/admin/systems/${sid}/domains`)
  const data = (r['data'] ?? r) as unknown
  return Array.isArray(data) ? (data as Array<Record<string, unknown>>) : []
}

async function listWhitelistsByDomain(c: GatewayClient, domain: string): Promise<Array<Record<string, unknown>>> {
  const r = await c.request('GET', `/api/v1/whitelists?domain=${encodeURIComponent(domain)}`)
  const data = (r['data'] ?? r) as unknown
  return Array.isArray(data) ? (data as Array<Record<string, unknown>>) : []
}

async function deleteWhitelistEntryById(c: GatewayClient, entryId: number): Promise<Record<string, unknown>> {
  const r = await c.request('DELETE', `/api/v1/whitelists/${entryId}`)
  return r as unknown as Record<string, unknown>
}

async function getApiKeyByEmail(c: GatewayClient, email: string): Promise<Record<string, unknown>> {
  const r = await c.request('GET', `/api/v1/admin/api-keys?email=${encodeURIComponent(email)}`)
  const entries = (r['entries'] ?? r['data']) as unknown
  if (Array.isArray(entries) && entries.length) return entries[0] as Record<string, unknown>
  return {}
}

async function deleteApiKey(c: GatewayClient, keyId: number): Promise<Record<string, unknown>> {
  const r = await c.request('DELETE', `/api/v1/admin/api-keys/${keyId}`)
  return r as unknown as Record<string, unknown>
}

// ── deregistration chain (mirrors pysdk/aimail_base.py deregister_agent_email)────────────────

/** Unregister chain (api-key → domain → whitelist), returns status of each step {api_key, domain, whitelist}. */
async function deregisterAgentEmail(
  c: GatewayClient,
  systemId: string,
  email: string,
  managerAddress: string,
): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {}
  // 1. Delete API key (lookup id by email)
  try {
    const k = await getApiKeyByEmail(c, email)
    const id = k['id']
    if (id != null) {
      const r = await deleteApiKey(c, Number(id))
      out['api_key'] = String(r['status'] ?? '')
    } else {
      out['api_key'] = 'not_found'
    }
  } catch (e) {
    out['api_key'] = `err:${e instanceof Error ? e.message : e}`
  }
  // 2. Delete domain entry (by id, fallback by name)
  try {
    const domains = await listSystemDomains(c, systemId)
    let addrId = ''
    for (const d of domains) {
      if (d['domain'] === email) {
        addrId = String(d['id'] ?? '')
        break
      }
    }
    const r = addrId
      ? await c.request('DELETE', `/api/v1/admin/system-domains/${addrId}`)
      : await c.request('DELETE', `/api/v1/admin/system-domains/${encodeURIComponent(email)}`)
    out['domain'] = String(r.status ?? '')
  } catch (e) {
    out['domain'] = `err:${e instanceof Error ? e.message : e}`
  }
  // 3. Whitelist cleanup — exact (domain_addr==email) then delete by id (F10: converge by address on miss)
  try {
    const domain = email.includes('@') ? email.split('@')[1] : ''
    if (!domain) {
      out['whitelist'] = 'unsupported'
    } else {
      const rows = await listWhitelistsByDomain(c, domain)
      const mine = rows.filter((r) => r['domain_addr'] === email && r['id'] != null)
      const exact = managerAddress ? mine.filter((r) => r['value'] === managerAddress) : []
      const targets = exact.length ? exact : mine
      if (targets.length) {
        const st: string[] = []
        for (const r of targets) {
          const rr = await deleteWhitelistEntryById(c, Number(r['id']))
          st.push(String(rr.status ?? ''))
        }
        out['whitelist'] = st.join(',') + (exact.length ? '' : '(by_addr)')
      } else {
        out['whitelist'] = rows.length ? 'not_found_addr' : 'not_found'
      }
    }
  } catch (e) {
    out['whitelist'] = `err:${e instanceof Error ? e.message : e}`
  }
  return out
}

/** Uninstall fallback: clear this system's own whitelist rows (mirrors cleanup_system_whitelists).
 *  Returns {"removed":N,"per_key":{key:n},"errors":[...]}. */
async function cleanupSystemWhitelists(
  c: GatewayClient,
  systemId: string,
  addresses: string[],
  domains: string[],
  deregistered: string[],
): Promise<Record<string, unknown>> {
  const sweep = Array.from(new Set([...domains, ...addresses.filter((a) => !deregistered.includes(a))])).sort()
  if (!sweep.length) return { removed: 0, per_key: {}, errors: [] }
  let removed = 0
  const perKey: Record<string, number> = {}
  const errors: string[] = []
  const seen = new Set<number>()
  for (const key of sweep) {
    let rows: Array<Record<string, unknown>>
    try {
      rows = await listWhitelistsByDomain(c, key)
    } catch (e) {
      errors.push(`list ${key}: ${e instanceof Error ? e.message : e}`)
      continue
    }
    let n = 0
    for (const r of rows) {
      if (r['id'] == null) continue
      if (r['system_id'] != null && r['system_id'] !== systemId) continue // double safety
      const rid = Number(r['id'])
      if (seen.has(rid)) continue
      seen.add(rid)
      try {
        await deleteWhitelistEntryById(c, rid)
        n++
      } catch (e) {
        errors.push(`delete ${key}#${rid}: ${e instanceof Error ? e.message : e}`)
      }
    }
    removed += n
    perKey[key] = n
  }
  return { removed, per_key: perKey, errors }
}

// ── manager resolution (mirrors pysdk resolve_manager_address: explicit > env)────────────
// Single contract env name: AIMAIL_MANAGER_ADDRESS (INTEGRATE_* removed, no compatibility).

const MANAGER_ENV_VARS = ['AIMAIL_MANAGER_ADDRESS'] as const
function resolveManager(explicit: string, syscfg: Record<string, unknown>): string {
  const v = String(explicit || '').trim()
  if (v) return v
  for (const k of MANAGER_ENV_VARS) {
    const e = String(process.env[k] || '').trim()
    if (e) return e
  }
  return String(syscfg['manager_address'] || '').trim()
}

// ── binding location (mirrors pysdk _agent_binding: email exact > agent_id scan all)────

async function agentBinding(
  systemId: string,
  agentId: string,
  email: string,
  explicit: unknown,
): Promise<Record<string, unknown>> {
  // Explicit binding (repair's webhook-secret caller provided the binding directly) ⇒ use as-is
  if (explicit && typeof explicit === 'object') {
    const b = { ...(explicit as Record<string, unknown>) }
    if (email && !b['email']) b['email'] = email
    return b
  }
  if (email) {
    const c = await loadAgentConfig(systemId, email)
    if (c) return { ...(c as unknown as Record<string, unknown>) }
  }
  if (agentId) {
    for (const el of await listAgentConfigs(systemId)) {
      if (el.agent_id === agentId || el.email === agentId) {
        return { ...(el as unknown as Record<string, unknown>) }
      }
    }
  }
  return {}
}

/** Binding backfill (pure write-back, judgment belongs to caller; same shape as pysdk backfill_binding). */
async function backfillBinding(cfg: Record<string, unknown>, systemId: string): Promise<string> {
  const ac = { ...(cfg as unknown as AgentConfig) }
  if (!ac.email) throw new UsageError('backfill_binding: binding missing email')
  ac.system_id = systemId
  return saveAgentConfig(ac, systemId)
}

// ── naming (mirrors plan_address_name main agent chain)────────────────────────────────

function mainAgentPlan(
  requestedName: string,
  domain: string,
  systemName: string,
): {
  default_name: string
  target_name: string
  reg_as: string
  needs_rename: boolean
  email: string
  target_email: string
} {
  // node platform registrar accepts address name (direct, needs_rename=False — F13 root):
  // main agent constant name agent (platform private naming not involved), shared system appends .{system_name}.
  const name = requestedName || 'agent'
  if (!/^[A-Za-z0-9!#$%&'*+\/=?^_`{|}~]+$/.test(name)) {
    throw new UsageError(`illegal address name '${name}': must be atext-no-dot characters (no dot/space/@)`)
  }
  const email = emailForAgent(name, domain, systemName, [])
  return {
    default_name: 'agent',
    target_name: name,
    reg_as: name,
    needs_rename: false,
    email,
    target_email: email,
  }
}

// ── op: assemble ───────────────────────────────────────────────────────────
/** Assembly: self-locating config → naming → registration chain (registerAddress idempotent four steps) → persist binding.
 *  Mirrors pysdk _op_assemble (node host: registrar = this entry itself, no external registrar,
 *  register_spec does not exist). */
export async function opAssemble(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  if (!systemId) throw new UsageError('assemble: system_id is required')
  const syscfg = await loadSystem(systemId, args)
  const domain = String(args['domain'] || syscfg['domain'] || '')
  const systemName = String(args['system_name'] || syscfg['system_name'] || '')
  if (!domain) {
    throw new UsageError(`assemble: system ${systemId} has no domain — complete activation/install first`)
  }
  const requested = String(args['requested_name'] || 'agent')
  const plan = mainAgentPlan(requested, domain, systemName)
  // Explicit email priority (repair's cloud re-pairing goes through assemble, provides address directly)
  const email = String(args['email'] || plan.email)
  const manager = resolveManager(String(args['manager_address'] || ''), syscfg)
  if (!manager) {
    throw new UsageError('assemble: missing manager (registration/whitelist does not accept empty values) — provide -m/--manager or env')
  }
  // Local receive endpoint (registration value = resolveRegisterWebhook three-state resolution by webhook_host)
  let webhookUrl = String(args['local_webhook_url'] || args['webhook_url'] || '')
  if (webhookUrl) {
    webhookUrl = resolveRegisterWebhook(syscfg as never, webhookUrl) || webhookUrl
  }
  // secret single source of truth = local binding (same shape as pysdk register_agent_email defect 10 criteria):
  // Local exists ⇒ use it (never mint new — unconditional minting would make cloud≠local ⇒ inbound always 401, J4e root cause);
  // Local missing, caller provided (repair passes local value) ⇒ use it; both missing ⇒ mint new.
  const _existing = await loadAgentConfig(systemId, email)
  const _existingSecret = String((_existing ?? {})['webhook_secret'] ?? '').trim()
  const _callerSecret = String(args['webhook_secret'] ?? '').trim()
  const secret =
    _existingSecret ||
    _callerSecret ||
    randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
  const reg = await registerAddress({
    systemId,
    email,
    webhookUrl,
    webhookSecret: secret,
    managerAddress: manager,
    ...(String(syscfg['gateway_url'] || '') ? { gatewayUrl: String(syscfg['gateway_url']) } : {}),
    ...(String(syscfg['admin_key'] || '') ? { adminKey: String(syscfg['admin_key']) } : {}),
  })
  const registered = reg.exists ? { exists: true } : { api_key: reg.api_key ?? '' }

  // Persist binding: new registration ⇒ saveBinding; already exists ⇒ reuse local (supplement manager/webhook facts).
  // Idempotency key: exists branch if local has no api_key binding ⇒ loud failure (consistent with python auto-bind
  // "already exists but no local binding", deregister first then retry).
  // G1b(2026-10-13)→P3-C(2026-10-17):preset —— host concept, self-reported by the SDK side
  // (dsh register-cli assembleExtra, 10-11 three-source adjudication "host self-report/internal → SDK internal");
  // op envelope merge `{...args, ...extra}` carries it in ⇒ if non-empty, write to binding top-level (extra channel,
  // both fresh/exists branches write ⇒ idempotent); dsh check session dimension criteria only recognize it (adapters.rs).
  // CLI registry register.args no longer declares host concept (P3-C removed `--preset mail`).
  const preset = String(args['preset'] || '').trim()
  const presetExtra: Record<string, unknown> = preset ? { preset } : {}
  let bindingPath = ''
  if (reg.exists) {
    if (_existing && _existing.api_key) {
      // exists branch: local secret is the single source of truth —— if missing locally but already synced on gateway side via input params/new value ⇒ write back to local
      // (mirrors pysdk aimail_base.py:2472-2494 "first write back this key to binding then PUT", card B ③ same criteria).
      const merged: Record<string, unknown> = {
        ..._existing,
        manager_address: manager,
        ...presetExtra,
      }
      if (!String(merged['webhook_secret'] ?? '').trim()) {
        merged['webhook_secret'] = secret
      }
      bindingPath = await saveAgentConfig(merged as unknown as AgentConfig, systemId)
    } else {
      throw new UsageError(
        `assemble: ${email} already exists in system ${systemId} but no binding with api_key locally —— ` +
          `deregister that address first (aimail deregister --email ${email}) or restore ${BINDING_FILE}`,
      )
    }
  } else {
    const apiKey = String(reg.api_key ?? '')
    if (!apiKey) throw new UsageError(`assemble: ${email} registration did not return api_key(${JSON.stringify(registered)})`)
    bindingPath = await saveBinding({
      systemId,
      email,
      apiKey,
      webhookUrl,
      webhookSecret: secret,
      managerAddress: manager,
      gateway: syscfg as never,
      extra: presetExtra,
    })
  }
  // Shared assembly step: if the host's built-in skill/tool exposure layer is disabled by default, the platform passes (patch file + ids to enable)
  // id) passed in as **data**; hosts that natively expose it do not pass it ⇒ behavior unchanged (no platform-specific code path).
  const exposePatch = String(args['skill_expose_patch'] || '')
  const skillExposure = exposePatch
    ? ensureSkillToolExposure({
        patchFile: exposePatch,
        ids: String(args['skill_expose_ids'] || '')
          .split(/[\s,]+/)
          .filter(Boolean),
      })
    : null
  return {
    ok: true,
    email,
    plan,
    registrar: { mode: 'self', rc: 0 },
    renamed: null,
    registered,
    binding_path: bindingPath,
    sdk_version: await sdkVersion(),
    skill_exposure: skillExposure,
  }
}

// ── op: update ─────────────────────────────────────────────────────────────
/** Controlled changes: rename | set-manager | prompt | persona | webhook-secret | backfill | repair.
 *  Mirrors pysdk _op_update (payload keys are identical). */
export async function opUpdate(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  if (!systemId) throw new UsageError('update: system_id is required')
  const syscfg = await loadSystem(systemId, args)
  const action = String(args['action'] || '').trim()
  const email = String(args['email'] || args['old_email'] || '')
  const client = gwClient(syscfg)

  // repair system-level: no target given ⇒ iterate all bindings of that system (same semantics as python)
  if (action === 'repair' && !(args['email'] || args['agent_id'])) {
    const filledFlat: string[] = []
    for (const row of await listAgentConfigs(systemId)) {
      const em = String(row.email || '')
      if (!em) continue
      const r = await opUpdate({ ...args, action: 'repair', email: em, agent_id: em })
      for (const f of (r['filled'] as string[] | undefined) ?? []) filledFlat.push(`${em}:${f}`)
    }
    return { ok: true, action: 'repair', sdk_version: await sdkVersion(), filled: filledFlat, count: filledFlat.length }
  }

  const cfg = await agentBinding(systemId, String(args['agent_id'] || ''), email, args['binding'])
  let r: Record<string, unknown>

  if (action === 'rename') {
    r = await opRename(systemId, String(args['old_email'] || email), String(args['new_name'] || args['name'] || ''), cfg, client)
  } else if (action === 'set-manager') {
    const mgr = resolveManager(String(args['manager_address'] || ''), syscfg)
    if (!mgr || !mgr.includes('@')) throw new UsageError(`invalid manager address '${args['manager_address']}'`)
    const res = await client.request('PUT', `/api/v1/admin/agent-meta/${encodeURIComponent(email)}`, { manager_address: mgr })
    if (res.status === 0 || res.status >= 400) {
      throw new UsageError(`updating manager on the gateway failed: ${JSON.stringify({ status: res.status, error: String(res.error ?? '') })}`)
    }
    cfg['manager_address'] = mgr
    r = { binding_path: await backfillBinding(cfg, systemId) }
  } else if (action === 'backfill') {
    r = { binding_path: await backfillBinding(cfg, systemId) }
  } else if (action === 'repair') {
    // Repair determination is contained within the SDK (idempotent: only records fields actually filled; api_key is not treated as a local gap)
    const filled: string[] = []
    for (const k of ['gateway_url', 'domain', 'system_name']) {
      if (!String(cfg[k] || '').trim()) {
        const v = String(syscfg[k] || '').trim()
        if (v) {
          cfg[k] = v
          filled.push(k)
        }
      }
    }
    if (!String(cfg['manager_address'] || '').trim()) {
      const v = resolveManager('', syscfg)
      if (v) {
        cfg['manager_address'] = v
        filled.push('manager_address')
      }
    }
    // webhook_url aligned with live route (replace if not equal; do nothing if equal ⇒ idempotent)
    const routes = (args['routes'] ?? {}) as Record<string, unknown>
    const local =
      String(routes[email] || cfg['local_webhook_url'] || args['local_webhook_url'] || '')
    if (local) {
      const want = resolveRegisterWebhook(syscfg as never, local)
      if (want && String(cfg['webhook_url'] || '').trim() !== want.trim()) {
        cfg['webhook_url'] = want
        filled.push('webhook_url')
      }
    }
    // secret: reuse existing local one (single source of truth); if missing ⇒ generate and write back to binding.
    // Shape is identical to python ensure_binding_webhook_secret {changed,secret,path,reason,detail}
    const ensured = await ensureBindingWebhookSecret(cfg, String(cfg['_config_path'] || ''))
    const secretPath = String(cfg['_config_path'] || '')
    const secretOut: Record<string, unknown> = ensured.provisioned
      ? { changed: true, secret: ensured.secret, path: secretPath, reason: 'provisioned', detail: '' }
      : { changed: false, secret: ensured.secret, path: secretPath, reason: 'exists', detail: '' }
    const out: Record<string, unknown> = { secret: secretOut }
    if (filled.length) {
      try {
        out['backfill'] = await backfillBinding(cfg, systemId)
      } catch (e) {
        out['backfill'] = { error: String(e) }
      }
    }
    out['filled'] = filled
    r = out
  } else if (action === 'prompt' || action === 'persona' || action === 'webhook-secret') {
    if (action === 'prompt') {
      // Name passes SDK predicate (promptRuleNameOk = same shape as python PROMPT_RULE_NAME_RE)
      const list = (args['prompt_rules'] ??
        (args['updates'] as Record<string, unknown> | undefined)?.['prompt_rules']) as unknown
      if (Array.isArray(list)) {
        for (const rule of list) {
          const n = rule && typeof rule === 'object' ? String((rule as Record<string, unknown>)['name'] ?? '') : ''
          if (n && !promptRuleNameOk(n)) throw new UsageError(`update action=prompt: invalid rule name '${n}'`)
        }
      }
    }
    const patch: Record<string, unknown> = {}
    if (action === 'prompt') {
      const v = args['prompt_rules'] ?? (args['updates'] as Record<string, unknown> | undefined)?.['prompt_rules']
      if (v !== undefined && v !== null) patch['prompt_rules'] = v
    } else if (action === 'persona') {
      const v = args['persona'] ?? (args['updates'] as Record<string, unknown> | undefined)?.['persona']
      if (v !== undefined && v !== null) patch['persona'] = v
    } else {
      // webhook-secret: value priority (top-level key > updates envelope); no value ⇒ ensure semantics
      // (CLI repair.rs:872 contract v1.0 §4.1: calling update without a value expects reuse of local/generate and write back).
      const v = args['webhook_secret'] ?? (args['updates'] as Record<string, unknown> | undefined)?.['webhook_secret']
      if (v !== undefined && v !== null && String(v).trim() !== '') {
        patch['webhook_secret'] = v
      } else {
        const ensured = await ensureBindingWebhookSecret(cfg, String(cfg['_config_path'] || ''))
        patch['webhook_secret'] = ensured.secret
      }
    }
    if (Object.keys(patch).length === 0) throw new UsageError(`update action=${action}: missing fields to update`)
    Object.assign(cfg, patch)
    r = { binding_path: await backfillBinding(cfg, systemId) }
  } else {
    throw new UsageError(`update: unknown action='${action}'(rename|set-manager|prompt|persona|webhook-secret|backfill|repair)`)
  }
  return { ok: true, action, sdk_version: await sdkVersion(), ...(r ?? {}) }
}

async function opRename(
  systemId: string,
  oldEmail: string,
  newName: string,
  cfg: Record<string, unknown>,
  client: GatewayClient,
): Promise<Record<string, unknown>> {
  const name = String(newName || '').trim()
  if (!/^[A-Za-z0-9!#$%&'*+\/=?^_`{|}~]+$/.test(name)) {
    throw new UsageError(`invalid address name '${newName}': must be atext without '.' (no dot/space/@)`)
  }
  const old = String(oldEmail || '').trim()
  if (!old || !old.includes('@')) throw new UsageError(`invalid current address '${oldEmail}'`)
  const domain = String(cfg['domain'] || '').trim()
  if (!domain) throw new UsageError('system config lacks domain')
  const systemName = String(cfg['system_name'] || '').trim()
  const newEmail = emailForAgent(name, domain, systemName, [])
  if (newEmail === old) {
    return { old_email: old, new_email: old, unchanged: true, dir: '', moved: false, merged: false, migrated: true, signal: null }
  }
  // 1) Conflict pre-check (local binding, same semantics as python)
  for (const row of await listAgentConfigs(systemId)) {
    const em = String(row.email || '')
    if (!em || em === old) continue
    const base = em.split('@')[0]?.split('.')[0] ?? ''
    if (base === name) throw new UsageError(`new address name '${name}' conflicts with registered address ${em}`)
  }
  // 2) cloud rename (throw on failure —— local unchanged)
  const res = await client.request('POST', `/api/v1/admin/systems/${systemId}/addresses/rename`, { old_email: old, new_email: newEmail })
  if (res.status === 0 || (res.status >= 400 && res.status < 500)) {
    throw new UsageError(
      `server-side rename ${old} -> ${newEmail} failed (local untouched): ` +
        `${JSON.stringify({ status: res.status, error: String(res.error ?? ''), detail: String(res.detail ?? '') })}`,
    )
  }
  // 3) Whitelist orphan row cleanup (best-effort, key = full old address)
  try {
    const rows = await listWhitelistsByDomain(client, old)
    for (const row of rows) {
      if (row && row['domain_addr'] === old && row['id'] != null) {
        await deleteWhitelistEntryById(client, Number(row['id']))
      }
    }
  } catch {
    /* best-effort (same as python: single key failure does not block) */
  }
  // 4) Local binding migration (directory + content completed in one go)
  const baseRoot = systemDir(systemId)
  let dirPath = ''
  let moved = false
  let merged = false
  let migrated = true
  try {
    const entries = await fs.readdir(baseRoot, { withFileTypes: true })
    for (const e of entries) {
      if (!e.isDirectory()) continue
      const jf = path.join(baseRoot, e.name, BINDING_FILE)
      let acfg: Record<string, unknown>
      try {
        acfg = JSON.parse(await fs.readFile(jf, 'utf-8')) as Record<string, unknown>
      } catch {
        continue
      }
      if (String(acfg['email'] || '') !== old) continue
      acfg['email'] = newEmail
      const dst = path.join(baseRoot, cleanAddr(newEmail))
      if (path.join(baseRoot, e.name) !== dst) {
        try {
          await fs.access(dst)
          merged = true
        } catch {
          await fs.mkdir(dst, { recursive: true, mode: 0o700 })
          await fs.rename(jf, path.join(dst, BINDING_FILE))
          moved = true
        }
      }
      dirPath = dst
      await saveAgentConfig({ ...(acfg as unknown as AgentConfig) }, systemId)
      break
    }
  } catch (e) {
    migrated = false
    process.stderr.write(`[sdk-ops] rename: local binding migration failed (cloud already renamed): ${e instanceof Error ? e.message : String(e)}\n`)
  }
  return { old_email: old, new_email: newEmail, unchanged: false, dir: dirPath, moved, merged, migrated, signal: null }
}

// ── op: teardown ───────────────────────────────────────────────────────────
/** Teardown: deregister → whitelist cleanup (fixed order; missing target ⇒ loud failure). Mirrors pysdk _op_teardown.
 *  payload = {ok, actions:[{deregister_agent_email:{...}} | {cleanup_system_whitelists:{...}}], sdk_version}
 *  —— CLI uses action_of(result, key) to fetch the corresponding action from the actions array. */
export async function opTeardown(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  const email = String(args['email'] || '')
  const mode = (args['mode'] ?? {}) as Record<string, unknown>
  const wants = mode['unregister'] ?? true
  const whitelist = mode['whitelist'] ?? false
  const backfill = mode['backfill'] ?? false
  if (!wants && !whitelist && !backfill) {
    // Explicitly close all actions ⇒ idempotent no-op (no network, no target required)
    return { ok: true, actions: [], sdk_version: await sdkVersion() }
  }
  if (!systemId) throw new UsageError('teardown: system_id is required')
  const syscfg = await loadSystem(systemId, args)
  const client = gwClient(syscfg)
  const manager = String(args['manager_address'] || '')
  const actions: Record<string, unknown>[] = []
  if (wants) {
    if (!email) throw new UsageError('teardown: email required (missing target with actions ⇒ loud failure)')
    actions.push({ deregister_agent_email: await deregisterAgentEmail(client, systemId, email, manager) })
  }
  if (whitelist) {
    const addresses = (mode['addresses'] ?? [email]) as string[]
    const domains = (mode['domains'] ?? []) as string[]
    const deregistered = (mode['deregistered'] ?? [email]) as string[]
    actions.push({ cleanup_system_whitelists: await cleanupSystemWhitelists(client, systemId, addresses, domains, deregistered) })
  }
  if (backfill) {
    const cfg = await agentBinding(systemId, String(args['agent_id'] || ''), email, args['binding'])
    actions.push({ backfill_binding: await backfillBinding(cfg, systemId) })
  }
  return { ok: true, actions, sdk_version: await sdkVersion() }
}

// ── op: prompt-test (read-only; contract §4.1 adds three ops, owner 2026-10-10 (a) ruling)──
/** L5 filter + matching read-only check: read binding → `readPromptRules` (filter: dict only · name passes predicate ·
 *  `file` non-empty · skip if `enabled is False` · at least one non-empty field, sorted by name) → per-rule
 *  `promptRuleMatches` (OR within fields / AND across fields). **No network, no disk write** —— same as python gate
 *  `read_prompt_rules` / `prompt_rule_matches` (contract §4.1(2) two-path semantics consistent).
 *
 *  CLI `prompt test` L5 side now calls this op (replacing CLI-side `loader_rules` filter mirror +
 *  per-rule `prompt_rule_matches` python shim call —— node host has no python, shim must be red).
 *  Name validation is also included in this op (`name_ok` = `promptRuleNameOk`) —— CLI `prompt add` / `create-file`
 *  name validity now calls this op (replacing python shim `prompt_rule_name_ok`), `update action=prompt`
 *  still self-validates before writing to disk (double insurance, gate criteria do not depend on caller).
 *  In: `{system_id, email|agent_id, subject, body, sender, recipient, name?}`.
 *  Out: `{ok, rules:[{name, file, match}], name_ok?, sdk_version}` (CLI prints miss/hit in order,
 *  role file existence is still determined locally by CLI, unrelated to this op).
 */
export async function opPromptTest(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  if (!systemId) throw new UsageError('prompt-test: system_id is required')
  const email = String(args['email'] || '')
  const agentId = String(args['agent_id'] || '')
  const subject = String(args['subject'] ?? '')
  const body = String(args['body'] ?? '')
  const sender = String(args['sender'] ?? '')
  const recipient = String(args['recipient'] ?? '')
  // Binding location (email exact > agent_id scan all); read-only, missing binding ⇒ empty rules (no network, no loud failure ——
  // CLI side resolve_target already guarantees the binding exists, this is just defensive).
  const cfg = await agentBinding(systemId, agentId, email, null)
  const rules = readPromptRules(cfg as unknown as AgentConfig).map((rule) => ({
    name: rule.name,
    file: rule.file,
    match: promptRuleMatches(rule, subject, body, sender, recipient),
  }))
  const out: Record<string, unknown> = { ok: true, rules, sdk_version: await sdkVersion() }
  // Name validation (CLI `prompt add` / `create-file` name legality); only returned when name is provided.
  const name = String(args['name'] ?? '')
  if (name) out['name_ok'] = promptRuleNameOk(name)
  return out
}

// ── This module is a **library** ─────────────────────────────────────────────────────────
// opAssemble/opUpdate/opTeardown are called by platform entry points (dsh/pi register-cli.js,
// openclaw's `aimail` subcommand) —— CLI starts those entries via registry ops.argv, which are responsible
// --op/--args parsing and single-line JSON envelope (same shape as python gate `python3 -m aimail.sdk_ops`).
// No argv parsing here, no self-running main, maintaining pure function library semantics.

