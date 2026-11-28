/**
 * dsh mail hub — the ONE inbound component (SSE pull only).
 *
 * - system onboarded: one stream with the system/domain key; deliveries are
 *   routed by recipient to the binding/workspace.
 * - no system (advanced shared addresses): one stream per agent key.
 * - pause/unbind: no ack (mail stays pending); resume/bind drains pending.
 *
 * No HTTP listener, no HMAC, no webhook fields, no bridge key.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'

import {
  buildAgentSystemClient,
  GatewayClient,
  isAckedInboundResult,
  listAgentConfigs,
  listSystemDirs,
  loadAgentConfig,
  pullAck,
  pullList,
  type AgentConfig,
  type InboundPayload,
  type InboundResult,
} from '../core/index.js'
import { getAgentSystemId, setAgentHomeResolver } from '../core/agent-system-id.js'
import { isV2AgentScopeBinding } from '../core/poll-entry.js'
import { deliverInbound, installDshInboundProviders, toInboundResult } from './inbound.js'
import { agentHome, statusPath } from './workspace.js'
export interface Config {
  /** AIMAIL_PULL_MODE equivalent: auto|stream|poll. */
  mode?: string
}

interface StreamHandle {
  email: string
  gatewayUrl: string
  stop(): void
}

interface HubService {
  reconcile(): Promise<void>
  pause(email: string): Promise<void>
  resume(email: string): Promise<void>
  status(): Promise<Record<string, unknown>>
}

function sleep(ms: number, signal?: AbortSignal): Promise<void> {
  if (signal?.aborted) return Promise.resolve()
  return new Promise((resolve) => {
    const t = setTimeout(() => {
      signal?.removeEventListener('abort', onAbort)
      resolve()
    }, ms)
    ;(t as { unref?: () => void }).unref?.()
    function onAbort (): void {
      clearTimeout(t)
      resolve()
    }
    signal?.addEventListener('abort', onAbort, { once: true })
  })
}

/** Monotonic per-process counter for `writeStatus()` temp names (see the ENOENT note there). */
let writeStatusSeq = 0

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function headersOf(v: unknown): Record<string, unknown> {
  return typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {}
}

/**
 * One visible line for a gateway auth refusal. The gateway deliberately logs nothing for 401/403
 * (it is the client's key/anchor problem), so without this a refused pull looks like an idle
 * mailbox — the exact blind spot that hid L2 defect 1. Since v2.5 an anchor refusal is a hard
 * refusal with three distinct codes, and each one wants a different operator action.
 */
function authFailureLine(email: string, status: number, detail = ''): string {
  if (status === 403) {
    if (detail === 'anchor_required') {
      return (
        `[agent-aimail] pull refused for ${email}: the request carried no X-Aimail-Anchor claim ` +
        '(anchor_required) — this client/binding knows no anchor; re-activate the address'
      )
    }
    if (detail === 'anchor_unregistered') {
      return (
        `[agent-aimail] pull refused for ${email}: this address was activated WITHOUT an anchor ` +
        '(anchor_unregistered) — re-activate it with an anchor (contract §7.1)'
      )
    }
    return (
      `[agent-aimail] anchor refused by gateway for ${email}: this address is registered to a ` +
      `different anchor (${detail || 'anchor_mismatch'}) — re-activate it (contract §13.3); ` +
      'mail stays pending until then'
    )
  }
  return `[agent-aimail] pull rejected by gateway for ${email}: HTTP ${status} — the key was not accepted (re-activate)`
}
export class MailHub {
  private readonly ctx: Context
  private readonly ctrl = new AbortController()
  private handles: StreamHandle[] = []
  private started = false
  private lastEventAt = ''
  private lastError = ''
  private agentSystemId = ''
  private statusTimer: NodeJS.Timeout | undefined

  constructor(ctx: Context, _config: Config = {}) {
    this.ctx = ctx
  }

