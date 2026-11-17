/**
 * Persist the activation-time workspace onto an existing address binding.
 *
 * Non-DSH hosts expose a current workspace (Pi cwd / OpenClaw agent workspace)
 * but the generic address tool has no host context. The adapter calls this
 * after a successful bind so a restart cannot route the mailbox elsewhere.
 */
import { findBindingByEmail, loadAgentConfig, saveAgentConfig } from '../../core/index.js'
import type { WorkspaceRef } from '../types.js'

export async function pinWorkspaceToBinding(email: string, workspace: WorkspaceRef): Promise<boolean> {
  const found = await findBindingByEmail(email)
  if (!found.active || !found.active.system_id) return false
  const cfg = await loadAgentConfig(found.active.system_id, email)
  if (!cfg) return false
  cfg.workspace_id = workspace.id
  cfg.workspace_path = workspace.path
  if (workspace.title) cfg.workspace_title = workspace.title
  await saveAgentConfig(cfg, cfg.system_id)
  return true
}
