/**
 * sdk-ops — node 侧可执行 op 入口(单行 JSON ABI)。
 *
 * 契约 v1.0 §4.1(2):node transport 与 Python 门(`python3 -m aimail.sdk_ops <op>`)
 * 语义与信封一致、仅传输不同。CLI 按 platforms.json 的 `ops.argv` 分派并读结果:
 *
 *   in :  --op <assemble|update|teardown> --args '<单个 JSON 对象>'
 *   out : stdout 恰一行
 *         ok   → {"ok":true,"result":{ ...平铺 payload... }}
 *         fail → {"ok":false,"kind":"usage"|"import"|"call","exc":"<Cls>","error":"<Cls: msg>"}
 *   exit: 0 ok / 2 usage / 1 其它。SDK 自身打印走 stderr,绝不污染 stdout。
 *
 * 语义逐条镜像 pysdk/sdk_ops.py 的 _op_assemble / _op_update / _op_teardown
 * (payload 键被 CLI 逐字读取:`email`、`filled`、`reason`、`secret`、
 * `actions[].deregister_agent_email`、`actions[].cleanup_system_whitelists.per_key`…)。
 * 判定与落盘在本模块内;调用方(CLI/平台入口)只传参、只收单行 JSON。
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { randomUUID } from 'node:crypto'
import {
  GatewayClient,
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

// ── 信封 ───────────────────────────────────────────────────────────────────

let _sdkVersion = ''
async function sdkVersion(): Promise<string> {
  if (_sdkVersion) return _sdkVersion
  try {
    // 从本模块所在目录向上找 @aimail/mail-core 的 package.json(node 侧 SDK 身份)
    let dir = path.dirname(new URL(import.meta.url).pathname)
    for (let i = 0; i < 8; i++) {
      try {
        const pj = JSON.parse(await fs.readFile(path.join(dir, 'package.json'), 'utf-8')) as {
          name?: string
          version?: string
        }
        if (pj.name === '@aimail/mail-core') {
          _sdkVersion = String(pj.version ?? '')
          return _sdkVersion
        }
      } catch {
        /* 向上找 */
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

// ── 系统级配置(单真源:CLI 写、SDK 只读)─────────────────────────────────────

async function loadSystem(
  systemId: string,
  args: Record<string, unknown>,
): Promise<Record<string, unknown>> {
  if (!systemId) throw new UsageError('找不到系统级配置: 缺 system_id')
  // 显式 system_cfg(CLI 已持有该系统级配置)⇒ 直接用,不再自定位
  const given = args['system_cfg']
  if (given && typeof given === 'object') {
    const g = { ...(given as Record<string, unknown>) }
    if (g['gateway_url'] && g['admin_key']) return g
  }
  // 自定位:~/.aimail/systems/<sid>/aimail_gateway.json
  try {
    const cfg = (await readSystemConfig(systemId)) as unknown as Record<string, unknown>
    if (cfg && (cfg['gateway_url'] || cfg['admin_key'])) return cfg
  } catch {
    /* 落到 gw 覆盖 */
  }
  // 显式 gw 覆盖(测试夹具)
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
  throw new UsageError(`找不到系统级配置 system_id=${systemId}(须先 aimail install)`)
}

function gwClient(syscfg: Record<string, unknown>): GatewayClient {
  const url = String(syscfg['gateway_url'] || '').replace(/\/+$/, '')
  const key = String(syscfg['admin_key'] || '')
  if (!url || !key) {
    throw new UsageError('系统级配置缺少 gateway_url/admin_key —— 先跑 aimail install')
  }
  // identity 留空:网关按签名自选 key(与 auto-bind registerAddress 同约定)
  return new GatewayClient(url, key, 30_000)
}

// ── admin 端点(镜像 pysdk/aimail_tools.py _GatewayClient)──────────────────

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

// ── 注销链(镜像 pysdk/aimail_base.py deregister_agent_email)────────────────

