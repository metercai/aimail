/**
 * agent-system-client.ts — the ONE constructor for the AgentSystemId aggregate client.
 *
 * Every aggregate pull (SSE stream, long-poll, ack) must carry BOTH headers:
 *   `X-Agent-System-Id` — which group is being pulled;
 *   `X-Aimail-Anchor`   — `email;sha256(anchor)` for every address of that group (contract §7.1).
 *
 * Why this file exists (2026-11-25, L2 defect 1): the anchor header was written into this
 * constructor only, and two other places built their own client by hand —
 *   * `dsh/mail-hub.ts` (its own SSE loop + drain)  ⇒ dsh went to ZERO delivery the moment its
 *     address had a recorded anchor: the gateway answers 403 `anchor_mismatch`, the stream backs
 *     off silently and nothing is ever pulled;
 *   * `core/system-pull.ts` (pi and openclaw's live branches) ⇒ latent, masked only because those
 *     hosts had no anchor row yet (they were not registering one — separate defect).
 * Both now go through here, and the constructor resolves the group's members itself
 * (`resolveAgentSystemMembers`) instead of trusting the caller to pass them: a caller that
 * forgets a parameter is exactly how this class of defect happens.
 *
 * The anchor is not a secret (it is the agent's own stable directory — dsh: the workspace,
 * pi: `~/.pi/agent`, hermes: the profile home), and the gateway never interprets it: it only
 * compares the hash with the one registered at activation.
 */
import type { AgentConfig } from './types.js'
import { GatewayClient, type GatewayFetchImpl } from './gateway.js'
import { listAgentConfigs } from './config.js'
import { listSystemDirs } from './system-config.js'
import { anchorHeader } from './anchor-id.js'
import { PULL_HTTP_TIMEOUT_MS } from './pull-contract.js'

/** V2 predicate: the binding is activated under an AgentSystemId. */
export function isV2AgentScopeBinding(cfg: AgentConfig | undefined | null): boolean {
  if (!cfg) return false
  return typeof cfg.agent_system_id === 'string' && cfg.agent_system_id.length > 0
}

/**
 * ONE constructor for the AgentSystemId aggregate client (V2 fix, B2): the aggregate
 * SSE/pending/ack paths, the `X-Agent-System-Id` header and the `X-Aimail-Anchor` claims.
 * Every aggregate pull entry (tier-2, no-pointer grouping, host pull entries) must build its
 * client through here — B2 was "a second entry built a plain client and hit the per-address
 * endpoint without the header"; defect 1 was the same mistake one layer down (the header existed
 * here, but hosts built their own client and dropped it).
 */
export function buildAgentSystemClient(
  gatewayUrl: string,
  apiKey: string,
  agentSystemId: string,
  identity: string,
  /** Bindings of this group — their anchors ride along so the gateway can verify them (§7.1). */
  members: readonly Pick<AgentConfig, 'email' | 'workspace_path'>[] = [],
  /** Transport seam: a plugin host whose egress is a broker passes its own fetch here. */
  opts: { fetchImpl?: GatewayFetchImpl } = {},
): GatewayClient {
  const anchors = anchorHeader(members)
  return new GatewayClient(gatewayUrl, apiKey, PULL_HTTP_TIMEOUT_MS, identity, {
    ...(opts.fetchImpl ? { fetchImpl: opts.fetchImpl } : {}),
    streamPath: '/api/v1/agent-system/pending/stream',
    pendingPath: '/api/v1/agent-system/pending',
    ackPath: '/api/v1/agent-system/pending/ack',
    extraHeaders: {
      'X-Agent-System-Id': agentSystemId,
      // The anchor identifier the gateway recorded at activation: `email;sha256`, comma separated.
      // It is not a secret (the anchor is the agent's own workspace) — it is the claim the gateway
      // checks, which is what stops a locally edited binding from moving a mailbox.
      ...(anchors ? { 'X-Aimail-Anchor': anchors } : {}),
    },
  })
}

/**
 * The active members of one `(AgentSystemId, Gateway)` group — what the anchor claims are built
 * from. Paused addresses, keyless bindings and copies of the same address are excluded; the
 * gateway only enforces addresses it has a record for, so an empty result is not an error.
 */
export async function resolveAgentSystemMembers(
  agentSystemId: string,
  gatewayUrl: string,
): Promise<AgentConfig[]> {
  const asid = String(agentSystemId ?? '').trim()
  if (!asid) return []
  const wantGateway = String(gatewayUrl ?? '').replace(/\/+$/, '')
  const seen = new Set<string>()
  const out: AgentConfig[] = []
  try {
    for (const sid of await listSystemDirs()) {
      for (const c of await listAgentConfigs(sid)) {
        if (!isV2AgentScopeBinding(c) || c.agent_system_id !== asid) continue
        if (c.paused === true || !c.api_key) continue
        if (wantGateway && String(c.gateway_url ?? '').replace(/\/+$/, '') !== wantGateway) continue
        const email = String(c.email ?? '').trim().toLowerCase()
        if (!email || seen.has(email)) continue
        seen.add(email)
        out.push(c)
      }
    }
  } catch {
    // A missing/corrupt systems dir must not stop arming: the stream still runs, it just carries
    // fewer claims (the gateway then refuses only the addresses it cannot match).
    return out
  }
  return out
}

/** Convenience: build the aggregate client with the members resolved from the binding store. */
export async function buildAgentSystemClientResolved(
  gatewayUrl: string,
  apiKey: string,
  agentSystemId: string,
  identity: string,
): Promise<GatewayClient> {
  const members = await resolveAgentSystemMembers(agentSystemId, gatewayUrl)
  return buildAgentSystemClient(gatewayUrl, apiKey, agentSystemId, identity, members)
}
