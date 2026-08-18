/**
 * @agentmail/mail-inbound — AgentMail inbound endpoint.
 *
 * node:http listener (headless-friendly). Receives bridge-forwarded raw
 * webhook bodies at POST {path} (default /agentmail/deliver):
 *   HMAC verify (X-Webhook-Signature vs webhook_secret from agentmail.json)
 *   → TS preprocess chain (mail-core, DSH-PREPROCESS-CONTRACT.md)
 *   → ping/pong intercept (three-stage logs, swallowed)
 *   → un-intercepted: followup the bound dsh session (live agent), or
 *     resume persisted session when cold; 200 ack either way.
 */
import { createServer, type IncomingMessage, type ServerResponse } from 'node:http'
import type { Context } from '@deepseek-ai/cordis'
import type { Agent } from '@deepseek-ai/dsh-agent'
import { createUserMessage } from '@deepseek-ai/dsh-llm'
import { processInboundMail, verifySignature, type InboundPayload } from '@agentmail/mail-core'
import type { MailService } from '@agentmail/mail'

export const name = 'mail-inbound'
export const inject = ['mail', 'agents']

export interface Config {
  /** Listen host (default 127.0.0.1). */
  host?: string
  /** Listen port (default AMAIL_INBOUND_PORT or 9099). */
  port?: number
  /** Deliver path (default /agentmail/deliver). */
  path?: string
}

interface DeliveryOutcome {
  status: string
  detail?: string
}

function writeJson(res: ServerResponse, code: number, body: DeliveryOutcome): void {
  const text = JSON.stringify(body)
  res.writeHead(code, { 'Content-Type': 'application/json' })
  res.end(text)
}

function readBody(req: IncomingMessage): Promise<Buffer> {
  return new Promise((resolve, reject) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => resolve(Buffer.concat(chunks)))
    req.on('error', reject)
  })
}

/** Pick the agent's own address from payload recipients (domain match). */

export function apply(ctx: Context, config: Config = {}): () => void {
  const mail = ctx.get('mail') as MailService | undefined
  if (mail === undefined) {
    throw new Error('mail-inbound requires the mail service: mount @agentmail/mail first')
  }
  const host = config.host ?? '127.0.0.1'
  const port = config.port ?? Number(process.env.AMAIL_INBOUND_PORT ?? 9099)
  const deliverPath = config.path ?? '/agentmail/deliver'

  const server = createServer(async (req, res) => {
    try {
      if (req.method !== 'POST' || (req.url ?? '').split('?')[0] !== deliverPath) {
        writeJson(res, 404, { status: 'not_found' })
        return
      }
      const rawBody = await readBody(req)
      let payload: InboundPayload
      try {
        payload = JSON.parse(rawBody.toString('utf-8')) as InboundPayload
      } catch {
        writeJson(res, 400, { status: 'bad_json' })
        return
      }

      // Resolve binding by recipient address (agent's own domain)
      const toRaw = Array.isArray(payload.to) ? payload.to : typeof payload.to === 'string' ? [payload.to] : []
      let cfg
      let agentAddr = ''
      for (const t of toRaw) {
        const addr = String(t).trim()
        if (!addr.includes('@')) continue
        try {
          const c = await mail.resolveByEmail(addr)
          if (c) {
            cfg = c
            agentAddr = addr
            break
          }
        } catch {
          /* not this address */
        }
      }
      if (!cfg) {
        writeJson(res, 200, { status: 'no_agent', detail: `no binding for ${toRaw.join(',')}` })
        return
      }

      // HMAC verify (per-address webhook_secret)
      const sig = (req.headers['x-webhook-signature'] as string) ?? ''
      if (!verifySignature(rawBody, sig, cfg.webhook_secret ?? '')) {
        writeJson(res, 401, { status: 'bad_signature' })
        return
      }

      // TS preprocess chain (13 steps) + ping/pong intercept
      const headers = { ...(req.headers as Record<string, string | string[]>), ...(payload.headers ?? {}) }
      const result = await processInboundMail(payload, headers as Record<string, string>, {
        systemId: cfg.system_id,
        email: cfg.email,
      })
      if (result === null) {
        writeJson(res, 200, { status: 'intercepted' })
        return
      }

      // Deliver to the bound session (live followup or cold resume)
      const sessionId = cfg.session_id ?? ''
      if (!sessionId) {
        writeJson(res, 200, { status: 'no_session', detail: `no session_id binding for ${cfg.email}` })
        return
      }
      const message = createUserMessage({
        content: [{ type: 'text', text: JSON.stringify({ ...result, to: agentAddr }) }],
        source: { kind: 'user' },
      })

      const agents = ctx.get('agents') as { get(id: unknown): Agent | undefined; resume(opts: unknown): Promise<{ agent: Agent }> } | undefined
      if (agents === undefined) {
        writeJson(res, 200, { status: 'no_agents_service', detail: 'dsh-agent not mounted' })
        return
      }
      const live = agents.get(sessionId)
      if (live) {
        live.followup(message)
        writeJson(res, 200, { status: 'delivered', detail: 'followup queued' })
        return
      }
      // cold: resume persisted session (requires sessionPersistence configured)
      try {
        const handle = await agents.resume({ resumeSessionId: sessionId })
        handle.agent.followup(message)
        writeJson(res, 200, { status: 'resumed', detail: 'cold session resumed + followup queued' })
      } catch (e) {
        writeJson(res, 200, {
          status: 'no_live_agent',
          detail: e instanceof Error ? e.message : String(e),
        })
      }
    } catch (e) {
      writeJson(res, 500, { status: 'error', detail: e instanceof Error ? e.message : String(e) })
    }
  })

  server.listen(port, host)
  return () => {
    server.close()
  }
}