/** 注销链(api-key → domain → whitelist),返回各步状态 {api_key, domain, whitelist}。 */
async function deregisterAgentEmail(
  c: GatewayClient,
  systemId: string,
  email: string,
  managerAddress: string,
): Promise<Record<string, unknown>> {
  const out: Record<string, unknown> = {}
  // 1. 删 API key(按 email 查 id)
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
  // 2. 删 domain entry(按 id,回退按名)
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
  // 3. 白名单清理 —— 精确 (domain_addr==email) 后按 id 删(F10:落空时按地址收敛)
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

/** 卸载兜底:清本系统自己的白名单行(镜像 cleanup_system_whitelists)。
 *  返回 {"removed":N,"per_key":{key:n},"errors":[...]}。 */
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
      if (r['system_id'] != null && r['system_id'] !== systemId) continue // 双保险
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

// ── manager 解析(镜像 pysdk resolve_manager_address:显式 > env)────────────

const MANAGER_ENV_VARS = ['AIMAIL_MANAGER', 'AIMAIL_MANAGER_ADDRESS', 'INTEGRATE_MANAGER_ADDRESS'] as const
function resolveManager(explicit: string, syscfg: Record<string, unknown>): string {
  const v = String(explicit || '').trim()
  if (v) return v
  for (const k of MANAGER_ENV_VARS) {
    const e = String(process.env[k] || '').trim()
    if (e) return e
  }
  return String(syscfg['manager_address'] || '').trim()
}

// ── binding 定位(镜像 pysdk _agent_binding:email 精确 > agent_id 扫全部)────

async function agentBinding(
  systemId: string,
  agentId: string,
  email: string,
  explicit: unknown,
): Promise<Record<string, unknown>> {
  // 显式 binding(repair 的 webhook-secret 调用方直接给了绑定)⇒ 直接用
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

/** 绑定回填(纯写回,判定归调用方;与 pysdk backfill_binding 同形)。 */
async function backfillBinding(cfg: Record<string, unknown>, systemId: string): Promise<string> {
  const ac = { ...(cfg as unknown as AgentConfig) }
  if (!ac.email) throw new UsageError('backfill_binding: 绑定缺 email')
  ac.system_id = systemId
  return saveAgentConfig(ac, systemId)
}

// ── 定名(镜像 plan_address_name 主 agent 链)────────────────────────────────

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
  // node 平台注册器接受地址名(直达,needs_rename=False —— F13 根):
  // 主 agent 恒定名 agent(平台私有叫法不参与),shared 系统拼 .{system_name}。
  const name = requestedName || 'agent'
  if (!/^[A-Za-z0-9!#$%&'*+\/=?^_`{|}~]+$/.test(name)) {
    throw new UsageError(`非法地址名 '${name}':须为 atext-no-dot 字符(不能含点/空格/@)`)
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
/** 装配:自定位配置 → 定名 → 注册链(registerAddress 幂等四步)→ 落绑定。
 *  镜像 pysdk _op_assemble(node 宿主:注册器 = 本入口自己,不跑外部注册器,
 *  register_spec 不存在)。 */
export async function opAssemble(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  if (!systemId) throw new UsageError('assemble: 需要 system_id')
  const syscfg = await loadSystem(systemId, args)
  const domain = String(args['domain'] || syscfg['domain'] || '')
  const systemName = String(args['system_name'] || syscfg['system_name'] || '')
  if (!domain) {
    throw new UsageError(`assemble: 系统 ${systemId} 没有 domain —— 先完成激活/安装`)
  }
  const requested = String(args['requested_name'] || 'agent')
  const plan = mainAgentPlan(requested, domain, systemName)
  // 显式 email 优先(repair 的云端重配对走 assemble,直接给地址)
  const email = String(args['email'] || plan.email)
  const manager = resolveManager(String(args['manager_address'] || ''), syscfg)
  if (!manager) {
    throw new UsageError('assemble: 缺 manager(注册/白名单不接受空值)—— 给 -m/--manager 或 env')
  }
  // 本地收端点(注册值 = resolveRegisterWebhook 按 webhook_host 三态解析)
  let webhookUrl = String(args['local_webhook_url'] || args['webhook_url'] || '')
  if (webhookUrl) {
    webhookUrl = resolveRegisterWebhook(syscfg as never, webhookUrl) || webhookUrl
  }
  const secret = randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
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

  // 落绑定:新注册 ⇒ saveBinding;已存在 ⇒ 沿用本地(补 manager/webhook 事实)。
  // 幂等关键:exists 分支若本地无 api_key 绑定 ⇒ 响亮失败(与 python auto-bind
  // "already exists but no local binding" 同语义,先注销再重来)。
  let bindingPath = ''
  const existing = await loadAgentConfig(systemId, email)
  if (reg.exists) {
    if (existing && existing.api_key) {
      const merged = { ...existing, manager_address: manager }
      bindingPath = await saveAgentConfig(merged, systemId)
    } else {
      throw new UsageError(
        `assemble: ${email} 已存在于系统 ${systemId} 但本地没有带 api_key 的绑定 —— ` +
          `先注销该地址(aimail deregister --email ${email})或恢复 agentmail.json`,
      )
    }
  } else {
    const apiKey = String(reg.api_key ?? '')
    if (!apiKey) throw new UsageError(`assemble: ${email} 注册未返回 api_key(${JSON.stringify(registered)})`)
    bindingPath = await saveBinding({
      systemId,
      email,
      apiKey,
      webhookUrl,
      webhookSecret: secret,
      managerAddress: manager,
      gateway: syscfg as never,
    })
  }
  return {
    ok: true,
    email,
    plan,
    registrar: { mode: 'self', rc: 0 },
    renamed: null,
    registered,
    binding_path: bindingPath,
    sdk_version: await sdkVersion(),
  }
}

// ── op: update ─────────────────────────────────────────────────────────────
/** 受控变更:rename | set-manager | prompt | persona | webhook-secret | backfill | repair。
 *  镜像 pysdk _op_update(payload 键逐字一致)。 */
export async function opUpdate(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  if (!systemId) throw new UsageError('update: 需要 system_id')
  const syscfg = await loadSystem(systemId, args)
  const action = String(args['action'] || '').trim()
  const email = String(args['email'] || args['old_email'] || '')
  const client = gwClient(syscfg)

  // repair 系统级:未给目标 ⇒ 遍历该系统全部绑定(与 python 同语义)
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
    // 修复判定收在 SDK 内(幂等:只记真补上的字段;api_key 不作本地缺口)
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
    // webhook_url 与活路由对齐(不等即换;相等不动 ⇒ 幂等)
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
    // secret:复用本地既有(唯一真源);缺失 ⇒ 生成并写回绑定。
    // 形状与 python ensure_binding_webhook_secret 同形 {changed,secret,path,reason,detail}
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
      // 名字过 SDK 谓词(promptRuleNameOk = python PROMPT_RULE_NAME_RE 同形)
      const list = (args['prompt_rules'] ??
        (args['updates'] as Record<string, unknown> | undefined)?.['prompt_rules']) as unknown
      if (Array.isArray(list)) {
        for (const rule of list) {
          const n = rule && typeof rule === 'object' ? String((rule as Record<string, unknown>)['name'] ?? '') : ''
          if (n && !promptRuleNameOk(n)) throw new UsageError(`update action=prompt: 规则名不合法 '${n}'`)
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
      const v = args['webhook_secret'] ?? (args['updates'] as Record<string, unknown> | undefined)?.['webhook_secret']
      if (v !== undefined && v !== null) patch['webhook_secret'] = v
    }
    if (Object.keys(patch).length === 0) throw new UsageError(`update action=${action}: 缺少要更新的字段`)
    Object.assign(cfg, patch)
    r = { binding_path: await backfillBinding(cfg, systemId) }
  } else {
    throw new UsageError(`update: 未知 action='${action}'(rename|set-manager|prompt|persona|webhook-secret|backfill|repair)`)
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
  // 1) 冲突预检(本地绑定,同 python 语义)
  for (const row of await listAgentConfigs(systemId)) {
    const em = String(row.email || '')
    if (!em || em === old) continue
    const base = em.split('@')[0]?.split('.')[0] ?? ''
    if (base === name) throw new UsageError(`new address name '${name}' conflicts with registered address ${em}`)
  }
  // 2) 云端 rename(失败即抛 —— 本地未动)
  const res = await client.request('POST', `/api/v1/admin/systems/${systemId}/addresses/rename`, { old_email: old, new_email: newEmail })
  if (res.status === 0 || (res.status >= 400 && res.status < 500)) {
    throw new UsageError(
      `server-side rename ${old} -> ${newEmail} failed (local untouched): ` +
        `${JSON.stringify({ status: res.status, error: String(res.error ?? ''), detail: String(res.detail ?? '') })}`,
    )
  }
  // 3) 白名单孤儿行清理(best-effort,键 = 完整 old 地址)
  try {
    const rows = await listWhitelistsByDomain(client, old)
    for (const row of rows) {
      if (row && row['domain_addr'] === old && row['id'] != null) {
        await deleteWhitelistEntryById(client, Number(row['id']))
      }
    }
  } catch {
    /* best-effort(与 python 同:单键失败不阻断) */
  }
  // 4) 本地绑定迁移(目录 + 内容一次完成)
  const baseRoot = systemDir(systemId)
  let dirPath = ''
  let moved = false
  let merged = false
  let migrated = true
  try {
    const entries = await fs.readdir(baseRoot, { withFileTypes: true })
    for (const e of entries) {
      if (!e.isDirectory()) continue
      const jf = path.join(baseRoot, e.name, 'agentmail.json')
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
          await fs.rename(jf, path.join(dst, 'agentmail.json'))
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
/** 拆除:注销 → 白名单清理(顺序固定;缺目标 ⇒ 响亮失败)。镜像 pysdk _op_teardown。
 *  payload = {ok, actions:[{deregister_agent_email:{...}} | {cleanup_system_whitelists:{...}}], sdk_version}
 *  —— CLI 用 action_of(result, key) 从 actions 数组取对应动作。 */
export async function opTeardown(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  const email = String(args['email'] || '')
  const mode = (args['mode'] ?? {}) as Record<string, unknown>
  const wants = mode['unregister'] ?? true
  const whitelist = mode['whitelist'] ?? false
  const backfill = mode['backfill'] ?? false
  if (!wants && !whitelist && !backfill) {
    // 显式关闭全部动作 ⇒ 幂等 no-op(不触网、不要求目标)
    return { ok: true, actions: [], sdk_version: await sdkVersion() }
  }
  if (!systemId) throw new UsageError('teardown: 需要 system_id')
  const syscfg = await loadSystem(systemId, args)
  const client = gwClient(syscfg)
  const manager = String(args['manager_address'] || '')
  const actions: Record<string, unknown>[] = []
  if (wants) {
    if (!email) throw new UsageError('teardown: 需要 email(有动作时缺目标 ⇒ 响亮失败)')
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

// ── op: prompt-test(只读;契约 §4.1 三 op 增列,owner 2026-10-10 (a) 裁定)──
/** L5 过滤 + 匹配的只读判定:读绑定 → `readPromptRules`(过滤:仅 dict · 名过谓词 ·
 *  `file` 非空 · `enabled is False` 跳过 · 至少一个非空字段,按名排序)→ 逐规则
 *  `promptRuleMatches`(字段内或 / 字段间且)。**不触网、不写盘** —— 与 python 门
 *  `read_prompt_rules` / `prompt_rule_matches` 同形(契约 §4.1(2) 两路语义一致)。
 *
 *  CLI `prompt test` 的 L5 面改调本 op(替代 CLI 侧 `loader_rules` 过滤镜像 +
 *  逐规则 `prompt_rule_matches` 的 python shim 调用 —— node 宿主无 python,shim 必红)。
 *  名字校验也收进本 op(`name_ok` = `promptRuleNameOk`)—— CLI `prompt add` / `create-file`
 *  的名字合法性改调本 op(替代 python shim `prompt_rule_name_ok`),`update action=prompt`
 *  落盘前仍自校验(双重保险,门内判据不依赖调用方)。
 *  入:`{system_id, email|agent_id, subject, body, sender, recipient, name?}`。
 *  出:`{ok, rules:[{name, file, match}], name_ok?, sdk_version}`(CLI 按序打印 miss/hit,
 *  角色文件存在性仍由 CLI 本地判定,与本 op 无关)。
 */
export async function opPromptTest(args: Record<string, unknown>): Promise<Record<string, unknown>> {
  const systemId = String(args['system_id'] || '')
  if (!systemId) throw new UsageError('prompt-test: 需要 system_id')
  const email = String(args['email'] || '')
  const agentId = String(args['agent_id'] || '')
  const subject = String(args['subject'] ?? '')
  const body = String(args['body'] ?? '')
  const sender = String(args['sender'] ?? '')
  const recipient = String(args['recipient'] ?? '')
  // 绑定定位(email 精确 > agent_id 扫全部);只读,缺绑定 ⇒ 空规则(不触网、不响亮失败 ——
  // CLI 侧 resolve_target 已保证绑定存在,这里只是防御)。
  const cfg = await agentBinding(systemId, agentId, email, null)
  const rules = readPromptRules(cfg as unknown as AgentConfig).map((rule) => ({
    name: rule.name,
    file: rule.file,
    match: promptRuleMatches(rule, subject, body, sender, recipient),
  }))
  const out: Record<string, unknown> = { ok: true, rules, sdk_version: await sdkVersion() }
  // 名字校验(CLI `prompt add` / `create-file` 的名字合法性);仅当给了 name 时返回。
  const name = String(args['name'] ?? '')
  if (name) out['name_ok'] = promptRuleNameOk(name)
  return out
}

// ── 本模块是**库** ─────────────────────────────────────────────────────────
// opAssemble/opUpdate/opTeardown 由平台入口调用(dsh/pi 的 register-cli.js、
// openclaw 的 `aimail` 子命令)——CLI 经注册表 ops.argv 起的就是那些入口,它们负责
// --op/--args 解析与单行 JSON 信封(与 python 门 `python3 -m aimail.sdk_ops` 同形)。
// 这里不解析 argv、不自跑 main,保持纯函数库语义。

