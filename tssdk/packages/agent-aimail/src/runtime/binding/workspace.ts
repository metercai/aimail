/**
 * Persist the activation-time workspace onto an existing address binding.
 *
 * Non-DSH hosts expose a current workspace (Pi cwd / OpenClaw agent workspace)
 * but the generic address tool has no host context. The adapter calls this
 * after a successful bind so a restart cannot route the mailbox elsewhere.
 */
import { findBindingByEmail, loadAgentConfig, saveAgentConfig } from '../../core/index.js'
import { BINDING_FILE } from '../../core/contract.js'
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

/** Pi single-address guard: find another active address (if any). */
export async function findOtherActiveAddress(email: string): Promise<string | undefined> {
  const { promises: fsp } = await import('node:fs')
  const path = await import('node:path')
  const { AIMAIL_HOME } = await import('../../core/index.js')
  const base = path.join(AIMAIL_HOME(), 'systems')
  let roots: string[] = []
  try { roots = await fsp.readdir(base) } catch { return undefined }
  const want = email.toLowerCase()
  for (const root of roots) {
    let entries: import('node:fs').Dirent[] = []
    try { entries = await fsp.readdir(path.join(base, root), { withFileTypes: true }) } catch { continue }
    for (const e of entries) {
      if (!e.isDirectory()) continue
      try {
        const cfg = JSON.parse(await fsp.readFile(path.join(base, root, e.name, BINDING_FILE), 'utf-8')) as Record<string, unknown>
        const em = String(cfg.email ?? '').toLowerCase()
        if (em && em !== want && cfg.api_key && cfg.paused !== true) return em
      } catch { /* skip */ }
    }
  }
  return undefined
}