  private agents(): {
    get(id: string): Agent | undefined
    resume(opts: unknown): Promise<{ agent: Agent }>
    create(opts: { sessionId: string; meta?: { cwd?: string }; agentOptions?: unknown }): Promise<{ agent: Agent; dispose(): Promise<void> }>
  } {
    const agents = this.ctx.get('agents') as never
    if (!agents) throw new Error('mail-hub requires ctx.agents')
    return agents
  }

  private agentOptions(): unknown {
    return (this.ctx.get('agentDefaultModel') as { currentSelection?(): unknown } | undefined)?.currentSelection?.()
  }

  async start(): Promise<void> {
    if (this.started) return
    this.started = true
    // SOUL/skills providers + the activation skill staging used to live in the `inbound` plugin,
    // which `cordis.patch.yml` never mounts — so on a shipped dsh the prompt rendered without SOUL
    // and skills were never enumerated. They belong to the host boot, i.e. here.
    installDshInboundProviders()
    await this.reconcile()
    this.statusTimer = setInterval(() => void this.writeStatus().catch(() => undefined), 30_000)
    this.statusTimer.unref?.()
  }

  dispose(): void {
    this.ctrl.abort()
    for (const h of this.handles) h.stop()
    this.handles = []
    this.started = false
    if (this.statusTimer) clearInterval(this.statusTimer)
    this.statusTimer = undefined
    void this.writeStatus().catch(() => undefined)
  }

  private stopAll(): void {
    for (const h of this.handles) h.stop()
    this.handles = []
  }

  async reconcile(): Promise<void> {
    this.stopAll()
    if (this.ctrl.signal.aborted) return
    const sids = await listSystemDirs()
    const bindings: AgentConfig[] = []
    for (const sid of sids) {
      bindings.push(...(await this.listBindings(sid)))
    }
    // V2: one stream per (AgentSystemId, Gateway). Only bindings owned by
    // THIS AgentSystemId may supply the aggregate credential: a V1 key (no
    // ASID row at the gateway) would 403 the whole stream, and another
    // instance's binding must never be aggregated here.
    const agentSystemId = await getAgentSystemId()
    this.agentSystemId = agentSystemId
    const v2ByGateway = new Map<string, AgentConfig[]>()
    const legacy: AgentConfig[] = []
    let activeBindings = 0
    for (const b of bindings) {
      if (b.paused === true || !b.api_key) continue
      activeBindings++
      if (!isV2AgentScopeBinding(b)) {
        // V1 binding (no agent_system_id): the per-address pull endpoints were retired
        // (gateway v1.0.20) — there is nothing to arm. Say it once, loudly, per reconcile.
        legacy.push(b)
        continue
      }
      if (b.agent_system_id !== agentSystemId) {
        // Another instance/profile owns this address. Do not pull it; the
        // owner must re-activate after an identity change (contract §5.3).
        console.error(
          `[agent-aimail] skip binding ${b.email}: agent_system_id ${b.agent_system_id} != ${agentSystemId} (re-activate)`,
        )
        continue
      }
      const gw = (b.gateway_url ?? '').replace(/\/+$/, '')
      const group = v2ByGateway.get(gw)
      if (group) group.push(b)
      else v2ByGateway.set(gw, [b])
    }
    const streams = v2ByGateway.size + legacy.length
    console.error(`[agent-aimail] SSE reconcile: ${streams} gateway stream(s) from ${activeBindings} binding(s)`)
    for (const [gatewayUrl, members] of v2ByGateway) {
      const rep = members[0]
      this.handles.push(this.startStream({
        email: rep.email,
        gatewayUrl,
        apiKey: rep.api_key,
        identity: rep.email,
        agentSystemId,
        // Every address of the group rides along: the gateway checks each recorded anchor (§7.1),
        // so a partial claim list is refused exactly like a missing one.
        members,
      }))
    }
    for (const b of legacy) {
      console.error(
        `[agent-aimail] V1 binding ${b.email}: per-address pull is retired — re-activate the ` +
          'address with a code to join the aggregate (contract §5.3)',
      )
    }
    await this.writeStatus()
  }

  async pause(email: string): Promise<void> {
    const found = await this.findBinding(email)
    if (found) {
      await import('../core/index.js').then((m) => m.updateAgentConfig(found.sid, email, { paused: true }))
      await this.reconcile()
    }
  }

