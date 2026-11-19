/**
 * dsh address binding (`aimail_address`) — workspace-scoped, no manager.
 *
 * Two cases:
 *   B1: a system is onboarded → a local-domain address (code optional; the
 *       system/domain key registers it in pull mode).
 *   B2: no system → a shared-domain address with an address code (advanced
 *       gateways only).
 *
 * Lifecycle is bind/pause/resume only; there is no unbind. A new valid code
 * re-binds with the gateway's renewal/rotation semantics.
 */
import type { Context } from '@deepseek-ai/cordis'

import {
  activateAddressCode,
  preflightActivationCode,
  registerPullAddress,
  ProvisionError,
  type SystemGatewayConfig,
} from '../core/provision.js'
import { loadAgentConfig, listSystemDirs, saveAgentConfig, updateAgentConfig } from '../core/index.js'
import type { AgentConfig } from '../core/index.js'
import { currentWorkspacePath, ensureWorkspace } from './workspace.js'
import { findExistingSystem } from './system-core.js'

interface Args {
  action?: string
  email?: string
  code?: string
  gateway_url?: string
  manager?: string
}

interface HubService {
  reconcile?(): void | Promise<void>
  resume?(email: string): void | Promise<void>
}

function fail(errorCode: string, error: string, retryable = false): Record<string, unknown> {
  return { success: false, error_code: errorCode, retryable, hint: error }
}

export async function findBinding(email: string): Promise<{ sid: string; cfg: AgentConfig } | undefined> {
  const target = email.trim().toLowerCase()
  for (const sid of await listSystemDirs()) {
    const cfg = await loadAgentConfig(sid, target)
    if (cfg) return { sid, cfg }
  }
  return undefined
}

function domainOf(email: string): string {
  return email.includes('@') ? (email.split('@').pop() ?? '').toLowerCase() : ''
}

function domainKeyFor(cfg: SystemGatewayConfig, domain: string): string {
  return cfg.domain_keys?.[domain] ?? (String(cfg.domain ?? '').toLowerCase() === domain ? cfg.admin_key : '')
}

function workspaceRef(path: string, id: string, title?: string): { id: string; path: string; title?: string } {
  return { id, path, ...(title ? { title } : {}) }
}

