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
import { randomUUID } from 'node:crypto'

import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'

import {
  GatewayClient,
  listSystemDirs,
  loadAgentConfig,
  processInboundMail,
  pullAck,
  pullList,
  readLocalMeta,
  listAgentConfigs,
  type AgentConfig,
  type InboundPayload,
} from '../core/index.js'
import { stampSessionMeta } from '../core/meta.js'
import { getAgentSystemId, setAgentHomeResolver } from '../core/agent-system-id.js'
import { routeThread } from '../runtime/session-router.js'
import { isV2AgentScopeBinding } from '../core/poll-entry.js'
import { isAckedInboundResult, type InboundResult } from '../core/index.js'
import { readSystemGatewayConfig } from '../core/provision.js'
import { agentHome, statusPath } from './workspace.js'

export const name = 'mail-hub'
export const inject = ['mail', 'agents']

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

function str(v: unknown): string {
  return typeof v === 'string' ? v : ''
}

function headersOf(v: unknown): Record<string, unknown> {
  return typeof v === 'object' && v !== null ? (v as Record<string, unknown>) : {}
}

function messageText(enriched: Record<string, unknown>, to: string): string {
  return JSON.stringify({ ...enriched, to })
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
    const systems: Array<{ sid: string; cfg: Awaited<ReturnType<typeof readSystemGatewayConfig>> }> = []
    const bindings: AgentConfig[] = []
    for (const sid of sids) {
      const cfg = await readSystemGatewayConfig(sid)
      if (cfg?.admin_key) systems.push({ sid, cfg })
      bindings.push(...(await this.listBindings(sid)))
    }
    // V2: one stream per (AgentSystemId, Gateway). Only bindings owned by
    // THIS AgentSystemId may supply the aggregate credential: a V1 key (no
    // ASID row at the gateway) would 403 the whole stream, and another
    // instance's binding must never be aggregated here.
    const agentSystemId = await getAgentSystemId()
    this.agentSystemId = agentSystemId
    const v2ByGateway = new Map<string, AgentConfig>()
    const legacy: AgentConfig[] = []
    let activeBindings = 0
    for (const b of bindings) {
      if (b.paused === true || !b.api_key) continue
      activeBindings++
      if (!isV2AgentScopeBinding(b)) {
        // V1 binding: the gateway has no ASID ownership row for it. Keep the
        // legacy per-address pull alive instead of 403ing an aggregate.
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
      if (!v2ByGateway.has(gw)) v2ByGateway.set(gw, b)
    }
    const streams = v2ByGateway.size + legacy.length
    console.error(`[agent-aimail] SSE reconcile: ${streams} gateway stream(s) from ${activeBindings} binding(s)`)
    for (const [gatewayUrl, b] of v2ByGateway) {
      this.handles.push(this.startStream({
        email: b.email,
        gatewayUrl,
        apiKey: b.api_key,
        identity: b.email,
        agentSystemId,
      }))
    }
    for (const b of legacy) {
      console.error(`[agent-aimail] V1 binding ${b.email}: per-address pull (re-activate to join the aggregate)`)
      this.handles.push(this.startStream({
        email: b.email,
        gatewayUrl: b.gateway_url,
        apiKey: b.api_key,
        identity: b.email,
      }))
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

  private startStream(opts: { email: string; gatewayUrl: string; apiKey: string; identity: string; agentSystemId?: string }): StreamHandle {
    const ctrl = new AbortController()
    const onAbort = (): void => ctrl.abort()
    this.ctrl.signal.addEventListener('abort', onAbort, { once: true })
    const client = new GatewayClient(opts.gatewayUrl, opts.apiKey, 30_000, opts.identity, opts.agentSystemId ? {
      streamPath: '/api/v1/agent-system/pending/stream',
      pendingPath: '/api/v1/agent-system/pending',
      ackPath: '/api/v1/agent-system/pending/ack',
      extraHeaders: { 'X-Agent-System-Id': opts.agentSystemId },
    } : {})
    const run = async (): Promise<void> => {
      let streak = 0
      while (!ctrl.signal.aborted && !this.ctrl.signal.aborted) {
        try {
          const result = await client.streamRound(async (batches) => {
            for (const batch of batches) {
              await this.handleBatch(client, batch as never)
            }
          }, ctrl.signal)
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
    // REQ-2: workspace preflight BEFORE the heavy inbound pipeline. The
    // pipeline writes meta/snapshots/FTS and downloads attachments; doing it
    // first means a workspace miss leaves dirty side effects even though the
    // delivery is not acked.
    const workspace = await this.workspaceFor(cfg)
    if (!workspace) {
      const detail = `no workspace for ${recipient}`
      this.lastError = detail
      return { status: 'rejected', detail }
    }
    const enriched = await processInboundMail(payload, headers as Record<string, string>, { systemId: cfg.system_id, email: cfg.email })
    if (enriched === null) return { status: 'intercepted', detail: 'ping/pong consumed' }
    const root = (Array.isArray(payload.references) && payload.references[0]) || str(headers.in_reply_to) || str(payload.message_id)
    const meta = root ? await readLocalMeta(recipient, root) : undefined
    const target = routeThread(payload as never, { id: workspace.id, path: workspace.path }, meta as never, headers)
    const sessionId = target.kind === 'followup' ? String(target.sessionId ?? '') : ''
    const message = createUserMessage({
      content: [{ type: 'text', text: messageText(enriched as Record<string, unknown>, recipient) }],
      source: { kind: 'user' },
    })
    const agents = this.agents()
    if (sessionId && (await this.sessionInWorkspace(sessionId, workspace.path))) {
      const live = agents.get(sessionId)
      if (live) {
        live.followup(message)
        await stampSessionMeta(recipient, str(payload.message_id), { sessionId, workspacePath: workspace.path })
        return { status: 'accepted', detail: `followup ${sessionId}` }
      }
      try {
        const handle = await agents.resume({ resumeSessionId: sessionId, agentOptions: this.agentOptions() })
        handle.agent.followup(message)
        await stampSessionMeta(recipient, str(payload.message_id), { sessionId, workspacePath: workspace.path })
        return { status: 'accepted', detail: `resumed ${sessionId}` }
      } catch {
        /* fall through to new session */
      }
    }
    const newId = randomUUID()
    try {
      const handle = await agents.create({ sessionId: newId, meta: { cwd: workspace.path }, agentOptions: this.agentOptions() })
      handle.agent.followup(message)
      await stampSessionMeta(recipient, str(payload.message_id), { sessionId: newId, workspacePath: workspace.path })
      return { status: 'accepted', detail: `created ${newId}` }
    } catch (e) {
      const detail = e instanceof Error ? e.message : String(e)
      this.lastError = detail
      return { status: 'rejected', detail: `session create failed: ${detail}` }
    }
  }

  private async drain(email: string): Promise<void> {
    const found = await this.findBinding(email)
    if (!found) return
    const client = new GatewayClient(found.cfg.gateway_url, found.cfg.api_key, 30_000, found.cfg.email)
    const listed = await pullList(client, 20)
    if (!listed.success) return
    for (const batch of listed.batches) {
      await this.handleBatch(client, batch as never)
    }
  }

  private async writeStatus(): Promise<void> {
    const p = statusPath()
    await fs.mkdir(path.dirname(p), { recursive: true })
    const body = this.statusBody()
    const tmp = `${p}.tmp`
    await fs.writeFile(tmp, JSON.stringify(body, null, 2) + '\n', { mode: 0o600 })
    await fs.rename(tmp, p)
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
