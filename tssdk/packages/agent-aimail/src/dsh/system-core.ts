/**
 * dsh-only `aimail_system` tool — system onboarding (no CLI).
 *
 * One entry, independent from `aimail_address`: redeem a one-time system
 * activation code, write `aimail_gateway.json` (audited contract exception),
 * mint per-domain keys, register the default worker and bind it to DSH's
 * DEFAULT workspace.
 */
import * as path from 'node:path'

import type { Context } from '@deepseek-ai/cordis'

import {
  activateSystem,
  createApiKey,
  defaultWorkerEmail,
  listSystemDomains,
  preflightActivationCode,
  readAgentmailPointer,
  readSystemGatewayConfig,
  registerPullAddress,
  removeRawSystemKey,
  whoami,
  writeAgentmailPointer,
  writeRawSystemKey,
  writeSystemGatewayConfig,
  ProvisionError,
  type SystemGatewayConfig,
} from '../core/provision.js'
import { listAgentConfigs, loadAgentConfig, listSystemDirs, saveAgentConfig } from '../core/index.js'
import { defaultWorkspace, ensureWorkspace } from './workspace.js'

export interface Config {
  gatewayUrl?: string
}

export interface Args {
  action?: string
  code?: string
  gateway_url?: string
  system_name?: string
  domain?: string
  manager?: string
  /** Independent gateway (scenario 5): existing system-level key. */
  system_key?: string
  /** Independent gateway: system id printed by the gateway at start. */
  system_id?: string
}

function failure(errorCode: string, error: string, retryable = false): Record<string, unknown> {
  return { success: false, error_code: errorCode, retryable, hint: error }
}

export async function findExistingSystem(): Promise<{ sid: string; cfg: SystemGatewayConfig } | undefined> {
  const ptr = await readAgentmailPointer()
  if (ptr?.system_id) {
    const cfg = await readSystemGatewayConfig(ptr.system_id)
    if (cfg) return { sid: ptr.system_id, cfg }
  }
  const sids = await listSystemDirs()
  for (const sid of sids) {
    const cfg = await readSystemGatewayConfig(sid)
    if (cfg?.admin_key) return { sid, cfg }
  }
  return undefined
}

export async function systemStatus(): Promise<Record<string, unknown>> {
  const existing = await findExistingSystem()
  if (!existing) return { success: true, scope: 'system', provisioned: false }
  const { sid, cfg } = existing
  const defaultEmail = cfg.domain ? defaultWorkerEmail(cfg.domain, cfg.system_name) : ''
  const binding = defaultEmail ? await loadAgentConfig(sid, defaultEmail) : undefined
  return {
    success: true,
    scope: 'system',
    provisioned: true,
    system_id: sid,
    domain: cfg.domain,
    system_name: cfg.system_name,
    domains: cfg.domains ?? (cfg.domain ? [cfg.domain] : []),
    default_email: defaultEmail,
    workspace: binding?.workspace_path ? { path: binding.workspace_path, title: binding.workspace_title } : undefined,
  }
}

