/**
 * Legacy pointer lookup used by the DSH address tool.
 *
 * V2 removed the host-facing `aimail_system` tool and the whole system
 * onboarding path. The address tool only needs the gateway URL of a previously
 * provisioned system, so this module keeps that read-only lookup and nothing
 * else (no code redemption, key minting, or workspace binding).
 */
import {
  readAgentmailPointer,
  readSystemGatewayConfig,
  type SystemGatewayConfig,
} from '../core/provision.js'
import { listSystemDirs } from '../core/index.js'

export async function findExistingSystem(): Promise<
  { sid: string; cfg: SystemGatewayConfig } | undefined
> {
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