  async resume(email: string): Promise<void> {
    const found = await this.findBinding(email)
    if (!found) return
    await import('../core/index.js').then((m) => m.updateAgentConfig(found.sid, email, { paused: false }))
    await this.reconcile()
    void this.drain(email).catch((e) => {
      this.lastError = e instanceof Error ? e.message : String(e)
    })
  }

  /**
   * Contract §8.4 status shape: mode/agentSystemId/gateways[]. The legacy
   * `streams` list stays for existing evidence readers; it contains only
   * addresses (no key/code/body).
   */
  private statusBody(): Record<string, unknown> {
    const connected = this.handles.length > 0 && !this.ctrl.signal.aborted
    const seen = new Set<string>()
    const gateways: Array<Record<string, unknown>> = []
    for (const h of this.handles) {
      if (seen.has(h.gatewayUrl)) continue
      seen.add(h.gatewayUrl)
      gateways.push({
        gatewayUrl: h.gatewayUrl,
        connected,
        lastEventAt: this.lastEventAt,
        lastError: this.lastError,
      })
    }
    return {
      mode: 'sse',
      agentSystemId: this.agentSystemId,
      connected,
      streams: this.handles.map((h) => h.email),
      gateways,
      lastEventAt: this.lastEventAt,
      lastError: this.lastError,
    }
  }

  async status(): Promise<Record<string, unknown>> {
    return this.statusBody()
  }

  private async listBindings(sid: string): Promise<AgentConfig[]> {
    return await listAgentConfigs(sid)
  }

  private async findBinding(email: string, gatewayUrl?: string): Promise<{ sid: string; cfg: AgentConfig } | undefined> {
    const target = email.toLowerCase()
    const wantGateway = (gatewayUrl ?? '').replace(/\/+$/, '')
    for (const sid of await listSystemDirs()) {
      const cfg = await loadAgentConfig(sid, target)
      if (!cfg) continue
      // §9.1: route by gateway_url + recipient. A same-named address on another
      // gateway must not receive this delivery.
      if (wantGateway && (cfg.gateway_url ?? '').replace(/\/+$/, '') !== wantGateway) continue
      return { sid, cfg }
    }
    return undefined
  }


  private startStream(opts: {
    email: string
    gatewayUrl: string
    apiKey: string
    identity: string
    agentSystemId?: string
    /** This group's bindings — their anchor claims ride on every request (contract §7.1). */
    members?: readonly AgentConfig[]
  }): StreamHandle {
    const ctrl = new AbortController()
    const onAbort = (): void => ctrl.abort()
    this.ctrl.signal.addEventListener('abort', onAbort, { once: true })
    // Aggregate ⇒ the ONE constructor (ASID + anchor claims). Hand-building it here is what made
    // dsh pull zero mail once its address had a recorded anchor (L2 defect 1, 2026-11-25).
    // One transport only (per-address pull retired): the client is always the aggregate one.
    const client = buildAgentSystemClient(
      opts.gatewayUrl,
      opts.apiKey,
      opts.agentSystemId ?? this.agentSystemId,
      opts.identity,
      opts.members ?? [],
    )
    const run = async (): Promise<void> => {
      let streak = 0
      while (!ctrl.signal.aborted && !this.ctrl.signal.aborted) {
        try {
          const result = await client.streamRound(async (batches: unknown[]) => {
            for (const batch of batches) {
              await this.handleBatch(client, batch as never)
            }
          }, ctrl.signal)
          if (typeof result === 'object' && 'auth' in result) {
            // 401/403 from the gateway does NOT get logged gateway-side: without this line a
            // refused pull is indistinguishable from an idle mailbox. v2.5: 403 has three codes.
            const { auth: status, detail } = result as { auth: number; detail?: string }
            this.lastError = authFailureLine(opts.email, status, detail)
            console.error(this.lastError)
            streak++
            await sleep(Math.min(30_000, 1000 * 2 ** Math.min(streak, 5)), ctrl.signal)
            continue
          }
          if (result === 'stop') break
          if (result === 'stay') {
            streak = 0
            continue
          }
          if (result === 'occupied') {
            await sleep(Math.min(30_000, 1000 * 2 ** Math.min(streak, 5)), ctrl.signal)
            streak++
            continue
          }
          // drop / error
          streak++
          await sleep(Math.min(30_000, 1000 * 2 ** Math.min(streak, 5)), ctrl.signal)
        } catch (e) {
          if (ctrl.signal.aborted || this.ctrl.signal.aborted) break
          streak++
          this.lastError = e instanceof Error ? e.message : String(e)
          await sleep(Math.min(30_000, 1000 * 2 ** Math.min(streak, 5)), ctrl.signal)
        }
      }
      this.ctrl.signal.removeEventListener('abort', onAbort)
    }
    void run().catch((e) => {
      this.lastError = e instanceof Error ? e.message : String(e)
    })
    return {
      email: opts.email,
      gatewayUrl: opts.gatewayUrl,
      stop: () => {
        ctrl.abort()
        this.ctrl.signal.removeEventListener('abort', onAbort)
      },
    }
  }

