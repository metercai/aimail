/**
 * system-config — thin read helpers over `~/.aimail/systems/{sid}/`.
 *
 * This file used to be `auto-bind.ts`: the SDK-side "register an address when the machine already has
 * a system config" chain (`registerAddress` → `saveBinding` → `autoBind`, plus the webhook tri-state
 * and the webhook-secret repair). All of it was v1 push/CLI surface and is gone (2026-11-25, owner
 * ruling):
 *
 *   - a binding is created ONLY by an activation code (`aimail_address` → `activateAddressCode` →
 *     `activateAddressCodePersist`), so nothing in the SDK derives an address or registers one;
 *   - there is no local endpoint and therefore no `webhook_url` to resolve, patch or repair;
 *   - address naming (`--name` planning) was a CLI concern, not a runtime one.
 *
 * What remains is what the SSE-V2 pull path actually reads: the system list and the system-level
 * gateway facts.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'

import { AIMAIL_HOME, gatewayConfigPath } from './config.js'

/** ~/.aimail/systems/{sid}/aimail_gateway.json (system-level facts). */
export interface SystemGatewayConfig {
  system_id?: string
  gateway_url?: string
  api_key?: string
  domain?: string
  system_name?: string
  manager_address?: string
  /** Residual v1 keys on disk are ignored; this interface keeps them readable, never written. */
  [k: string]: unknown
}

/** Every system directory under `~/.aimail/systems` (sorted; empty when the dir is absent). */
export async function listSystemDirs(): Promise<string[]> {
  const root = path.join(AIMAIL_HOME(), 'systems')
  try {
    const entries = await fs.readdir(root, { withFileTypes: true })
    return entries
      .filter((e) => e.isDirectory())
      .map((e) => e.name)
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
      `gateway config not found (aimail_gateway.json) for system ${systemId} — a system is created by ` +
        'consuming an activation code (aimail_address), it is never derived locally',
    )
  }
}
