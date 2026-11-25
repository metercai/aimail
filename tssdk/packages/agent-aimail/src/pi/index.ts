/**
 * pi-aimail — AIMail extension for pi (earendil-works/pi).
 *
 * Same capability surface as dsh-aimail / openclaw-aimail, adapted to pi's
 * extension API:
 * - 15 mail/board tools via MAIL_TOOLS (single TS semantic source), bare
 *   names, registered with pi.registerTool (TypeBox parameters).
 * - Inbound receiver: pi has no HTTP route registration, so the extension
 *   owns a local listener (127.0.0.1:9099 by default — the common inbound_port) that the bridge pushes
 *   to; the handler runs HMAC verify → processInboundMail (13-step + ping/
 *   pong intercept) → pi.sendUserMessage (always triggers a turn).
 * - Identity: ~/.pi/.agentmail pointer ({system_id, email}) is the sole
 *   trust source; outbound X-AIMail-Agent: pi/<detected host version>.
 *
 * Install: copy/symlink into ~/.pi/agent/extensions/ (or ship as a pi
 * package). Binding: create ~/.pi/.agentmail with {system_id, email}.
 */
import { installActivationSkillSync } from '../core/agent-level.js'
import { activateHost } from '../hosts/activate.js'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { processInboundMail, releaseAllSystems, routeAddressFromHeaders, loadAgentConfig, startAgentPullEntries, startSystemPull, startAgentSystemGroups, resolveAgentPullSettings, freshDeliveryTarget, listAgentConfigs, listSystemDirs, onBindingCreated, setProfileDirResolver, setSoulProvider, setSkillsProvider, setToolsetProvider, listSkillNames, type AgentConfig, type AgentPullHandle, type AgentPullOverrides, type InboundPayload, packageResourcesRoot } from '../core/index.js'
import { countActiveAgentSystemBindings } from '../core/index.js'
import { setAgentHomeResolver } from '../core/index.js'
import { resolveByRecipient } from '../mail/index.js'
import { getAgentSystemId } from '../core/agent-system-id.js'
import { readSystemGatewayConfig } from '../core/provision.js'
import { agentIdentity, initIdentity, readPointer } from './identity.js'
import { buildPiTools } from './tools.js'
import { setAddressModel } from '../core/host-capabilities.js'

// Capability declaration (contract §11): pi/TUI gives no way to wake a *different*
// session safely — its session files carry no lock and a foreign writer cannot be
// detected — so it stays a single-address host. Declaring it here is what makes the
// SDK refuse a second address before consuming an activation code, instead of
// silently shadowing the first one.
setAddressModel('single')
// Env mirror: survives a bundled duplicate of the capabilities module (see its docstring).
process.env.AIMAIL_HOST_ADDRESS_MODEL = 'single'

/** Options for the pi extension. There is nothing to configure for delivery any more: SSE/pull only. */
export interface PiAimailOptions {
  /** @deprecated 2026-11-25 — no listener exists; accepted and ignored so old configs do not crash. */
  inboundPort?: number
  /** @deprecated 2026-11-25 — push is gone; any value is ignored (SSE/pull is the only transport). */
  transport?: 'sse' | 'push'
}

/** The resolved target of one inbound payload (binding + routed address). */
export interface PiInboundTarget {
  cfg: AgentConfig
  agentAddr: string
}

/** Result of the shared inbound chain (routing→delivery). */
export interface PiInboundOutcome {
  status: string
  /**
   * Did the mail reach the session? `false` ⇒ a pulled delivery must NOT be
   * acked (it re-pulls next round).
   */
  ok: boolean
  /** Free-form evidence line (e.g. that delivery was a hand-off, not a landing). */
  detail?: string
}

/**
 * Recipient routing — the ONE routing step both entries share (push handler +
 * pull loop). The routed address is authoritative: `to[0]` is often an external
 * recipient (payload.to is the filtered full list), so the X-AIMail-Email
 * header wins when present; the pointer email is the last-resort fallback.
 */
export async function resolvePiTarget(
  payload: InboundPayload,
  headers: Record<string, unknown>,
): Promise<PiInboundTarget | undefined> {
  const routeAddr = routeAddressFromHeaders(headers)
  const toRaw = Array.isArray(payload.to)
    ? payload.to
    : typeof payload.to === 'string'
      ? [payload.to]
      : []
  const candidates: unknown[] = routeAddr ? [routeAddr] : toRaw
  let cfg: Awaited<ReturnType<typeof resolveByRecipient>> | undefined
  let agentAddr = ''
  for (const t of candidates) {
    const addr = String(t).trim()
    if (!addr.includes('@')) continue
    try {
      const c = await resolveByRecipient(addr)
      if (c) {
        cfg = c
        agentAddr = addr
        break
      }
    } catch { /* try next candidate */ }
  }
  return cfg ? { cfg, agentAddr } : undefined
}

