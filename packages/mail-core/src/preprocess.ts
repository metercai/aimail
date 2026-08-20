/**
 * Inbound preprocess chain (TS) — line-by-line contract baseline:
 * DSH-PREPROCESS-CONTRACT.md (agentmail repo). Mirrors Python
 * preprocess_mail_payload / process_inbound_mail / handle_ping_pong.
 *
 * dsh differences (explicit in contract): no persona (PERSONA_SUPPORTED=false
 * → my_amail_addr = base email); no raw snapshot; log events MUST be kept.
 */
import { promises as fsp } from 'node:fs'
import * as path from 'node:path'
import { createHmac } from 'node:crypto'
import { GatewayClient } from './gateway.js'
import { AIMAIL_HOME, cleanAddr, loadAgentConfig } from './config.js'
import { sendMail, sanitizeMessageId } from './tools.js'
import { registerBoardGateway } from './board.js'
import type { AgentConfig, EnrichedPayload, InboundPayload } from './types.js'
import type { ToolCtx } from './tools.js'

// ── ping/pong contract (never diverge) ─────────────────────────

export const PING_PREFIX = '__agentmail_ping__:'
export const PONG_PREFIX = '__amail_pong__:'

// ── logs ───────────────────────────────────────────────────────

function logPath(email: string): string {
  return path.join(AIMAIL_HOME(), 'logs', `agentmail.${cleanAddr(email)}.log`)
}

async function appendLog(email: string, entry: Record<string, unknown>): Promise<void> {
  try {
    const p = logPath(email)
    await fsp.mkdir(path.dirname(p), { recursive: true })
    await fsp.appendFile(p, JSON.stringify({ ts: new Date().toISOString(), ...entry }) + '\n', 'utf-8')
  } catch {
    /* non-fatal */
  }
}

/** Three-stage ping event log (ping_intercepted / pong_sent / pong_returned). */
export async function logPingEvent(
  dir: string,
  pingId: string,
  payload: InboundPayload,
  pongStatus = '',
): Promise<void> {
  const to = payload.to
  const first = Array.isArray(to) ? to[0] : to
  const entry: Record<string, unknown> = {
    dir,
    ping_id: pingId,
    from: payload.from ?? '',
    to: first ?? '',
  }
  if (pongStatus) entry.pong_status = pongStatus
  const email = typeof first === 'string' && first.includes('@') ? first : (payload.from as string) || 'unknown'
  await appendLog(email, entry)
}

/** Lightweight inbound log (mirror _log_amail("inbound", ...)). */
export async function logAmailInbound(email: string, from: string, to: string, subject: string): Promise<void> {
  await appendLog(email, { event: 'inbound', from, to, subject })
}

// ── address helpers (mirror parse_amail_persona / base_email) ──

export function parseAmailPersona(email: string, systemName = ''): { persona: string; profile: string; sysName: string } {
  const local = email.includes('@') ? email.split('@')[0] ?? '' : email
  const parts = local.split('.')

  // short form: sys_name@domain → (default agent)
  if (systemName && parts.length === 1 && parts[0] === systemName) {
    return { persona: '', profile: 'default', sysName: systemName }
  }
  // three-part: persona.profile.sys_name@domain
  if (systemName && parts.length >= 2 && parts[parts.length - 1] === systemName) {
    const profileParts = parts.slice(0, -1)
    if (profileParts.length >= 2) {
      const profile = profileParts[profileParts.length - 1] ?? ''
      return {
        persona: profileParts.slice(0, -1).join('.'),
        profile,
        sysName: systemName,
      }
    }
    return { persona: '', profile: profileParts[0] ?? '', sysName: systemName }
  }
  // traditional two-part: persona.profile@domain
  if (parts.length >= 2) {
    const profile = parts[parts.length - 1] ?? ''
    return { persona: parts.slice(0, -1).join('.'), profile, sysName: '' }
  }
  return { persona: '', profile: parts[0] ?? '', sysName: '' }
}