async function provisionWithSystemKey(
  ctx: Context,
  args: Args,
  gatewayUrl: string,
  systemKey: string,
  existing: { sid: string; cfg: SystemGatewayConfig } | undefined,
): Promise<Record<string, unknown>> {
  const requestedSid = String(args.system_id ?? '').trim()
  const domain = String(args.domain ?? existing?.cfg.domain ?? '').trim().toLowerCase()
  const systemName = String(args.system_name ?? '').trim()
  const manager = String(args.manager ?? '').trim()
  if (!requestedSid) {
    return failure('bad_args', 'system_id is required with system_key (printed by the gateway at start, e.g. system-xxxx)')
  }
  if (!domain) return failure('bad_args', 'domain is required with system_key')
  if (existing && existing.cfg.gateway_url.replace(/\/+$/, '') !== gatewayUrl) {
    return failure('gateway_mismatch', `already provisioned for gateway ${existing.cfg.gateway_url}`)
  }
  let who
  try {
    who = await whoami(gatewayUrl, systemKey, requestedSid)
  } catch (e) {
    return failure('system_key_invalid', e instanceof Error ? e.message : String(e))
  }
  if (who.status !== 200 || who.system_id !== requestedSid) {
    return failure('system_key_invalid', 'system_key does not authenticate for this system_id')
  }
  if (who.email) {
    return failure('system_key_invalid', 'this is an address-scoped key; a system-level key is required for system onboarding')
  }
  const sid = who.system_id ?? requestedSid
  const initial: SystemGatewayConfig = {
    gateway_url: gatewayUrl,
    admin_key: systemKey,
    system_id: sid,
    ...(systemName ? { system_name: systemName } : {}),
    domain,
    ...(manager || who.manager_address ? { manager_address: manager || who.manager_address } : {}),
    save_raw_snapshots: true,
    system_home: (process.env.DSH_HOME || path.join(process.env.HOME || '', '.dsh')).replace(/\/+$/, ''),
    domains: [domain],
  }
  await writeSystemGatewayConfig(sid, initial)
  const domainKeys: Record<string, string> = {}
  try {
    domainKeys[domain] = await createApiKey({ gatewayUrl, adminKey: systemKey, identity: sid, systemId: sid, email: domain, scopes: ['system'], category: 'domain' })
  } catch {
    /* degraded: use the supplied system key */
  }
  const selectedKey = domainKeys[domain] || systemKey
  await writeSystemGatewayConfig(sid, { ...initial, admin_key: selectedKey, ...(Object.keys(domainKeys).length ? { domain_keys: domainKeys } : {}) })
  await writeAgentmailPointer(sid)

  let defaultEmail = ''
  let workspace: { id: string; path: string; title?: string } | undefined
  defaultEmail = defaultWorkerEmail(domain, systemName)
  const ws = await defaultWorkspace(ctx)
  if (!ws) return failure('workspace_invalid', 'DSH default workspace not found — create/open the default workspace first')
  workspace = await ensureWorkspace(ctx, ws.path)
  const existingBinding = await loadAgentConfig(sid, defaultEmail)
  if (!existingBinding) {
    try {
      const agentKey = await registerPullAddress({
        gatewayUrl,
        adminKey: selectedKey,
        identity: sid,
        systemId: sid,
        email: defaultEmail,
        ...(initial.manager_address ? { manager: initial.manager_address } : {}),
      })
      await saveAgentConfig({
        email: defaultEmail,
        gateway_url: gatewayUrl,
        system_id: sid,
        domain,
        ...(systemName ? { system_name: systemName } : {}),
        api_key: agentKey,
        workspace_id: workspace.id,
        workspace_path: workspace.path,
        ...(workspace.title ? { workspace_title: workspace.title } : {}),
        paused: false,
      } as never, sid)
    } catch (e) {
      return { success: true, scope: 'system', system_id: sid, domain, default_email: defaultEmail, workspace, kind: 'key', degraded: true, reason: e instanceof Error ? e.message : String(e) }
    }
  }
  return { success: true, scope: 'system', system_id: sid, domain, system_name: systemName, manager_address: initial.manager_address, domains: [domain], default_email: defaultEmail, workspace, kind: 'key', degraded: selectedKey === systemKey }
}

