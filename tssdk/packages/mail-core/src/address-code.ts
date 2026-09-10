/**
 * Address-code consumption chain (open-application plan §2.4/§2.5, slice 7).
 *
 * Type-3 addresses (`{agentname}@{shared_domain}`) are consumed by the
 * AGENT itself — no CLI involvement:
 *
 *   apply-address (web) → activation code arrives by email
 *     → `activateAddressCode` (public endpoint; the code IS the credential)
 *     → `activateAddressCodePersist` (same + register-isomorphic落盘)
 *     → `startPolling` pulls its own mail (agent-scope key)
 *
 * The persist step writes the same files the register path produces
 * (auto-bind.ts saveBinding: email/gateway_url/domain/system_id/api_key
 * + system_name/manager_address when known), so every consumer
 * (tools, preprocess, pull) works with zero changes.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import {
  type AgentConfig,
} from './types.js'
import {
  gatewayConfigPath,
  saveAgentConfig,
} from './config.js'
import type { GatewayResponse } from './types.js'

/** Minimal client surface these helpers need (GatewayClient satisfies it). */
export interface RequestClient {
  request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
  ): Promise<GatewayResponse>
}

export interface ActivateAddressCodeResult {
  success: boolean
  raw_key?: string | undefined
  system_id?: string | undefined
  email_address?: string | undefined
  expires_at?: string | undefined
  error?: string | undefined
  status?: number | undefined
}

/**
 * Activate a shared_addr address code. The email is lowercased before
 * sending: the gateway stores the key's address lowercased and agent-scope
 * pull matches on exact-email SQL — a case mismatch would silently orphan
 * the agent's mail.
 */
export async function activateAddressCode(
  client: RequestClient,
  code: string,
  emailAddress: string,
): Promise<ActivateAddressCodeResult> {
  const res = await client.request('POST', '/api/v1/activate-address-code', {
    code,
    email_address: emailAddress.trim().toLowerCase(),
  })
  const rawKey = res.raw_key as string | undefined
  if (!rawKey) {
    return {
      success: false,
      error: String(res.error ?? 'activation failed'),
      status: typeof res.status === 'number' ? res.status : undefined,
    }
  }
  return {
    success: true,
    raw_key: rawKey,
    system_id: res.system_id as string | undefined,
    email_address: res.email_address as string | undefined,
    expires_at: res.expires_at as string | undefined,
  }
}

export interface ActivateAddressCodePersistOptions {
  /** Gateway base URL recorded in the binding (the gateway the agent uses). */
  gatewayUrl: string
  /**
   * Agent id recorded in the binding. Defaults to the address local-part
   * (the mailbox IS the agent — the address flow has no separate agent
   * concept).
   */
  agentId?: string
  /** Platform home dir; when set, the `.agentmail` discovery pointer is written there. */
  platformHome?: string
}

export interface ActivateAddressCodePersistResult extends ActivateAddressCodeResult {
  config_path?: string | undefined
  pointer_written?: boolean | undefined
}

/**
 * Activate AND persist — the whole consumption chain in one call.
 *
 * Writes:
 * 1. `~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json` — fields
 *    isomorphic to the register path (saveBinding), plus `expires_at`
 *    so the agent can surface its own renewal window.
 * 2. `~/.aimail/systems/{sid}/aimail_gateway.json` — create-if-absent
 *    only (never clobbers an existing system's admin_key), 0600.
 * 3. `{platformHome}/.agentmail` pointer (best-effort) when given —
 *    discovery/log naming read it.
 */
export async function activateAddressCodePersist(
  client: RequestClient,
  code: string,
  emailAddress: string,
  opts: ActivateAddressCodePersistOptions,
): Promise<ActivateAddressCodePersistResult> {
  const act = await activateAddressCode(client, code, emailAddress)
  if (!act.success || !act.raw_key || !act.system_id || !act.email_address) {
    return act
  }
  const sid = act.system_id
  const email = act.email_address
  const domain = email.includes('@') ? email.split('@').pop()! : ''
  const agentId = opts.agentId || email.split('@')[0] || 'agent'

  // 1. Address-keyed agentmail.json (register-isomorphic + expires_at).
  const cfg: AgentConfig = {
    email,
    gateway_url: opts.gatewayUrl,
    domain,
    system_id: sid,
    api_key: act.raw_key,
    agent_id: agentId,
  }
  if (act.expires_at) cfg.expires_at = act.expires_at
  const p = await saveAgentConfig(cfg, sid)

  // 2. Gateway connection file — create-if-absent only.
  const gwPath = gatewayConfigPath(sid)
  try {
    await fs.access(gwPath)
  } catch {
    await fs.mkdir(path.dirname(gwPath), { recursive: true })
    await fs.writeFile(
      gwPath,
      JSON.stringify(
        {
          gateway_url: opts.gatewayUrl,
          admin_key: act.raw_key,
          system_id: sid,
          domain,
        },
        null,
        2,
      ) + '\n',
      { mode: 0o600 },
    )
  }

  // 3. Discovery pointer (best-effort).
  let pointerWritten = false
  if (opts.platformHome) {
    try {
      const ptrPath = path.join(opts.platformHome, '.agentmail')
      await fs.mkdir(path.dirname(ptrPath), { recursive: true })
      await fs.writeFile(
        ptrPath,
        JSON.stringify({ system_id: sid, email }, null, 2) + '\n',
      )
      pointerWritten = true
    } catch {
      // best-effort: discovery degrades, the binding itself is intact
    }
  }

  return {
    success: true,
    raw_key: act.raw_key,
    system_id: sid,
    email_address: email,
    expires_at: act.expires_at,
    config_path: p,
    pointer_written: pointerWritten,
  }
}