/** Strip persona prefix: support.alice@agent.com → alice@agent.com */
export function baseEmail(email: string, systemName: string): string {
  const p = parseAmailPersona(email, systemName)
  const domain = email.includes('@') ? email.split('@', 2)[1] ?? '' : ''
  return p.sysName ? `${p.profile}.${p.sysName}@${domain}` : `${p.profile}@${domain}`
}

// ── header parsing (mirror _parse_header_addrs) ────────────────

function parseHeaderAddrs(headerVal: string): Array<{ name: string; email: string }> {
  const out: Array<{ name: string; email: string }> = []
  for (const part of headerVal.split(',')) {
    const p = part.trim()
    if (!p) continue
    const m = /<([^>]+)>/.exec(p)
    if (m) {
      const m1 = m[1]
      if (!m1) continue
      const email = m1.trim().toLowerCase()
      const nm = /^(.+?)\s*</.exec(p)
      const n1 = nm ? nm[1] : undefined
      const name = n1 ? n1.trim() : email.split('@')[0] ?? ''
      out.push({ name, email })
    } else if (p.includes('@')) {
      const email = p.trim().toLowerCase()
      out.push({ name: email.split('@')[0] ?? '', email })
    }
  }
  return out
}

function toList(v: unknown): string[] {
  if (Array.isArray(v)) return v.filter((s): s is string => typeof s === 'string' && s.trim() !== '').map(s => s.trim())
  if (typeof v === 'string') return v.split(',').map(s => s.trim()).filter(Boolean)
  return []
}

// ── attachment download (mirror preprocess steps 10-11) ────────

async function downloadAttachments(
  client: GatewayClient,
  attachments: InboundPayload['attachments'],
  messageId: string,
  cfg: AgentConfig,
): Promise<string[]> {
  if (!attachments?.length) return []
  const attchDir = path.join(AIMAIL_HOME(), 'mail', cleanAddr(cfg.email), new Date().toISOString().slice(0, 7).replace('-', ''), 'attch', sanitizeMessageId(messageId || 'unknown'))
  await fsp.mkdir(attchDir, { recursive: true })
  const localPaths: string[] = []
  for (const att of attachments) {
    if (typeof att !== 'object' || att === null) continue
    const attId = (att.attachment_id as string) || (att.id as string) || ''
    const fname = (att.filename as string) || (att.name as string) || 'unnamed_attachment'
    if (!attId) continue
    const content = await client.downloadAttachment(attId)
    if (!content) continue
    const safeName = path.basename(fname) || 'unnamed_attachment'
    const localPath = path.join(attchDir, safeName)
    await fsp.writeFile(localPath, content)
    localPaths.push(localPath)
    // markdown conversion for binary docs (docx/xlsx/html/htm) — via external
    // helper when available; keep original otherwise (mirror markitdown best-effort)
    const ext = path.extname(fname).toLowerCase()
    if (['.docx', '.xlsx', '.html', '.htm'].includes(ext)) {
      try {
        const mdText = await convertToMarkdown(localPath)
        if (mdText.trim()) {
          const mdPath = path.join(attchDir, `${path.basename(fname, path.extname(fname))}.md`)
          await fsp.writeFile(mdPath, mdText, 'utf-8')
          localPaths.push(mdPath)
        }
      } catch {
        /* keep original */
      }
    }
  }
  return localPaths
}

/** Best-effort doc→markdown; resolved at runtime so the dep stays optional. */
async function convertToMarkdown(filePath: string): Promise<string> {
  try {
    const { convert } = await import('./doc-convert.js')
    return await convert(filePath)
  } catch {
    return ''
  }
}

// ── ping/pong handling ─────────────────────────────────────────

async function sendPong(cfg: AgentConfig, payload: InboundPayload, pingId: string): Promise<void> {
  try {
    await sendMail(
      { systemId: cfg.system_id, email: cfg.email },
      {
        to: (payload.from as string) || '',
        subject: `${PONG_PREFIX}${pingId}`,
        body: '',
        message_id: payload.message_id as string,
      },
    )
  } catch {
    /* pong best-effort */
  }
}