  private async handleBatch(
    client: GatewayClient,
    batch: { body?: unknown; deliveries?: Array<{ id: number; email?: string; headers?: unknown }> },
  ): Promise<void> {
    const deliveries = Array.isArray(batch.deliveries) ? batch.deliveries : []
    const ack: number[] = []
    for (const d of deliveries) {
      const recipient = str(d.email).toLowerCase()
      if (!recipient) continue
      const found = await this.findBinding(recipient, client.baseUrl)
      if (!found) continue // system stream may carry other agents' mail — do not ack/steal
      if (found.cfg.paused) continue // leave pending for resume
      const headers = { ...headersOf(d.headers), 'x-aimail-email': recipient }
      const result = await this.deliver(found.cfg, batch.body, headers, recipient)
      if (isAckedInboundResult(result)) ack.push(Number(d.id))
      else if (result.detail) this.lastError = `${recipient}: ${result.status}: ${result.detail}`
    }
    if (ack.length > 0) {
      try {
        await pullAck(client, ack)
        this.lastEventAt = new Date().toISOString()
      } catch (e) {
        this.lastError = e instanceof Error ? e.message : String(e)
      }
    }
  }

  /** Test/one-shot entry: deliver one payload for a binding. */
  private async sessionInWorkspace(sessionId: string, workspacePath: string): Promise<boolean> {
    const cwd = (this.ctx.get('sessions') as { get(id: string): { header?: { cwd?: string } } | undefined } | undefined)
      ?.get(sessionId)?.header?.cwd
    if (!cwd) return true // no session record (cold) — the binding workspace is authoritative
    try {
      return (await fs.realpath(cwd)) === workspacePath
    } catch {
      return false
    }
  }

  async deliverOne(cfg: AgentConfig, body: unknown, headers: Record<string, unknown>, recipient: string): Promise<InboundResult> {
    return await this.deliver(cfg, body, headers, recipient)
  }

  private async workspaceFor(cfg: AgentConfig): Promise<{ id: string; path: string; title?: string } | undefined> {
    if (cfg.workspace_path) {
      try {
        const real = await fs.realpath(cfg.workspace_path)
        const stat = await fs.stat(real)
        if (stat.isDirectory()) return { id: cfg.workspace_id ?? '', path: real, ...(cfg.workspace_title ? { title: cfg.workspace_title } : {}) }
      } catch {
        /* fall through to default */
      }
    }
    // Contract §7.2: no workspace ⇒ do NOT ack; never invent a default.
    return undefined
  }