/**
 * Post-routing inbound chain: preprocess (13 steps + ping/pong intercept) →
 * deliver into the running session (always triggers a turn).
 *
 * ⚠ The TRUST step is not here: pushed mail is verified by the per-address HMAC
 * (bridge↔endpoint boundary), pulled mail by the gateway's own verification of
 * the agent-scope key. Routing, enrichment and delivery are one chain.
 */
export async function deliverPiInbound(
  pi: ExtensionAPI,
  target: PiInboundTarget,
  payload: InboundPayload,
  headers: Record<string, unknown>,
): Promise<PiInboundOutcome> {
  const { cfg, agentAddr } = target
  const result = await processInboundMail(
    payload,
    headers as Record<string, string>,
    { systemId: cfg.system_id, email: cfg.email },
  )
  if (result === null) {
    return { status: 'intercepted', ok: true }
  }
  // Deliver into the running session — always triggers a turn.
  pi.sendUserMessage(JSON.stringify({ ...result, to: agentAddr }), {
    deliverAs: 'steer',
  })
  // `sendUserMessage` is fire-and-forget: this is the hand-off, not a landing. Contract 附 E marks
  // pi as `admission-only` (and S, so C17 does not apply); the detail says so.
  return { status: 'delivered', detail: 'admission-only: handed to pi.sendUserMessage', ok: true }
}

/**
 * Pull entry (agent-scope bindings only) — the missing production wire for the
 * address-code flow: an address activated by an activation CODE has no push
 * path (the gateway stores webhook_url=NULL for it), so this long-lived session
 * fetches its own mail on a timer. Pulled mail enters `deliverPiInbound`: the
 * very same chain pushed mail takes. Never started for system/bridge bindings —
 * the decision lives in mail-core (`startAgentPullEntries`).
 */