/**
 * Full inbound chain: preprocess → ping/pong intercept (LAST).
 * Returns enriched payload, or null when intercepted (swallow, no agent run).
 */
export async function processInboundMail(
  payload: InboundPayload,
  _headers: Record<string, string>,
  ctx: ToolCtx,
): Promise<EnrichedPayload | null> {
  // board gateway registry extraction (.a2a@ / [A2A] mails)
  const fromRaw = payload.from ?? ''
  const subjectRaw = payload.subject ?? ''
  const bodyRaw = payload.body ?? ''
  if (fromRaw.includes('.a2a@') || subjectRaw.startsWith('[A2A]')) {
    const gwMatch = /API:\s*(https?:\/\/\S+)/.exec(bodyRaw)
    const tokenMatch = /Token:\s*(bdt_\S+)/.exec(bodyRaw)
    if (gwMatch && gwMatch[1]) {
      const boardIdMatch = /board[-_]?id[\s:]+(\w+)/i.exec(bodyRaw)
      const bid = boardIdMatch?.[1] ?? ''
      if (bid) registerBoardGateway(bid, gwMatch[1])
      // token persistence (board_creds.json) when token present
      if (tokenMatch?.[1] && ctx.systemId && ctx.email) {
        try {
          const credsPath = path.join(AIMAIL_HOME(), 'systems', ctx.systemId, cleanAddr(ctx.email), 'board_creds.json')
          const existing = JSON.parse(await fsp.readFile(credsPath, 'utf-8').catch(() => '{}')) as Record<string, unknown>
          existing[bid] = { token: tokenMatch[1] }
          await fsp.writeFile(credsPath, JSON.stringify(existing, null, 2) + '\n', { mode: 0o600 })
        } catch {
          /* non-fatal */
        }
      }
    }
  }

  // ── preprocess (contract steps 1-13) ──
  const cfg = await loadAgentConfig(ctx.systemId, ctx.email ?? '')
  const agentEmail = cfg?.email ?? ''
  const systemName = cfg?.system_name ?? ''

  if (!agentEmail || !cfg) {
    return {
      ...payload,
      my_amail_addr: '',
      direct_message: false,
      mentioned: false,
      _preprocess_error: 'agentmail email not configured',
    } as unknown as EnrichedPayload
  }

  let result: Record<string, unknown> = { ...payload }

  // step 4-6: header display names → recipients/sender
  const rawHeaders = (payload.headers ?? {}) as Record<string, string>
  const toNamed = parseHeaderAddrs(rawHeaders['to'] ?? '')
  const ccNamed = parseHeaderAddrs(rawHeaders['cc'] ?? '')
  const fmt = (n: string, e: string): string => (n ? `${n} <${e}>` : e)

  const toRaw = toList(payload.to)
  const ccRaw = toList(payload.cc)

  result.recipients = {
    to: toNamed.length ? toNamed.map(x => fmt(x.name, x.email)) : toRaw,
    cc: ccNamed.length ? ccNamed.map(x => fmt(x.name, x.email)) : ccRaw,
  }

  const fromNamed = parseHeaderAddrs(rawHeaders['from'] ?? '')
  if (fromNamed.length) {
    const f0 = fromNamed[0]
    if (f0) result.sender = fmt(f0.name, f0.email)
  }

  const toBare = toNamed.length ? toNamed.map(x => x.email) : toRaw.map(a => a.toLowerCase())
  const ccBare = ccNamed.length ? ccNamed.map(x => x.email) : ccRaw.map(a => a.toLowerCase())

  // step 7-8: persona (dsh: PERSONA_SUPPORTED=false → normalized base)
  const agentDomain = agentEmail.includes('@') ? agentEmail.split('@', 2)[1] ?? '' : ''
  let myToAddr = ''
  for (const addr of toBare) {
    if (agentDomain && addr.endsWith(`@${agentDomain}`)) {
      myToAddr = addr
      break
    }
  }
  const persona = myToAddr ? parseAmailPersona(myToAddr, systemName).persona : ''
  if (persona) {
    // PERSONA_SUPPORTED=false: normalize recipient to base address
    // (strip persona prefix, no config validation) — dsh contract
    result.my_amail_addr = agentEmail
  } else {
    result.my_amail_addr = myToAddr || agentEmail
  }

  // step 9: direct_message / mentioned
  const agentBase = baseEmail(agentEmail, systemName)
  const allBare = [...toBare, ...ccBare]
  const allBase = allBare.map(a => baseEmail(a, systemName))
  result.direct_message = toBare.length === 1 && ccBare.length === 0 && allBase[0] === agentBase

  const agentLocal = agentEmail.split('@')[0] ?? ''
  let agentDisplay = ''
  for (const x of [...toNamed, ...ccNamed]) {
    if (baseEmail(x.email, systemName) === agentBase && x.name) {
      agentDisplay = x.name
      break
    }
  }
  const p = parseAmailPersona(myToAddr, systemName)
  const matchTargets = [agentLocal, p.profile].filter(Boolean)
  if (agentDisplay) matchTargets.push(agentDisplay)
  const bodyLower = (bodyRaw ?? '').toLowerCase()
  result.mentioned = matchTargets.some(t => t && (bodyLower.includes(`@${t.toLowerCase()}`) || bodyLower.split(/\s+/).includes(t.toLowerCase())))

  // step 10-11: attachments
  if (payload.attachments?.length && cfg.api_key) {
    const client = new GatewayClient(cfg.gateway_url, cfg.api_key)
    const paths = await downloadAttachments(client, payload.attachments, (payload.message_id as string) ?? '', cfg)
    result.attachments = paths
  }

  // step 12: strip backend-only fields
  const stripFields = new Set(['mail_id', 'to', 'cc', 'headers', 'created_at', 'forwarder', 'forward_at'])
  const cleaned: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(result)) {
    if (!stripFields.has(k)) cleaned[k] = v
  }
  result = cleaned

  // step 13: inbound log
  const mid = (result.message_id as string) ?? ''
  const myAddr = result.my_amail_addr as string
  if (mid && myAddr) {
    const fromHdr = rawHeaders['from'] ?? (payload.from as string) ?? ''
    const subjHdr = rawHeaders['subject'] ?? subjectRaw
    await logAmailInbound(cfg.email, fromHdr, myAddr, subjHdr)
  }

  // board extras: [WHOAMI] / board_id+board_role
  const subj = subjectRaw.trim()
  if (subj.toUpperCase().startsWith('[WHOAMI]')) {
    result._whoami_update_public = true
  }
  const boardId = payload.board_id
  const boardRole = payload.board_role
  if (boardId && boardRole) {
    result._a2a_session_key = `a2a:${boardId}:${payload.from ?? ''}`
  }

  // ── LAST: ping/pong interception ──
  if (subj.startsWith(PING_PREFIX)) {
    const pingId = subj.split(':', 1)[1]?.trim() ?? subj.slice(PING_PREFIX.length).trim()
    await logPingEvent('ping_intercepted', pingId, payload)
    await sendPong(cfg, payload, pingId)
    await logPingEvent('pong_sent', pingId, payload, 'sent')
    return null
  }
  if (subj.startsWith(PONG_PREFIX)) {
    const pingId = subj.split(':', 1)[1]?.trim() ?? subj.slice(PONG_PREFIX.length).trim()
    await logPingEvent('pong_returned', pingId, payload)
    return null
  }
  return result as unknown as EnrichedPayload
}

/** HMAC verify (X-Webhook-Signature, hex sha256 of raw body with webhook_secret). */
export function verifySignature(rawBody: Buffer | string, signature: string, secret: string): boolean {
  if (!signature || !secret) return false
  const expected = createHmac('sha256', secret).update(rawBody).digest('hex')
  return signature.length === expected.length && createHmac('sha256', secret).update(rawBody).digest('hex') === signature
}