export async function runAddressTool(
  ctx: Context,
  exec: { agent?: { id?: string } | null },
  rawArgs: unknown,
): Promise<Record<string, unknown>> {
  const args = (rawArgs ?? {}) as Args
  const action = String(args.action ?? 'bind').toLowerCase()
  const email = String(args.email ?? '').trim().toLowerCase()
  if (!email || !email.includes('@')) return fail('bad_args', 'email is required')
  if (args.manager) return fail('bad_args', 'address binding does not accept manager')
  if (!['bind', 'pause', 'resume'].includes(action)) {
    return fail('bad_args', 'action must be bind|pause|resume (unbind is not supported)')
  }

  const found = await findBinding(email)
  if (action === 'pause' || action === 'resume') {
    if (!found) return fail('not-bound', `no binding for ${email}`)
    const paused = action === 'pause'
    await updateAgentConfig(found.sid, email, { paused })
    const hub = ctx.get('mailHub') as HubService | undefined
    if (paused) void hub?.reconcile?.()
    else void hub?.resume?.(email)
    return { success: true, scope: 'address', email, system_id: found.sid, workspace: found.cfg.workspace_path ? workspaceRef(found.cfg.workspace_path, found.cfg.workspace_id ?? '', found.cfg.workspace_title) : undefined, paused, mode: paused ? 'pause' : 'resume' }
  }

  const sessionId = String(exec.agent?.id ?? '')
  if (!sessionId) return fail('workspace_invalid', 'no session id — cannot resolve the current workspace')
  const workspacePath = await currentWorkspacePath(ctx, sessionId).catch((e: unknown) => {
    throw e
  })
  const workspace = await ensureWorkspace(ctx, workspacePath)
  const code = String(args.code ?? '').trim()
  const system = await findExistingSystem()
  const gatewayUrlArg = String(args.gateway_url ?? '').replace(/\/+$/, '')

  let pre
  if (code) {
    const gw = system?.cfg.gateway_url ?? gatewayUrlArg
    if (!gw) return fail('bad_args', 'gateway_url is required when no system is provisioned')
    try {
      pre = await preflightActivationCode(gw, code)
    } catch (e) {
      if (e instanceof ProvisionError) return fail(e.errorCode, e.message, e.retryable)
      throw e
    }
    if (!pre.valid || pre.claimed) return fail('activation_invalid', pre.error ?? 'activation code is invalid or already claimed')
    if (pre.code_type !== 'address') return fail('wrong_tool', 'this is not an address activation code')
    if (pre.email_address && pre.email_address.toLowerCase() !== email) {
      return fail('activation_invalid', `activation code is bound to ${pre.email_address}`)
    }
  }

  if (system) {
    // B1 — local-domain address.
    if (gatewayUrlArg && gatewayUrlArg !== system.cfg.gateway_url.replace(/\/+$/, '')) {
      return fail('gateway_mismatch', `gateway is ${system.cfg.gateway_url}`)
    }
    const domain = domainOf(email)
    if (pre?.system_id && pre.system_id !== system.sid) {
      return fail('system_mismatch', `activation code belongs to system ${pre.system_id}`)
    }
    if (pre?.domain && pre.domain.toLowerCase() !== domain) {
      return fail('system_mismatch', `activation code is bound to domain ${pre.domain}`)
    }
    if (pre?.scope && pre.scope === 'shared') {
      return fail('system_mismatch', 'shared-domain address cannot be bound inside an onboarded system')
    }
    if (system.cfg.system_name) {
      const suffix = `.${system.cfg.system_name}@${String(system.cfg.domain ?? '').toLowerCase()}`
      if (!email.endsWith(suffix)) return fail('system_mismatch', `address must be <name>${suffix}`)
    } else {
      const known = new Set<string>([
        String(system.cfg.domain ?? '').toLowerCase(),
        ...(system.cfg.domains ?? []).map((d) => d.toLowerCase()),
        ...Object.keys(system.cfg.domain_keys ?? {}).map((d) => d.toLowerCase()),
      ].filter(Boolean))
      if (!known.has(domain)) return fail('system_mismatch', `address domain must be one of ${[...known].join(', ')}`)
    }

    let key = ''
    let kind = 'link'
    let expiresAt: string | undefined
    if (code) {
      try {
        const act = await activateAddressCode({ gatewayUrl: system.cfg.gateway_url, code, email })
        key = act.raw_key
        kind = act.kind
        expiresAt = act.expires_at
      } catch (e) {
        if (e instanceof ProvisionError) return fail(e.errorCode, e.message, e.retryable)
        throw e
      }
    } else if (!found) {
      const domainKey = domainKeyFor(system.cfg, domain)
      if (!domainKey) return fail('domain_key_missing', `no domain key for ${domain}; re-run system onboarding with a new code`)
      try {
        key = await registerPullAddress({ gatewayUrl: system.cfg.gateway_url, adminKey: domainKey, identity: system.sid, systemId: system.sid, email })
        kind = 'register'
      } catch (e) {
        if (e instanceof ProvisionError) return fail(e.errorCode, e.message, e.retryable)
        throw e
      }
    } else {
      // No code + existing binding → only update the workspace (link).
      await updateAgentConfig(system.sid, email, {
        workspace_id: workspace.id,
        workspace_path: workspace.path,
        ...(workspace.title ? { workspace_title: workspace.title } : {}),
      })
      const hub = ctx.get('mailHub') as HubService | undefined
      void hub?.reconcile?.()
      return { success: true, scope: 'address', email, system_id: system.sid, workspace, mode: 'link', paused: Boolean(found.cfg.paused) }
    }

    const next: AgentConfig = {
      ...(found?.cfg ?? {}),
      email,
      gateway_url: system.cfg.gateway_url,
      system_id: system.sid,
      domain,
      api_key: key,
      ...(system.cfg.system_name ? { system_name: system.cfg.system_name } : {}),
      workspace_id: workspace.id,
      workspace_path: workspace.path,
      ...(workspace.title ? { workspace_title: workspace.title } : {}),
      paused: false,
      ...(expiresAt ? { expires_at: expiresAt } : {}),
    } as AgentConfig
    await saveAgentConfig(next, system.sid)
    const hub = ctx.get('mailHub') as HubService | undefined
    void hub?.reconcile?.()
    return { success: true, scope: 'address', email, system_id: system.sid, workspace, mode: code ? 'code' : kind, kind, paused: false, ...(expiresAt ? { expires_at: expiresAt } : {}) }
  }

  // B2 — no system; shared-domain address code only.
  if (!code) return fail('needs_system', 'no system onboarded; a shared-domain address requires an address activation code')
  if (!pre || pre.scope !== 'shared') {
    return fail('needs_system', 'this address is not a shared-domain address; onboard its system first')
  }
  if (!gatewayUrlArg) return fail('bad_args', 'gateway_url is required for a shared-domain address')
  let act
  try {
    act = await activateAddressCode({ gatewayUrl: gatewayUrlArg, code, email })
  } catch (e) {
    if (e instanceof ProvisionError) return fail(e.errorCode, e.message, e.retryable)
    throw e
  }
  const next: AgentConfig = {
    email,
    gateway_url: gatewayUrlArg,
    system_id: act.system_id,
    domain: domainOf(email),
    api_key: act.raw_key,
    workspace_id: workspace.id,
    workspace_path: workspace.path,
    ...(workspace.title ? { workspace_title: workspace.title } : {}),
    paused: false,
    ...(act.expires_at ? { expires_at: act.expires_at } : {}),
  } as AgentConfig
  await saveAgentConfig(next, act.system_id)
  const hub = ctx.get('mailHub') as HubService | undefined
  void hub?.reconcile?.()
  return { success: true, scope: 'address', email, system_id: act.system_id, workspace, mode: 'code', kind: act.kind, paused: false, ...(act.expires_at ? { expires_at: act.expires_at } : {}) }
}