async function startInboundPullInner(
  pi: ExtensionAPI,
  opts: {
    log?: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void }
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
    /** Test seam: build the aggregate/per-binding client (real path unchanged). */
    clientFor?: (rep: AgentConfig) => import('../core/index.js').RequestClient
  } = {},
): Promise<AgentPullHandle[]> {
  const ptr = readPointer()
  const sid = opts.systemId ?? ptr.system_id ?? ''
  const sys = sid ? await readSystemGatewayConfig(sid).catch(() => undefined) : undefined
  if (sys?.admin_key) {
    const h = await startSystemPull({
      systemId: sid,
      gatewayUrl: sys.gateway_url,
      apiKey: sys.admin_key,
      ...(opts.log ? { log: opts.log.info } : {}),
      onEmail: async (mail) => {
        if (typeof mail.body !== 'object' || mail.body === null) {
          throw new Error(`pulled delivery ${mail.id} for ${mail.email} carries no JSON payload — not acking`)
        }
        const headers: Record<string, unknown> = { ...(mail.headers ?? {}), 'x-aimail-email': mail.email }
        const target = await resolvePiTarget(mail.body as InboundPayload, headers)
        if (!target) throw new Error(`pulled delivery ${mail.id}: no binding for ${mail.email} — not acking`)
        const out = await deliverPiInbound(pi, target, mail.body as InboundPayload, headers)
        if (!out.ok) throw new Error(`pulled delivery ${mail.id}: inbound chain did not deliver (${out.status}) — not acking`)
      },
    })
    return [{
      email: `system:${sid}`, started: true, reason: 'enabled', intervalMs: 0, limit: 0,
      stop: h.stop, done: h.done as Promise<import('../core/index.js').PollStats>, stats: () => h.stats(),
    }]
  }
  // B2 (no system): one AgentSystemId aggregate stream (tier-2 / pointer).
  // The pointer is a DISCOVERY entry, not an agent switch (P0-7): a paused
  // pointer address must NOT starve the other active addresses of the same
  // (AgentSystemId, Gateway) — they keep exactly ONE aggregate stream (rep =
  // an active sibling), and the pointer's own mail is skipped at delivery
  // (fresh lookup ⇒ skip('paused'), left pending until resume). Pi is a
  // single-agent host, so the pool is normally the pointer's own addresses.
  const ptrCfg = ptr.system_id && ptr.email
    ? await loadAgentConfig(ptr.system_id, ptr.email).catch(() => undefined)
    : undefined
  if (ptrCfg?.api_key) {
    const env = opts.env ?? process.env
    const settings = resolveAgentPullSettings(ptrCfg, env, opts.overrides ?? {})
    if (!settings.enabled) {
      // Global switch (AIMAIL_PULL=0 / disabled): one decision for the whole
      // profile — tier-2, grouping and per-binding all honor the same env.
      opts.log?.info(
        `[aimail-pull] pointer agent:${ptrCfg.email} pull disabled by config ` +
          `(${settings.reason}) — no stream`,
      )
      return []
    }
    if (ptrCfg.paused === true) {
      // P0-7: pause the address, not the AgentSystemId. Scan the active
      // sibling pool of this (AgentSystemId, Gateway) and keep ONE aggregate
      // stream for it (the pointer's own mail is skipped at delivery level).
      const asid = ptrCfg.agent_system_id ?? (await getAgentSystemId())
      const rawPool: AgentConfig[] = []
      const sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
      for (const s of sids) {
        for (const c of await listAgentConfigs(s)) {
          const isSelf =
            c.system_id === ptrCfg.system_id &&
            (c.email ?? '').toLowerCase() === (ptrCfg.email ?? '').toLowerCase()
          if (isSelf) continue
          if (c.paused === true || !c.api_key) continue
          if (c.agent_system_id !== asid) continue
          if (c.gateway_url !== ptrCfg.gateway_url) continue
          rawPool.push(c)
        }
      }
      const poolSeen = new Set<string>()
      const pool: AgentConfig[] = []
      for (const c of rawPool) {
        const e = (c.email ?? '').toLowerCase()
        if (!e || poolSeen.has(e)) continue
        poolSeen.add(e)
        pool.push(c)
      }
      if (pool.length === 0) {
        opts.log?.info(
          `[aimail-pull] pointer agent:${ptrCfg.email} paused, no active ` +
            'sibling — no stream (resume re-arms in-process)',
        )
        return []
      }
      const handles = await startAgentSystemGroups(pool, {
        onEmail: (_cfg, mail) => deliverPiPulled(pi, mail),
        ...(opts.log ? { log: opts.log.info } : {}),
        ...(opts.env ? { env: opts.env } : {}),
        ...(opts.overrides ? { overrides: opts.overrides } : {}),
        ...(opts.clientFor ? { clientFor: opts.clientFor } : {}),
      })
      opts.log?.info(
        `[aimail-pull] pointer agent:${ptrCfg.email} paused — aggregate via ` +
          `agent:${asid} gw=${ptrCfg.gateway_url} ` +
          `members=[${pool.map((m) => m.email).join(', ')}] (pointer excluded)`,
      )
      return handles
    }
    // Normal branch: the pointer address is active — the existing tier-2
    // aggregate stream (rep = pointer), now wired with the resolved settings
    // (P0-5/P1-C) and the delivery-level fresh lookup (P0-6).
    const h = await startSystemPull({
      systemId: ptrCfg.email,
      agentSystemId: ptrCfg.agent_system_id ?? (await getAgentSystemId()),
      gatewayUrl: ptrCfg.gateway_url,
      apiKey: ptrCfg.api_key,
      ...(opts.log ? { log: opts.log.info } : {}),
      ...(opts.clientFor ? { client: opts.clientFor(ptrCfg) } : {}),
      intervalMs: settings.intervalMs,
      limit: settings.limit,
      mode: settings.mode,
      onEmail: async (mail) => {
        // Delivery-level fresh lookup (P0-6): a runtime pause/archive takes
        // effect on the very next delivery — paused ⇒ skip('paused'),
        // unknown ⇒ skip('unknown') (typed result, not a throw).
        const { skip } = await freshDeliveryTarget(mail.email)
        if (skip) return skip
        await deliverPiPulled(pi, mail)
      },
    })
    return [{
      email: `agent:${ptrCfg.email}`, started: true, reason: 'enabled',
      intervalMs: settings.intervalMs, limit: settings.limit,
      stop: h.stop, done: h.done as Promise<import('../core/index.js').PollStats>, stats: () => h.stats(),
    }]
  }
  return await startAgentPullEntries({
    systemId: opts.systemId ?? ptr.system_id ?? '',
    // Pi is a single-agent host: pin the pull to the pointer's own address
    // (never fan out over other addresses in the shared system).
    ...(ptr.email ? { onlyEmail: ptr.email } : {}),
    ...(opts.log ? { log: opts.log.info } : {}),
    ...(opts.env ? { env: opts.env } : {}),
    ...(opts.overrides ? { overrides: opts.overrides } : {}),
    onEmail: (_cfg, mail) => deliverPiPulled(pi, mail),
  })
}

