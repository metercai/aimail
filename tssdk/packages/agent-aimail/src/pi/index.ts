/**
 * pi-aimail — AIMail extension for pi (earendil-works/pi).
 *
 * Same capability surface as dsh-aimail / openclaw-aimail, adapted to pi's
 * extension API:
 * - 14 mail/board tools via MAIL_TOOLS (single TS semantic source), bare
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
import { activateHost } from '../hosts/activate.js'
import * as fs from 'node:fs'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'

import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { notifyInboundForSystem, formatInboundNotifyLine, isInboundNotifyWarning, processInboundMail, releaseAllSystems, routeAddressFromHeaders, startAgentPullEntries, setSoulProvider, setSkillsProvider, setToolsetProvider, listSkillNames, verifySignature, INBOUND_PATH, INBOUND_PORTS, type AgentConfig, type AgentPullHandle, type AgentPullOverrides, type InboundPayload, packageResourcesRoot } from '../core/index.js'
import { resolveByRecipient } from '../mail/index.js'
import { agentIdentity, initIdentity, readPointer, setInboundEndpoint, tryAutoBindOnce } from './identity.js'
import { buildPiTools } from './tools.js'

// Contract constants single copy = @aimail/mail-core(src/contract.ts ← contract/aimail-contract.json).
// re-export here to maintain this module's existing public surface (dist/index.d.ts previously exported INBOUND_PATH).
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
  // Host identification (#5): verify environment before activating.
  activateHost('pi')
  initIdentity()

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

  // ── 14 mail/board tools (bare names, MAIL_TOOLS single source) ──
  const registeredToolNames: string[] = []
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
    registeredToolNames.push(tool.name)
  }
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
          log.error(`[agent-aimail] inbound error: ${e instanceof Error ? e.message : String(e)}`)
          json(500, { status: 'error', detail: e instanceof Error ? e.message : String(e) })
        }
      })()
    })
    const port = options.inboundPort ?? DEFAULT_INBOUND_PORT
    // Confirmed inbound URL (contract §4.3): pi binds 127.0.0.1 (concrete), so the
    // serving URL is always known. It is both the address webhook auto-bind
    // registers (setInboundEndpoint) and what the CLI is told at `--inbound-live`
    // so the route can be pinned to the deterministic port.
    const confirmedUrl = `http://127.0.0.1:${port}${INBOUND_PATH}`
    setInboundEndpoint(confirmedUrl)
    // Eager default-agent binding (dsh parity, 2026-10-16 batch): the
    // `pi install`/plugin path never runs the CLI's register_default step, so
    // without a proactive bind the host would LISTEN without the default
    // agent's binding — "inbound port up" would not imply "binding written".
    // Gate the listen on the idempotent self-bind (once-guarded; the lazy
    // tool path shares it). A binding failure degrades: the host still
    // listens (unbound path), it does not wedge.
    void tryAutoBindOnce()
      .then(() => {
        if (!server) return
        server.listen(port, '127.0.0.1', () => {
          log.info(`[agent-aimail] inbound listening on http://127.0.0.1:${port}${INBOUND_PATH}`)
          // Inbound notification (owner decision 2026-09-28, SDK de-bridging): listen ready ⇒ tell environment master per address
          // (the `aimail` CLI) `aimail address -a <addr> --inbound-live`. The substance of the timing is unchanged
          // —— Only holds after "inbound is truly serving"; but **bridge semantics are not in the SDK**: the CLI decides what this means.
          // best-effort: argv only, 4s timeout, does not block this callback, never throws to host (no CLI ⇒ one line of log,
          // host serves normally and self-registers).
          void notifyInboundForSystem('live', { inboundUrl: confirmedUrl })
            .then((outcomes) => {
              for (const o of outcomes) {
                const line = `[agent-aimail] ${formatInboundNotifyLine(o, 'live')}`
                if (isInboundNotifyWarning(o)) log.warn(line)
                else log.info(line)
              }
            })
            .catch((e: unknown) => {
              log.warn(
                `[agent-aimail] inbound notify failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`,
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
              log.warn(`[agent-aimail] pull entry failed to start: ${e instanceof Error ? e.message : String(e)}`)
            })
        })
      })
      // tryAutoBindOnce never throws (failures warn + undefined); a reject
      // here would be a code defect — degrade to the unbound path anyway.
      .catch((e: unknown) => {
        log.warn(`[agent-aimail] eager binding gate failed: ${e instanceof Error ? e.message : String(e)}`)
        if (!server) return
        server.listen(port, '127.0.0.1', () => {
          log.info(`[agent-aimail] inbound listening (unbound) on http://127.0.0.1:${port}${INBOUND_PATH}`)
        })
      })
    server.on('error', (e) => {
      log.error(`[agent-aimail] inbound listener error: ${e.message}`)
    })
  }
  startInbound()

  // Listener lifecycle follows the session (pull loops stop with it).
  pi.on('session_shutdown', () => {
    // Shutdown notification (2026-09-28): per address `aimail address -a <addr> --inbound-down`,
    // also best-effort/non-blocking; CLI absent = nothing happens.
    void notifyInboundForSystem('down')
      .then((outcomes) => {
        for (const o of outcomes) {
          const line = `[agent-aimail] ${formatInboundNotifyLine(o, 'down')}`
          if (isInboundNotifyWarning(o)) log.warn(line)
          else log.info(line)
        }
      })
      .catch((e: unknown) => {
        log.warn(
          `[agent-aimail] inbound down notify failed (non-fatal): ${e instanceof Error ? e.message : String(e)}`,
        )
      })
    for (const h of pullHandles) h.stop()
    pullHandles = []
    server?.close()
    server = undefined
  })

  log.info('[agent-aimail] registered 14 mail tools + local inbound receiver')
}
