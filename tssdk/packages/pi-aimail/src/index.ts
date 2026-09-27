/**
 * pi-aimail — AIMail extension for pi (earendil-works/pi).
 *
 * Same capability surface as dsh-aimail / openclaw-aimail, adapted to pi's
 * extension API:
 * - 15 mail/board tools via MAIL_TOOLS (single TS semantic source), bare
 *   names, registered with pi.registerTool (TypeBox parameters).
 * - Inbound receiver: pi has no HTTP route registration, so the extension
 *   owns a local listener (127.0.0.1:9101 by default) that the bridge pushes
 *   to; the handler runs HMAC verify → processInboundMail (13-step + ping/
 *   pong intercept) → pi.sendUserMessage (always triggers a turn).
 * - Identity: ~/.pi/.agentmail pointer ({system_id, email}) is the sole
 *   trust source; outbound X-AIMail-Agent: pi/<detected host version>.
 *
 * Install: copy/symlink into ~/.pi/agent/extensions/ (or ship as a pi
 * package). Binding: create ~/.pi/.agentmail with {system_id, email}.
 */
import * as fs from 'node:fs'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { ensureSystem, ensureBridgeRoutesForSystem, formatBridgeRouteLine, isBridgeRouteWarning, processInboundMail, releaseAllSystems, routeAddressFromHeaders, startAgentPullEntries, verifySignature, INBOUND_PATH, INBOUND_PORTS, type AgentConfig, type AgentPullHandle, type AgentPullOverrides, type InboundPayload } from '@aimail/mail-core'
import { resolveByRecipient } from '@aimail/mail'
import { agentIdentity, initIdentity, readPointer, setInboundEndpoint } from './identity.js'
import { buildPiTools } from './tools.js'

// 契约常量唯一副本 = @aimail/mail-core(src/contract.ts ← contract/aimail-contract.json)。
// 这里 re-export 保持本模块既有公开面(dist/index.d.ts 曾导出 INBOUND_PATH)。
export { INBOUND_PATH }
const DEFAULT_INBOUND_PORT = INBOUND_PORTS.pi