/**
 * Public pull entry: start the loops, then emit the standardized reconcile
 * line L2 S2/S4 read:
 *   `SSE reconcile: <streams> gateway stream(s) from <bindings> binding(s)`
 * `bindings` counts this AgentSystemId's active V2 bindings (paused/keyless
 * excluded), so a two-address host reports `from 2 binding(s)` while still
 * running one aggregate stream.
 */
export async function startInboundPull(
  pi: ExtensionAPI,
  opts: {
    log?: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void }
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
    clientFor?: (rep: AgentConfig) => import('../core/index.js').RequestClient
  } = {},
): Promise<AgentPullHandle[]> {
  const handles = await startInboundPullInner(pi, opts)
  if (handles.length > 0) {
    const asid = await getAgentSystemId()
    const bindings = await countActiveAgentSystemBindings(asid)
    opts.log?.info(
      `[aimail-pull] SSE reconcile: ${handles.length} gateway stream(s) from ${bindings} binding(s)`,
    )
  }
  return handles
}

/**
 * Shared pulled-delivery chain for pi (tier-2 + no-pointer funnel here):
 * body shape check → recipient routing → inbound chain. A non-JSON body is a
 * MALFORMED delivery (a real error: throw ⇒ un-acked ⇒ re-pull); a missing
 * target or a failed inbound chain is likewise not-acked (re-pulled). The
 * delivery-level skip (paused/unknown) is the caller's typed-result concern
 * (P1-A), decided from a FRESH binding lookup before this chain runs.
 */
async function deliverPiPulled(
  pi: ExtensionAPI,
  mail: { id: number; email: string; headers: Record<string, unknown>; body: unknown },
): Promise<void> {
  if (typeof mail.body !== 'object' || mail.body === null) {
    throw new Error(`pulled delivery ${mail.id} for ${mail.email} carries no JSON payload — not acking`)
  }
  const headers: Record<string, unknown> = {
    ...(mail.headers ?? {}),
    'x-aimail-email': mail.email,
  }
  const target = await resolvePiTarget(mail.body as InboundPayload, headers)
  if (!target) {
    throw new Error(`pulled delivery ${mail.id}: no binding for ${mail.email} — not acking`)
  }
  const out = await deliverPiInbound(pi, target, mail.body as InboundPayload, headers)
  if (!out.ok) {
    throw new Error(
      `pulled delivery ${mail.id}: inbound chain did not deliver (${out.status}) — not acking`,
    )
  }
}


