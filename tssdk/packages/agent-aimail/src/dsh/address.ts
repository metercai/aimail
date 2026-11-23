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
import { promises as fs } from 'node:fs'

import {
  activateAddressCode,
  preflightActivationCode,
  ProvisionError,
} from '../core/provision.js'
import { aimailAddress, loadAgentConfig, listSystemDirs, saveAgentConfig, updateAgentConfig } from '../core/index.js'
import type { AgentConfig } from '../core/index.js'
import { currentWorkspacePath, ensureWorkspace } from './workspace.js'
import { getAgentSystemId } from '../core/agent-system-id.js'
import { findExistingSystem } from './system-core.js'

interface Args {
  action?: string
  email?: string
  code?: string
  gateway_url?: string
  manager?: string
  workspace_path?: string
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
  if (!['bind', 'pause', 'resume', 'unbind', 'move'].includes(action)) {
    return fail('bad_args', 'action must be bind|pause|resume|unbind|move')
  }

  const found = await findBinding(email)
  if (action === 'unbind') {
    if (!found) return fail('not-bound', `no binding for ${email}`)
    const r = await aimailAddress({ action: 'unbind', email })
    // §13.2: re-arm with the remaining addresses (or stop the stream when
    // this was the only one) — no host restart required.
    const hub = ctx.get('mailHub') as HubService | undefined
    void hub?.reconcile?.()
    return r as unknown as Record<string, unknown>
  }
  if (action === 'move') {
    if (!found) return fail('not-bound', `no binding for ${email}`)
    const target = String(args.workspace_path ?? '').trim()
    if (!target) return fail('bad_args', 'workspace_path is required for move')
    try {
      if (!(await fs.stat(target)).isDirectory()) return fail('workspace_invalid', `workspace is not a directory: ${target}`)
    } catch {
      return fail('workspace_invalid', `workspace does not exist: ${target}`)
    }
    await updateAgentConfig(found.sid, email, { workspace_id: target, workspace_path: target })
    // §13.3: same (AgentSystemId, Gateway) — no new code, no new SSE.
    return { success: true, scope: 'address', email, system_id: found.sid, workspace: workspaceRef(target, target), mode: 'move' }
  }
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

  // V2: the addres code is the ONLY activation prerequisite. There is no
  // local system/domain/key registration path (§3.1/§14): a system config may
  // only supply the gateway URL, never an admin/domain key.
  if (!code) {
    if (found) {
      // No code + existing binding → only update the workspace (link).
      await updateAgentConfig(found.sid, email, {
        workspace_id: workspace.id,
        workspace_path: workspace.path,
        ...(workspace.title ? { workspace_title: workspace.title } : {}),
      })
      const hub = ctx.get('mailHub') as HubService | undefined
      void hub?.reconcile?.()
      return { success: true, scope: 'address', email, system_id: found.sid, workspace, mode: 'link', paused: Boolean(found.cfg.paused) }
    }
    return fail(
      'code_required',
      'an address activation code is required to bind this address; system/domain keys are not an activation path',
    )
  }

  const gw = gatewayUrlArg || String(system?.cfg.gateway_url ?? '').replace(/\/+$/, '')
  if (!gw) return fail('bad_args', 'gateway_url is required to redeem an address activation code')
  let pre
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

  let act
  try {
    act = await activateAddressCode({ gatewayUrl: gw, code, email, agentSystemId: await getAgentSystemId() })
  } catch (e) {
    if (e instanceof ProvisionError) return fail(e.errorCode, e.message, e.retryable)
    throw e
  }

  // Re-activation/migration: if the address already lives under another
  // GatewaySystemId, archive the old binding only AFTER the new code was
  // accepted. Mail data stays where it is; the directory grouping changes.
  if (found && found.sid !== act.system_id) {
    await aimailAddress({ action: 'unbind', email })
  }

  const next: AgentConfig = {
    ...(found && found.sid === act.system_id ? found.cfg : {}),
    email,
    gateway_url: gw,
    system_id: act.system_id,
    domain: domainOf(email),
    api_key: act.raw_key,
    agent_system_id: await getAgentSystemId(),
    workspace_id: workspace.id,
    workspace_path: workspace.path,
    ...(workspace.title ? { workspace_title: workspace.title } : {}),
    paused: false,
    ...(act.manager_address ? { manager_address: act.manager_address } : {}),
    ...(act.expires_at ? { expires_at: act.expires_at } : {}),
  } as AgentConfig
  await saveAgentConfig(next, act.system_id)
  const hub = ctx.get('mailHub') as HubService | undefined
  void hub?.reconcile?.()
  return { success: true, scope: 'address', email, system_id: act.system_id, workspace, mode: 'code', kind: act.kind, paused: false, ...(act.expires_at ? { expires_at: act.expires_at } : {}) }
}