  private async deliver(cfg: AgentConfig, body: unknown, headers: Record<string, unknown>, recipient: string): Promise<InboundResult> {
    if (typeof body !== 'object' || body === null) {
      return { status: 'rejected', detail: 'payload is not an object' }
    }
    const payload = body as InboundPayload
    // REQ-2: workspace preflight BEFORE the heavy inbound pipeline. The pipeline writes
    // meta/snapshots/FTS and downloads attachments, so doing it first would leave dirty side effects
    // even though the delivery is not acked.
    const workspace = await this.workspaceFor(cfg)
    if (!workspace) {
      const detail = `no workspace for ${recipient}`
      this.lastError = detail
      return { status: 'rejected', detail }
    }
    // ONE delivery implementation (§9 E1/E2/E3 + C16 + C17 live in `deliverInbound`). Before this
    // the hub had its own weaker router: thread-level reuse only, no direct-reply edge, and no
    // dispatch line — so the shipped dsh path could not honour E1 and the s7/s8 evidence had no
    // producer. The workspace and the agents service are resolved HERE (realpath of the binding's
    // workspace), so the anchor has a single source of truth.
    const out = await deliverInbound(
      this.ctx,
      { cfg, agentAddr: recipient },
      payload,
      headers,
      {
        workspace: { id: workspace.id, path: workspace.path },
        agents: this.agents(),
        agentOptions: this.agentOptions(),
        sessionExists: (id: string) => this.sessionInWorkspace(id, workspace.path),
      },
    )
    const result = toInboundResult(out)
    if (result.status === 'rejected' && out.detail) this.lastError = out.detail
    return result
  }

  private async drain(email: string): Promise<void> {
    const found = await this.findBinding(email)
    if (!found) return
    // Aggregate only: the per-address pending endpoint is retired. The group's members are
    // needed because the request must carry their anchor claims (§7.1).
    const client = buildAgentSystemClient(
      found.cfg.gateway_url,
      found.cfg.api_key,
      found.cfg.agent_system_id ?? this.agentSystemId,
      found.cfg.email,
      [found.cfg],
    )
    const listed = await pullList(client, 20)
    if (!listed.success) {
      if (listed.status === 401 || listed.status === 403) {
        // Same blindness as the stream: the gateway logs nothing for an auth refusal.
        this.lastError = authFailureLine(found.cfg.email, listed.status)
        console.error(this.lastError)
      }
      return
    }
    for (const batch of listed.batches) {
      await this.handleBatch(client, batch as never)
    }
  }

  private async writeStatus(): Promise<void> {
    const p = statusPath()
    await fs.mkdir(path.dirname(p), { recursive: true })
    const body = this.statusBody()
    // Unique temp name per writer: `restart` (plugin install takes effect only after a restart)
    // overlaps the old hub's final write with the new hub's first one, and a SHARED `${p}.tmp`
    // makes the loser's rename fail with ENOENT — exactly what L3 hit on clean CI machines while
    // local runs (faster, no overlap) passed. pid + a counter is enough: the writers are in-process
    // timers and one short-lived predecessor, not separate machines.
    writeStatusSeq += 1
    const tmp = `${p}.${process.pid}.${writeStatusSeq}.tmp`
    try {
      await fs.writeFile(tmp, JSON.stringify(body, null, 2) + '\n', { mode: 0o600 })
      await fs.rename(tmp, p)
    } catch (e) {
      // Never let bookkeeping kill the hub: a failed rename must not propagate to the timer.
      await fs.unlink(tmp).catch(() => {})
      throw e
    }
  }
}

export function apply(ctx: Context, config: Config = {}): void {
  // CR-1: derive the AgentSystemId from this DSH profile's home, not the OS
  // user's home (otherwise DSH/Pi/OpenClaw on one machine merge).
  setAgentHomeResolver(agentHome)
  const hub = new MailHub(ctx, config)
  ctx.provide('mailHub', {
    reconcile: () => hub.reconcile(),
    pause: (email: string) => hub.pause(email),
    resume: (email: string) => hub.resume(email),
    status: () => hub.status(),
  } satisfies HubService)
  ctx.effect(() => {
    void hub.start().then(() => {
      // Read-on-boot: an address binding written by an external/headless
      // activator may land just after the first reconcile. Re-arm once.
      const t = setTimeout(() => {
        void hub.reconcile().catch(() => undefined)
      }, 2_000)
      t.unref?.()
    }).catch((e) => {
      console.warn(`[aimail-hub] start failed: ${e instanceof Error ? e.message : String(e)}`)
    })
    return () => hub.dispose()
  }, 'aimail.mailHub()')
}