export default function piAimail (pi: ExtensionAPI, options: PiAimailOptions = {}) {
  // The only options left are deprecated no-ops; read them so a caller passing them is not a type error.
  void options
  // Host identification (#5): verify environment before activating.
  activateHost('pi')
  initIdentity()
  setProfileDirResolver(() => path.join(os.homedir(), '.pi'))
  // CR-1: the AgentSystemId is per agent instance, not the OS user. Pi's
  // agent home is ~/.pi, so DSH/OpenClaw on the same machine do not share it.
  setAgentHomeResolver(() => path.join(os.homedir(), '.pi'))

  // Per-agent SOUL.md: pi is a single-agent host (agent_id 'agent'), and
  // ~/.pi/agent/ IS the per-agent dir (extensions/ skills/ sessions/ all live
  // under it), so the SOUL anchor is ~/.pi/agent/SOUL.md. Missing ⇒ '' (most
  // pi installs never author one; the template renders empty, documented).
  const piAgentDir = path.join(os.homedir(), '.pi', 'agent')
  setSoulProvider(() => {
    const p = path.join(piAgentDir, 'SOUL.md')
    try {
      return fs.existsSync(p) ? fs.readFileSync(p, 'utf-8') : ''
    } catch {
      return ''
    }
  })
  // pi skills ARE per-agent: every skill is a subdirectory of
  // ~/.pi/agent/skills/<name>/ (one dir per skill, each with a SKILL.md).
  // Enumerate via the shared helper (frontmatter `name` ?? dir name) — this
  // is genuinely the owning agent's list.
  setSkillsProvider(() => listSkillNames(path.join(piAgentDir, 'skills')))

  // SDK-shipped board resources → ~/.aimail/systems/*/board/ (idempotent;
  // never overwrites user-personalized files). Covers pi-only machines that
  // never install the Python SDK/CLI.
  try {
    releaseAllSystems(path.join(packageResourcesRoot(), 'board'))
  } catch {
    // non-fatal seed; re-released on next start
  }

  // SDK-shipped skill → ~/.pi/agent/skills/agentmail/ (idempotent;
  // identical-content skip). SKILL.md owns the inbound-message protocol
  // (6-step flow) — a different category from registerTool semantics
  // (tool usage). Symmetric across openclaw/dsh/pi.
  try {
    const skillSrc = path.join(packageResourcesRoot(), 'skills')
    const skillDst = path.join(os.homedir(), '.pi', 'agent', 'skills', 'agentmail')
    fs.mkdirSync(skillDst, { recursive: true })
    for (const f of ['SKILL.md', 'DESCRIPTION.md']) {
      const from = path.join(skillSrc, f)
      const to = path.join(skillDst, f)
      if (!fs.existsSync(from)) {
        // missing package resources = packaging defect: loud but must not block the host
        console.error(`[agent-aimail] skill resource missing: ${from} ` +
          '(repo: stage it via the package prepack; installed: reinstall the package)')
        continue
      }
      if (fs.existsSync(to) && fs.readFileSync(from).equals(fs.readFileSync(to))) continue
      fs.copyFileSync(from, to)
    }
    installActivationSkillSync(path.join(os.homedir(), '.pi', 'agent', 'skills'))
  } catch (e) {
    // non-fatal (next plugin start retries), but never silent
    console.error(`[agent-aimail] skill release failed: ${String(e)}`)
  }
  const log = {
    info: (m: string) => console.log(m),
    warn: (m: string) => console.warn(m),
    error: (m: string) => console.error(m),
  }
  log.info(`[agent-aimail] identity ${agentIdentity()}`)

  // ── 15 mail/board tools (bare names, MAIL_TOOLS single source) ──
  const registeredToolNames: string[] = []
  for (const tool of buildPiTools()) {
    pi.registerTool({
      name: tool.name,
      label: tool.label,
      description: tool.description,
      // REQ-11: parameters are plain JSON Schema; pi's ToolDefinition type
      // constrains TSchema (TypeBox). The runtime consumes JSON Schema, so
      // this boundary cast does not pull typebox into the package.
      parameters: tool.parameters as never,
      async execute (toolCallId, params, signal, _onUpdate, ctx) {
        void toolCallId
        void signal
        void ctx
        return tool.execute(toolCallId, params, signal, ctx)
      },
    })
    registeredToolNames.push(tool.name)
  }
  log.info(`[agent-aimail] mail tools: ${registeredToolNames.length}`)
  // TOOLSET_LIST: host actual state (same semantics as SOUL/SKILLS). Live
  // introspection via the host's own ExtensionAPI.getActiveTools() (the tool
  // set declared to the model), intersected with what THIS adapter
  // registered — so the report shrinks if the host narrows the active set,
  // and never claims a tool the host does not actually expose. Host API
  // unavailable (session not ready) ⇒ [] (no constant fallback).
  setToolsetProvider(() => {
    try {
      const active = new Set(pi.getActiveTools())
      return registeredToolNames.filter(n => active.has(n))
    } catch {
      return []
    }
  })

  // ── Inbound: SSE / pull only ──
  //
  // There is no local listener any more (retired 2026-11-25): SSE-V2 receives mail by pulling the
  // gateway stream, so pi binds no port, exposes no HMAC push endpoint and reports no "inbound live"
  // URL to the CLI. The deprecated `transport`/`inboundPort` options are accepted and ignored so an
  // older host config does not crash — they cannot resurrect the listener.
  let pullHandles: AgentPullHandle[] = []
  void startInboundPull(pi, { log })
    .then((handles) => {
      pullHandles = handles
    })
    .catch((e: unknown) =>
      log.warn(`[agent-aimail] SSE pull failed to start: ${e instanceof Error ? e.message : String(e)}`),
    )
  log.info('[agent-aimail] SSE pull armed (no listener, no inbound endpoint)')


  // Hot-add: an address activation after the session started must arm its
  // pull loop without requiring a Pi restart.
  const offBinding = onBindingCreated(async () => {
    for (const h of pullHandles) h.stop()
    pullHandles = []
    try { pullHandles = await startInboundPull(pi, { log }) } catch { /* next restart retries */ }
  })

  // Pull lifecycle follows the session (the loops stop with it).
  pi.on('session_shutdown', () => {
    offBinding()
    for (const h of pullHandles) h.stop()
    pullHandles = []
  })

  log.info('[agent-aimail] registered 14 mail tools + SSE pull (no inbound listener)')
}