export interface PiAimailOptions {
  /** Inbound listener port (default 9101). */
  inboundPort?: number
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
  if (!cfg) {
    const ptr = readPointer()
    if (ptr.email) {
      try {
        cfg = await resolveByRecipient(ptr.email)
        if (cfg && !agentAddr) agentAddr = ptr.email
      } catch { /* fallthrough */ }
    }
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
  return { status: 'delivered', ok: true }
}

/**
 * Pull entry (agent-scope bindings only) — the missing production wire for the
 * address-code flow: an address activated by an activation CODE has no push
 * path (the gateway stores webhook_url=NULL for it), so this long-lived session
 * fetches its own mail on a timer. Pulled mail enters `deliverPiInbound`: the
 * very same chain pushed mail takes. Never started for system/bridge bindings —
 * the decision lives in mail-core (`startAgentPullEntries`).
 */
export async function startInboundPull(
  pi: ExtensionAPI,
  opts: {
    log?: { info: (m: string) => void; warn: (m: string) => void; error: (m: string) => void }
    env?: NodeJS.ProcessEnv
    overrides?: AgentPullOverrides
    systemId?: string
  } = {},
): Promise<AgentPullHandle[]> {
  const ptr = readPointer()
  return await startAgentPullEntries({
    systemId: opts.systemId ?? ptr.system_id ?? '',
    ...(opts.log ? { log: opts.log.info } : {}),
    ...(opts.env ? { env: opts.env } : {}),
    ...(opts.overrides ? { overrides: opts.overrides } : {}),
    onEmail: async (cfg, pulled) => {
      if (typeof pulled.body !== 'object' || pulled.body === null) {
        throw new Error(
          `pulled delivery ${pulled.id} for ${cfg.email} carries no JSON payload — not acking`,
        )
      }
      // The delivery's own address is authoritative for routing (it is what the
      // agent-scope key was verified against server-side).
      const headers: Record<string, unknown> = {
        ...(pulled.headers ?? {}),
        'x-aimail-email': pulled.email,
      }
      const target = await resolvePiTarget(pulled.body as InboundPayload, headers)
      if (!target) {
        throw new Error(`pulled delivery ${pulled.id}: no binding for ${pulled.email} — not acking`)
      }
      const out = await deliverPiInbound(pi, target, pulled.body as InboundPayload, headers)
      if (!out.ok) {
        throw new Error(
          `pulled delivery ${pulled.id}: inbound chain did not deliver (${out.status}) — not acking`,
        )
      }
    },
  })
}

export default function piAimail (pi: ExtensionAPI, options: PiAimailOptions = {}) {
  initIdentity()
  // SDK-shipped board resources → ~/.aimail/systems/*/board/ (idempotent;
  // never overwrites user-personalized files). Covers pi-only machines that
  // never install the Python SDK/CLI.
  try {
    releaseAllSystems(path.join(path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'board'))
  } catch {
    // non-fatal seed; re-released on next start
  }

  // SDK-shipped skill → ~/.pi/agent/skills/agentmail/ (idempotent;
  // identical-content skip). SKILL.md owns the inbound-message protocol
  // (6-step flow) — a different category from registerTool semantics
  // (tool usage). Symmetric across openclaw/dsh/pi.
  try {
    const skillSrc = path.join(
      path.dirname(fileURLToPath(import.meta.url)), '..', 'resources', 'skills')
    const skillDst = path.join(os.homedir(), '.pi', 'agent', 'skills', 'agentmail')
    fs.mkdirSync(skillDst, { recursive: true })
    for (const f of ['SKILL.md', 'DESCRIPTION.md']) {
      const from = path.join(skillSrc, f)
      const to = path.join(skillDst, f)
      if (!fs.existsSync(from)) {
        // missing package resources = packaging defect: loud but must not block the host
        console.error(`[pi-aimail] skill resource missing: ${from} ` +
          '(repo: run scripts/materialize-resources.sh; installed: reinstall the package)')
        continue
      }
      if (fs.existsSync(to) && fs.readFileSync(from).equals(fs.readFileSync(to))) continue
      fs.copyFileSync(from, to)
    }
  } catch (e) {
    // non-fatal (next plugin start retries), but never silent
    console.error(`[pi-aimail] skill release failed: ${String(e)}`)
  }
  const log = {
    info: (m: string) => console.log(m),
    warn: (m: string) => console.warn(m),
    error: (m: string) => console.error(m),
  }
  log.info(`[pi-aimail] identity ${agentIdentity()}`)

  // Install readiness: system activation lives ONCE, in `aimail
  // install --system-only` (L1 only) — reverse-call it when THIS platform has no
  // owning system yet. Never platform wiring → acyclic call graph.
  {
    const platformHome =
      process.env.AIMAIL_SYSTEM_HOME?.trim() ||
      path.join(os.homedir(), '.pi')
    void ensureSystem({ systemHome: platformHome })
      .then((r) => {
        if (r.ok) {
          if (r.activated) log.info(`[pi-aimail] system activated: ${r.systemId}`)
        } else {
          const hint = r.hint ? ` (${r.hint})` : ''
          log.warn(`[pi-aimail] no aimail system yet — ${r.error ?? 'unknown'}` + hint)
        }
      })
      .catch((e) => {
        log.warn(`[pi-aimail] system ensure failed: ${e instanceof Error ? e.message : String(e)}`)
      })
  }

  // ── 15 mail/board tools (bare names, MAIL_TOOLS single source) ──
  for (const tool of buildPiTools()) {
    pi.registerTool({
      name: tool.name,
      label: tool.label,
      description: tool.description,
      parameters: tool.parameters,
      async execute (toolCallId, params, signal, _onUpdate, ctx) {
        void toolCallId
        void signal
        void ctx
        return tool.execute(toolCallId, params, signal, ctx)
      },
    })
  }

  // ── Inbound receiver (local listener; bridge push target) ──
  let server: http.Server | undefined
  let pullHandles: AgentPullHandle[] = []
  const startInbound = () => {
    if (server) return
    server = http.createServer((req, res) => {
      const json = (code: number, body: unknown) => {
        res.writeHead(code, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify(body))
      }
      void (async () => {
        try {
          if (req.method !== 'POST') {
            json(404, { status: 'not_found' })
            return
          }
          const chunks: Buffer[] = []
          for await (const c of req) chunks.push(c as Buffer)
          const rawBody = Buffer.concat(chunks)
          let payload: InboundPayload
          try {
            payload = JSON.parse(rawBody.toString('utf-8')) as InboundPayload
          } catch {
            json(400, { status: 'bad_json' })
            return
          }
          const headers = {
            ...(req.headers as Record<string, string | string[]>),
            ...((payload.headers ?? {}) as Record<string, unknown>),
          } as Record<string, unknown>
          const target = await resolvePiTarget(payload, headers)
          if (!target) {
            json(200, { status: 'no_agent', detail: 'no binding' })
            return
          }
          // HMAC verify — push-only trust step (pulled mail is authenticated by
          // the gateway on the agent-scope key); same chain below.
          const sig = String(req.headers['x-webhook-signature'] ?? '')
          if (!verifySignature(rawBody, sig, target.cfg.webhook_secret ?? '')) {
            json(401, { status: 'bad_signature' })
            return
          }
          const out = await deliverPiInbound(pi, target, payload, headers)
          json(200, { status: out.status })
        } catch (e) {
          log.error(`[pi-aimail] inbound error: ${e instanceof Error ? e.message : String(e)}`)
          json(500, { status: 'error', detail: e instanceof Error ? e.message : String(e) })
        }
      })()
    })
    const port = options.inboundPort ?? DEFAULT_INBOUND_PORT
    // Tell identity where inbound actually listens (auto-bind registers this
    // URL as the address webhook + bridge route target).
    setInboundEndpoint(`http://127.0.0.1:${port}${INBOUND_PATH}`)
    server.listen(port, '127.0.0.1', () => {
      log.info(`[pi-aimail] inbound listening on http://127.0.0.1:${port}${INBOUND_PATH}`)
      // 铁律(2026-08-18 用户强调): 有 bridge 时每个 agent 必须有路由 —— 桥的健康
      // 检查会在目标连续不可达(默认 30s × 6 = 180s)后**正确删除**该路由, 而删除后
      // 此前无人补写 ⇒ 宿主长时间停机/重启后入站**永久断链**(2026-09-21 生产实测)。
      // 故在**监听就绪之后**(重启末端)幂等 upsert: 路由存在与否始终反映"宿主此刻
      // 是否真在服务", 既不误判正常重启窗口, 也不留死路由。
      // 2026-09-27 收口: 该时序规则实现为 mail-core.ensureBridgeRoutesForSystem
      // (openclaw/dsh 共用同一实现), 路由目标取各绑定自己的 webhook_url。
      void ensureBridgeRoutesForSystem()
        .then((outcomes) => {
          for (const o of outcomes) {
            const line = `[pi-aimail] ${formatBridgeRouteLine(o)}`
            if (isBridgeRouteWarning(o)) log.warn(line)
            else log.info(line)
          }
        })
        .catch((e: unknown) => {
          log.warn(
            `[pi-aimail] bridge route ensure failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`,
          )
        })

      // Pull entry (2026-09-27): an address activated by an activation CODE has
      // no push path (the gateway stores webhook_url=NULL for it), so this
      // long-lived session fetches its own mail on a timer, delivering through
      // the SAME chain (`deliverPiInbound`). Armed only for agent-scope
      // bindings — the decision lives in mail-core, never here.
      void startInboundPull(pi, { log })
        .then((handles) => {
          pullHandles = handles
        })
        .catch((e: unknown) => {
          log.warn(`[pi-aimail] pull entry failed to start: ${e instanceof Error ? e.message : String(e)}`)
        })
    })
    server.on('error', (e) => {
      log.error(`[pi-aimail] inbound listener error: ${e.message}`)
    })
  }
  startInbound()

  // Listener lifecycle follows the session (pull loops stop with it).
  pi.on('session_shutdown', () => {
    for (const h of pullHandles) h.stop()
    pullHandles = []
    server?.close()
    server = undefined
  })

  log.info('[pi-aimail] registered 15 mail tools + local inbound receiver')
}