export async function provisionSystem(
  ctx: Context,
  args: Args,
): Promise<Record<string, unknown>> {
  const action = String(args.action ?? 'bind').toLowerCase()
  if (action === 'status') return await systemStatus()
  if (action !== 'bind') return failure('bad_args', 'action must be bind or status')

  const code = String(args.code ?? '').trim()
  const gatewayUrl = String(args.gateway_url ?? process.env.AIMAIL_GW_URL ?? 'https://aimail.token.tm').replace(/\/+$/, '')
  const systemName = String(args.system_name ?? '').trim()
  const domain = String(args.domain ?? '').trim().toLowerCase()
  const manager = String(args.manager ?? '').trim()

  const systemKey = String(args.system_key ?? '').trim()
  if (systemName && domain) {
    return failure('bad_args', 'provide exactly one of system_name (shared domain) or domain (dedicated), not both')
  }
  const existing = await findExistingSystem()
  if (existing && !code && !systemKey) {
    return { ...(await systemStatus()), reason: 'already_provisioned' }
  }
  if (!!code === !!systemKey) {
    return failure('bad_args', 'provide exactly one of code (cloud activation) or system_key (independent gateway)')
  }
  if (systemKey && systemName) {
    return failure('bad_args', 'independent gateway uses domain, not system_name')
  }
  if (systemKey) {
    return await provisionWithSystemKey(ctx, args, gatewayUrl, systemKey, existing)
  }

  // A profile runs either system mode or shared-address mode, never both.
  if (!existing) {
    for (const sid of await listSystemDirs()) {
      if ((await listAgentConfigs(sid)).length > 0) {
        return failure('binding_conflict', 'this profile already has address bindings but no system; remove them or use a separate profile (no unbind tool)')
      }
    }
  }

  let pre
  try {
    pre = await preflightActivationCode(gatewayUrl, code)
  } catch (e) {
    if (e instanceof ProvisionError) return failure(e.errorCode, e.message, e.retryable)
    throw e
  }
  if (!pre.valid) return failure('activation_invalid', pre.error ?? 'activation code is not valid')
  if (pre.claimed) return failure('activation_invalid', 'activation code already claimed')
  if (pre.code_type !== 'product' && pre.code_type !== 'system') {
    return failure('wrong_tool', 'this is an address activation code — use aimail_address bind')
  }
  if (existing && existing.cfg.gateway_url.replace(/\/+$/, '') !== gatewayUrl) {
    return failure('gateway_mismatch', `already provisioned for gateway ${existing.cfg.gateway_url}`)
  }
  const boundDomain = domain || pre.domain || ''
  const boundName = systemName || pre.system_name || ''
  if (pre.domain && domain && pre.domain.toLowerCase() !== domain) {
    return failure('system_mismatch', `activation code is bound to domain ${pre.domain}`)
  }
  if (pre.system_name && systemName && pre.system_name !== systemName) {
    return failure('system_mismatch', `activation code is bound to system ${pre.system_name}`)
  }
  if (pre.system_name && domain) {
    return failure('system_mismatch', `activation code is bound to shared system ${pre.system_name}; omit domain`)
  }
  if (pre.domain && systemName) {
    return failure('system_mismatch', `activation code is bound to dedicated domain ${pre.domain}; omit system_name`)
  }
  if (!boundDomain && !boundName) {
    return failure('bad_args', 'provide exactly one of domain or system_name when the code is not bound')
  }

  let activated
  try {
    activated = await activateSystem({ gatewayUrl, code, ...(boundName ? { systemName: boundName } : {}), ...(boundDomain ? { domain: boundDomain } : {}), ...(manager ? { manager } : {}) })
  } catch (e) {
    if (e instanceof ProvisionError) return failure(e.errorCode, e.message, e.retryable)
    throw e
  }
  const sid = activated.system_id
  const rawKey = activated.raw_key
  const selectedDomain = (activated.domain || boundDomain || '').toLowerCase()

  // Raw key first (rollback safety), then per-domain keys.
  const initial: SystemGatewayConfig = {
    gateway_url: gatewayUrl,
    admin_key: rawKey,
    system_id: sid,
    ...(activated.system_name || boundName ? { system_name: activated.system_name || boundName } : {}),
    ...(selectedDomain ? { domain: selectedDomain } : {}),
    ...(activated.manager_address ? { manager_address: activated.manager_address } : {}),
    save_raw_snapshots: true,
    system_home: (process.env.DSH_HOME || path.join(process.env.HOME || '', '.dsh')).replace(/\/+$/, ''),
    ...(activated.domains.length ? { domains: activated.domains } : {}),
  }
  await writeRawSystemKey(sid, rawKey)
  await writeSystemGatewayConfig(sid, initial)

  const domains = (activated.domains.length ? activated.domains : await listSystemDomains({ gatewayUrl, adminKey: rawKey, identity: sid, systemId: sid })).filter((d) => d.length > 0 && !d.includes('@'))
  const domainKeys: Record<string, string> = {}
  for (const d of domains) {
    try {
      domainKeys[d] = await createApiKey({ gatewayUrl, adminKey: rawKey, identity: sid, systemId: sid, email: d, scopes: ['system'], category: 'domain' })
    } catch {
      /* degraded: keep raw key */
    }
  }
  const selectedKey = (selectedDomain && domainKeys[selectedDomain]) || rawKey
  const finalCfg: SystemGatewayConfig = {
    ...initial,
    admin_key: selectedKey,
    ...(domains.length ? { domains } : {}),
    ...(Object.keys(domainKeys).length ? { domain_keys: domainKeys } : {}),
  }
  await writeSystemGatewayConfig(sid, finalCfg)
  if (selectedKey !== rawKey) await removeRawSystemKey(sid)
  await writeAgentmailPointer(sid)

  // Default worker → DSH default workspace. No current-workspace fallback:
  // system onboarding is a system-level operation, not an address bind.
  let defaultEmail = ''
  let workspace: { id: string; path: string; title?: string } | undefined
  if (selectedDomain) {
    defaultEmail = defaultWorkerEmail(selectedDomain, finalCfg.system_name)
    const ws = await defaultWorkspace(ctx)
    if (!ws) return failure('workspace_invalid', 'DSH default workspace not found — create/open the default workspace first')
    workspace = await ensureWorkspace(ctx, ws.path)
    const existingBinding = await loadAgentConfig(sid, defaultEmail)
    if (!existingBinding) {
      try {
        const agentKey = await registerPullAddress({
          gatewayUrl,
          adminKey: selectedKey,
          identity: sid,
          systemId: sid,
          email: defaultEmail,
          ...(finalCfg.manager_address ? { manager: finalCfg.manager_address } : {}),
        })
        await saveAgentConfig({
          email: defaultEmail,
          gateway_url: gatewayUrl,
          system_id: sid,
          domain: selectedDomain,
          api_key: agentKey,
          ...(finalCfg.system_name ? { system_name: finalCfg.system_name } : {}),
          workspace_id: workspace.id,
          workspace_path: workspace.path,
          ...(workspace.title ? { workspace_title: workspace.title } : {}),
          paused: false,
        } as never, sid)
      } catch (e) {
        return { success: true, scope: 'system', system_id: sid, domain: selectedDomain, manager_address: finalCfg.manager_address, domains, workspace, default_email: defaultEmail, degraded: true, reason: e instanceof Error ? e.message : String(e) }
      }
    }
  }

  return {
    success: true,
    scope: 'system',
    system_id: sid,
    domain: selectedDomain,
    system_name: finalCfg.system_name,
    manager_address: finalCfg.manager_address,
    domains,
    default_email: defaultEmail,
    workspace,
    kind: activated.kind,
    degraded: selectedKey === rawKey && Object.keys(domainKeys).length === 0,
  }
}

