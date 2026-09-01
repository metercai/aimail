/**
 * pi-aimail — AgentMail extension for pi (earendil-works/pi).
 *
 * Same capability surface as dsh-aimail / openclaw-aimail, adapted to pi's
 * extension API:
 * - 12 mail/board tools via MAIL_TOOLS (single TS semantic source), bare
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
import * as http from 'node:http'
import type { ExtensionAPI } from '@earendil-works/pi-coding-agent'
import { processInboundMail, routeAddressFromHeaders, verifySignature, type InboundPayload } from '@aimail/mail-core'
import { resolveByRecipient } from '@aimail/mail'
import { agentIdentity, initIdentity, readPointer } from './identity.js'
import { buildPiTools } from './tools.js'

export const INBOUND_PATH = '/aimail/inbound'
const DEFAULT_INBOUND_PORT = 9101

export interface PiAimailOptions {
  /** Inbound listener port (default 9101). */
  inboundPort?: number
}

export default function piAimail (pi: ExtensionAPI, options: PiAimailOptions = {}) {
  initIdentity()
  const log = {
    info: (m: string) => console.log(m),
    warn: (m: string) => console.warn(m),
    error: (m: string) => console.error(m),
  }
  log.info(`[pi-aimail] identity ${agentIdentity()}`)

  // ── 12 mail/board tools (bare names, MAIL_TOOLS single source) ──
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
          if (!cfg) {
            json(200, { status: 'no_agent', detail: `no binding for ${routeAddr || toRaw.join(',')}` })
            return
          }
          const sig = String(req.headers['x-webhook-signature'] ?? '')
          if (!verifySignature(rawBody, sig, cfg.webhook_secret ?? '')) {
            json(401, { status: 'bad_signature' })
            return
          }
          const result = await processInboundMail(
            payload,
            headers as Record<string, string>,
            { systemId: cfg.system_id, email: cfg.email },
          )
          if (result === null) {
            json(200, { status: 'intercepted' })
            return
          }
          // Deliver into the running session — always triggers a turn.
          pi.sendUserMessage(JSON.stringify({ ...result, to: agentAddr }), {
            deliverAs: 'steer',
          })
          json(200, { status: 'delivered' })
        } catch (e) {
          log.error(`[pi-aimail] inbound error: ${e instanceof Error ? e.message : String(e)}`)
          json(500, { status: 'error', detail: e instanceof Error ? e.message : String(e) })
        }
      })()
    })
    const port = options.inboundPort ?? DEFAULT_INBOUND_PORT
    server.listen(port, '127.0.0.1', () => {
      log.info(`[pi-aimail] inbound listening on http://127.0.0.1:${port}${INBOUND_PATH}`)
    })
    server.on('error', (e) => {
      log.error(`[pi-aimail] inbound listener error: ${e.message}`)
    })
  }
  startInbound()

  // Listener lifecycle follows the session.
  pi.on('session_shutdown', () => {
    server?.close()
    server = undefined
  })

  log.info('[pi-aimail] registered 12 mail tools + local inbound receiver')
}