export interface PullDelivery {
  id: number
  email: string
  headers: unknown
}

export interface PullBatch {
  body: unknown
  deliveries: PullDelivery[]
}

/**
 * Agent-scope pull over the shared `/api/v1/admin/pending` endpoint. With an
 * agent-scope key the gateway's interception layer serves ONLY this key's own
 * address — the `emails` field exists for bridge shape compatibility and is
 * ignored server-side (scope IS the range).
 */
export async function pullList(
  client: RequestClient,
  limit = 20,
): Promise<{ success: boolean; batches: PullBatch[]; error?: string }> {
  const res = await client.request('POST', '/api/v1/admin/pending', {
    limit: Math.max(1, Math.min(limit, 200)),
  })
  if (res.status !== 200) {
    return { success: false, batches: [], error: String(res.error ?? 'pull list failed') }
  }
  return { success: true, batches: (res.batches as PullBatch[]) ?? [] }
}

/** Ack pulled deliveries. Ownership is enforced server-side (cross-address ids affect 0 rows). */
export async function pullAck(
  client: RequestClient,
  ids: number[],
): Promise<{ success: boolean; acked?: number | undefined; error?: string | undefined }> {
  const res = await client.request('POST', '/api/v1/admin/pending/ack', { ids })
  if (res.status !== 200) {
    return { success: false, error: String(res.error ?? 'ack failed') }
  }
  return { success: true, acked: res.acked as number | undefined }
}

export interface PollStats {
  pulled: number
  acked: number
  errors: number
}

export interface StartPollingOptions {
  intervalMs?: number
  limit?: number
  /** Bounds the loop for tests (undefined = forever). */
  maxRounds?: number
}

/**
 * Poll → deliver → ack loop for the agent's own mailbox.
 *
 * Dedup key = delivery id (stable and unique). An `onEmail` failure leaves
 * the id un-acked — the mail re-pulls next round (nothing is lost, §9
 * failover). A failed pull round counts an error and backs off one interval.
 */
export async function startPolling(
  client: RequestClient,
  onEmail: (mail: { id: number; email: string; headers: unknown; body: unknown }) => void,
  opts: StartPollingOptions = {},
): Promise<PollStats> {
  const intervalMs = opts.intervalMs ?? 30_000
  const limit = opts.limit ?? 20
  const stats: PollStats = { pulled: 0, acked: 0, errors: 0 }
  const seen = new Set<number>()
  const sleep = (ms: number) => new Promise((r) => setTimeout(r, ms))
  for (let round = 0; opts.maxRounds === undefined || round < opts.maxRounds; round++) {
    try {
      const lst = await pullList(client, limit)
      if (!lst.success) {
        stats.errors++
        await sleep(intervalMs)
        continue
      }
      const delivered: number[] = []
      for (const batch of lst.batches) {
        for (const d of batch.deliveries ?? []) {
          if (seen.has(d.id)) continue
          seen.add(d.id)
          try {
            let body = batch.body
            if (typeof body === 'string') body = JSON.parse(body) as unknown
            onEmail({ id: d.id, email: d.email, headers: d.headers, body })
            delivered.push(d.id)
            stats.pulled++
          } catch {
            // un-acked on purpose: re-pulls next round
            seen.delete(d.id)
            stats.errors++
          }
        }
      }
      if (delivered.length) {
        const ack = await pullAck(client, delivered)
        if (ack.success) stats.acked += ack.acked ?? 0
      }
    } catch {
      stats.errors++
    }
    await sleep(intervalMs)
  }
  return stats
}
