/**
 * Inbound preprocess chain (TS) — line-by-line contract baseline:
 * DSH-PREPROCESS-CONTRACT.md (aimail repo). Mirrors Python
 * preprocess_mail_payload / process_inbound_mail / handle_ping_pong.
 *
 * dsh differences (explicit in contract): no persona (PERSONA_SUPPORTED=false
 * → my_aimail_addr = base email); no raw snapshot; log events MUST be kept.
 */
import { promises as fsp } from 'node:fs'
import * as path from 'node:path'
import { createHmac, createHash, timingSafeEqual } from 'node:crypto'
import { GatewayClient } from './gateway.js'
import { AIMAIL_HOME, cleanAddr, loadAgentConfig, systemDir } from './config.js'
import { sendMail, sanitizeMessageId } from './tools.js'
import { appendLog } from './log.js'
import { saveLocalMeta, threadPath } from './meta.js'
import { registerBoardGateway } from './board.js'
import type { AgentConfig, EnrichedPayload, InboundPayload } from './types.js'
import type { ToolCtx, ToolResult } from './tools.js'

// ── inbound routing (mirror Python bridge routing — Q3) ─────────
// The gateway/bridge injects a per-recipient X-AIMail-Email header on each
// forwarded delivery. payload.to is the FILTERED full list (external
// recipients first), so to[0] is often an external address and must NOT
// drive routing. The header is authoritative when present; payload.to is
// only a fallback when the header is absent.

/**
 * Resolve the authoritative inbound route address from HTTP headers.
 * The header is X-AIMail-Email (the only name the gateway/bridge emits;
 * mirrors the Python bridge). Case-insensitive (node lowercases header
 * names; direct callers may pass mixed case). Empty string when absent —
 * the caller then falls back to payload.to.
 */
export function routeAddressFromHeaders(headers: Record<string, unknown>): string {
  const pick = (names: Set<string>): string => {
    for (const [k, v] of Object.entries(headers)) {
      if (!names.has(k.toLowerCase())) continue
      if (typeof v === 'string' && v.trim()) return v.trim()
      if (Array.isArray(v)) {
        for (const x of v) if (typeof x === 'string' && x.trim()) return x.trim()
      }
    }
    return ''
  }
  return pick(new Set(['x-aimail-email']))
}

// ── ping/pong contract (never diverge) ─────────────────────────

export const PING_PREFIX = '__aimail_ping__:'
export const PONG_PREFIX = '__aimail_pong__:'

// ── logs ───────────────────────────────────────────────────────
// Shared helpers live in log.js (also used by tools.ts for outbound lines —
// keeping one implementation avoids drift between inbound and log formats).

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

/** Lightweight inbound log (mirror _log_aimail("inbound", ...)). */
export async function logAimailInbound(email: string, from: string, to: string, subject: string): Promise<void> {
  await appendLog(email, { event: 'inbound', from, to, subject })
}

// ── address helpers (mirror parse_aimail_persona / base_email) ──

export function parseAimailPersona(email: string, systemName = ''): { persona: string; profile: string; sysName: string } {
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
  const p = parseAimailPersona(email, systemName)
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
  // Attachments land in the per-agent leaf dir:
  // {leaf}/{yyyymm}/attch/{safe_mid}/ (mirror Python _aimail_dir() layout —
  // the leaf already encodes the address; yyyymm in LOCAL time like %Y%m).
  const now = new Date()
  const yyyymm = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}`
  const attchDir = path.join(AIMAIL_HOME(), 'mail', cleanAddr(cfg.email), yyyymm, 'attch', sanitizeMessageId(messageId || 'unknown'))
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

// ── a2a_board role templates (mirror _read_role_file / fill_template /
//    build_ctx; Q2 — dsh keeps the same role-file layout so the whoami.md /
//    board role .md files authored for the Python side work unchanged) ──

/** Replace {{KEY}} placeholders with ctx values (all occurrences). */
export function fillTemplate(text: string, ctx: Record<string, string>): string {
  let out = text
  for (const [key, val] of Object.entries(ctx)) {
    out = out.split(`{{${key}}}`).join(val)
  }
  return out
}

/**
 * Read an a2a_board role file with the Python three-level lookup:
 *   1. {home}/systems/{sid}/{addr}/role_prompt/{name}.md  (address override)
 *   2. {home}/systems/{sid}/board/role_prompt/{name}.md   (system-level)
 *   3. {home}/systems/{sid}/board/role_prompt/common.md   (fallback)
 * Empty string when nothing is found.
 * Role names are case-insensitive: the filename is always lowercased
 * before lookup (mirror Python _read_role_file .lower()).
 */
// ── prompt_rules: recognition ⇄ role file (ruling 2026-09-23; mirror
// pysdk aimail_base.prompt_rule_matches / read_prompt_rules) ──
// Fields = subject/body/sender/recipient; containment (case-insensitive);
// keywords inside one field are OR, the fields are AND. name = {serial10-99}_-
// {filename}; the actual file comes from rule.file (no string derivation).
// Single source within TS: preprocess evaluates these; the CLI's own dry-run
// lives in pysdk (language-native copy, parity locked by contract tests).
const PROMPT_RULE_NAME_RE = /^[1-9][0-9]_[a-z0-9_-]{1,64}$/
const PROMPT_RULE_FIELDS = ['subject', 'body', 'sender', 'recipient'] as const

export interface PromptRule {
  name: string
  file: string
  subject?: string | string[]
  body?: string | string[]
  sender?: string | string[]
  recipient?: string | string[]
  enabled?: boolean
}

export function promptRuleNameOk(name: string): boolean {
  return PROMPT_RULE_NAME_RE.test(name ?? '')
}

function ruleFieldKeywords(rule: PromptRule, key: (typeof PROMPT_RULE_FIELDS)[number]): string[] | null {
  const v = (rule as unknown as Record<string, unknown>)[key]
  if (v === undefined || v === null) return null // field absent → not evaluated
  if (typeof v === 'string') return [v]
  if (Array.isArray(v)) return v.map((k) => String(k))
  return null // wrong type → loader treats as no-field (WARN+drop); matcher → false
}

function ruleHasAnyField(rule: PromptRule): boolean {
  for (const key of PROMPT_RULE_FIELDS) {
    const arr = ruleFieldKeywords(rule, key)
    if (arr && arr.some((k) => k.trim())) return true
  }
  return false
}

/** Load + validate + sort (name order = serial order). Bad items: WARN + drop. */
export function readPromptRules(cfg: AgentConfig): PromptRule[] {
  const raw = cfg.prompt_rules
  if (raw === undefined || raw === null) return []
  if (!Array.isArray(raw)) {
    console.warn('[aimail] prompt_rules is not a list — ignored')
    return []
  }
  const kept: PromptRule[] = []
  for (const item of raw) {
    if (typeof item !== 'object' || item === null || Array.isArray(item)) {
      console.warn(`[aimail] prompt rule skipped (not an object): ${JSON.stringify(item)}`)
      continue
    }
    const r = item as PromptRule
    if (!promptRuleNameOk(r.name)) {
      console.warn(`[aimail] prompt rule skipped (bad name: ${JSON.stringify(r.name)})`)
      continue
    }
    if (typeof r.file !== 'string' || !r.file.trim()) {
      console.warn(`[aimail] prompt rule ${r.name} skipped (missing 'file')`)
      continue
    }
    if (r.enabled === false) continue
    if (!ruleHasAnyField(r)) {
      console.warn(`[aimail] prompt rule ${r.name} skipped (no non-empty field)`)
      continue
    }
    kept.push(r)
  }
  return kept.sort((a, b) => (a.name < b.name ? -1 : a.name > b.name ? 1 : 0))
}

/** Field-internal OR / cross-field AND (ruling ①). Absent field = skip;
 *  empty list = not given; wrong type = no match. */
export function promptRuleMatches(
  rule: PromptRule,
  subject: string,
  body: string,
  sender: string,
  recipient: string,
): boolean {
  const hay: Record<(typeof PROMPT_RULE_FIELDS)[number], string> = {
    subject: (subject ?? '').toLowerCase(),
    body: (body ?? '').toLowerCase(),
    sender: (sender ?? '').toLowerCase(),
    recipient: (recipient ?? '').toLowerCase(),
  }
  for (const key of PROMPT_RULE_FIELDS) {
    const v = (rule as unknown as Record<string, unknown>)[key]
    if (v === undefined || v === null) continue
    let items: string[]
    if (typeof v === 'string') items = [v]
    else if (Array.isArray(v)) items = v.map((k) => String(k).trim().toLowerCase()).filter(Boolean)
    else return false // wrong type → no match (loader already WARNs)
    if (items.length === 0) continue // empty list = not given
    if (!items.some((kw) => hay[key].includes(kw))) return false
  }
  return true
}

function recipientsText(result: Record<string, unknown>): string {
  const rec = (result.recipients ?? {}) as { to?: unknown; cc?: unknown }
  const to = Array.isArray(rec.to) ? rec.to : []
  const cc = Array.isArray(rec.cc) ? rec.cc : []
  return [...to, ...cc].map((x) => String(x)).join(' ')
}

function headerValue(headers: Record<string, unknown> | undefined, want: string): string {
  for (const [k, v] of Object.entries(headers ?? {})) {
    if (String(k).toLowerCase() === want) return String(v ?? '').trim()
  }
  return ''
}

async function readRoleFile(cfg: AgentConfig, name: string): Promise<string> {
  name = name.toLowerCase()
  const sid = cfg.system_id || 'default'
  const sysRoleDir = path.join(systemDir(sid), 'board', 'role_prompt')
  if (cfg.email) {
    const p = path.join(systemDir(sid), cleanAddr(cfg.email), 'role_prompt', `${name}.md`)
    try {
      return await fsp.readFile(p, 'utf-8')
    } catch {
      /* fall through */
    }
  }
  try {
    return await fsp.readFile(path.join(sysRoleDir, `${name}.md`), 'utf-8')
  } catch {
    /* fall through */
  }
  try {
    return await fsp.readFile(path.join(sysRoleDir, 'common.md'), 'utf-8')
  } catch {
    return ''
  }
}

/** Template context from the enriched payload (mirror build_ctx). */
function buildBoardCtx(result: Record<string, unknown>): Record<string, string> {
  return {
    AGENTMAIL_ADDRESS: String(result.my_aimail_addr ?? ''),
    BOARD_ID: String(result.board_id ?? ''),
    BOARD_ROLE: String(result.board_role ?? ''),
    FROM_ROLE: String(result.from_role ?? ''),
    INQUIRY_SENDER: String(result.from ?? ''),
    INQUIRY_SUBJECT: String(result.subject ?? ''),
    SOUL_MD_CONTENT: '', // Python injects per-platform (Hermes); dsh: none
    SKILLS_LIST: '',     // Python injects per-platform (Hermes); dsh: none
  }
}

// ── ping/pong handling ─────────────────────────────────────────

/**
 * SHARED pong sender (mirror Python send_pong): body carries
 * {"ping_id": ..., "event": {"mail_id": ...}} and the pong is keyed on the
 * original mail_id so the reply threading resolves. Returns success; the
 * pong_sent log carries the REAL outcome (ok/error), not a constant.
 */
async function sendPong(cfg: AgentConfig, payload: InboundPayload, pingId: string): Promise<boolean> {
  const from = (payload.from as string) || ''
  if (!from) return false
  const mailId = String(payload.mail_id ?? '')
  let res: ToolResult | undefined
  try {
    const pongArgs: { to: string; subject: string; body: string; message_id?: string } = {
      to: from,
      subject: `${PONG_PREFIX}${pingId}`,
      body: JSON.stringify({ ping_id: pingId, event: { mail_id: mailId } }),
    }
    if (mailId) pongArgs.message_id = mailId
    res = await sendMail(
      { systemId: cfg.system_id, email: cfg.email },
      pongArgs,
    )
  } catch (e) {
    await logPingEvent('pong_sent', pingId, payload, e instanceof Error ? e.message : String(e))
    return false
  }
  await logPingEvent('pong_sent', pingId, payload, res?.success ? 'ok' : String(res?.error ?? '?'))
  return Boolean(res?.success)
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
      const gatewayUrl = gwMatch[1].replace(/\/+$/, '')
      // board_id must match the gateway's derive_board_id: sha256 of the FULL
      // board address {short}.a2a@{gw-domain} [:20] — embeds the domain, so
      // no cross-system collision (mirror Python _extract_board_gateway; the
      // former body-regex guess was not the gateway's id).
      const fromMatch = /(\S+)\.a2a@/.exec(fromRaw)
      if (fromMatch?.[1]) {
        const gwDomain = /:\/\/([^/]+)/.exec(gatewayUrl)?.[1] ?? ''
        const boardEmail = `${fromMatch[1]}.a2a@${gwDomain}`
        const bid = createHash('sha256').update(boardEmail, 'utf-8').digest('hex').slice(0, 20)
        registerBoardGateway(bid, gatewayUrl)
        // token persistence (board_creds.json) when token present
        if (tokenMatch?.[1] && ctx.systemId && ctx.email) {
          try {
            const credsPath = path.join(systemDir(ctx.systemId), cleanAddr(ctx.email), 'board_creds.json')
            const existing = JSON.parse(await fsp.readFile(credsPath, 'utf-8').catch(() => '{}')) as Record<string, unknown>
            existing[bid] = { gateway_url: gatewayUrl, token: tokenMatch[1] }
            await fsp.mkdir(path.dirname(credsPath), { recursive: true })
            await fsp.writeFile(credsPath, JSON.stringify(existing, null, 2) + '\n', { mode: 0o600 })
          } catch {
            /* non-fatal */
          }
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
      my_aimail_addr: '',
      direct_message: false,
      mentioned: false,
      _preprocess_error: 'aimail email not configured',
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
  const persona = myToAddr ? parseAimailPersona(myToAddr, systemName).persona : ''
  if (persona) {
    // PERSONA_SUPPORTED=false: normalize recipient to base address
    // (strip persona prefix, no config validation) — dsh contract
    result.my_aimail_addr = agentEmail
  } else {
    result.my_aimail_addr = myToAddr || agentEmail
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
  const p = parseAimailPersona(myToAddr, systemName)
  const matchTargets = [agentLocal, p.profile].filter(Boolean)
  if (agentDisplay) matchTargets.push(agentDisplay)
  const bodyLower = (bodyRaw ?? '').toLowerCase()
  result.mentioned = matchTargets.some(t => t && (bodyLower.includes(`@${t.toLowerCase()}`) || bodyLower.split(/\s+/).includes(t.toLowerCase())))

  // step 10: B1 batch profile injection — one GET /api/v1/contacts?addresses=
  // for [sender, ...to, ...cc]; the endpoint treats the FIRST address as the
  // inbound sender. my_profile is the calling agent's approved persona
  // (domain_addr_meta — single source of truth). Failures are non-fatal
  // (no profiles injected), mirroring Python preprocess_inbound B1.
  const senderBare = String(payload.from ?? '').trim().toLowerCase()
  {
    const batchAddrs: string[] = []
    const seen = new Set<string>()
    for (const a of [senderBare, ...toBare, ...ccBare]) {
      if (a && !seen.has(a)) {
        seen.add(a)
        batchAddrs.push(a)
      }
    }
    if (batchAddrs.length && cfg.api_key) {
      try {
        const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
        const profiles = await client.getContactProfiles(batchAddrs)
        if (profiles.my_profile) result.my_profile = profiles.my_profile.profile
        if (Object.keys(profiles.sender_profile).length) result.sender_profile = profiles.sender_profile
        if (Object.keys(profiles.recipients_profile).length) result.recipients_profile = profiles.recipients_profile
      } catch (e) {
        console.warn(`B1 batch profiles skipped: ${e instanceof Error ? e.message : String(e)}`)
      }
    }
  }

  // step 11: B2 thread_summary preload (pure local, no gateway round-trip).
  // thread_id = first References entry (thread root), else the message_id
  // itself — identical to saveLocalMeta's write-time derivation. Only
  // pre-existing threads are injected; a first mail in a thread has no file
  // yet and gets nothing.
  {
    const midB2 = String(result.message_id ?? '').trim()
    const refsB2: string[] = Array.isArray(payload.references)
      ? payload.references.map(r => String(r).trim()).filter(Boolean)
      : []
    const tid = refsB2[0] || midB2
    if (tid) {
      try {
        const raw = await fsp.readFile(threadPath(cfg.email, tid), 'utf-8')
        const summary = (JSON.parse(raw) as { summary?: string }).summary?.trim()
        if (summary) result.thread_summary = summary
      } catch {
        /* no thread file yet (first mail) or unreadable — nothing to inject */
      }
    }
  }

  // step 12-13: attachments
  if (payload.attachments?.length && cfg.api_key) {
    const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
    const paths = await downloadAttachments(client, payload.attachments, (payload.message_id as string) ?? '', cfg)
    result.attachments = paths
  }

  // step 14: strip backend-only fields
  const stripFields = new Set(['mail_id', 'to', 'cc', 'headers', 'created_at', 'forwarder', 'forward_at'])
  const cleaned: Record<string, unknown> = {}
  for (const [k, v] of Object.entries(result)) {
    if (!stripFields.has(k)) cleaned[k] = v
  }
  result = cleaned

  // step 14b: payload format guard (port of Python store_inbound_message's
  // RAW-payload guard). dsh preprocesses INSIDE processInboundMail, so the
  // live mis-format is the reverse of Python's: an ALREADY-enriched payload
  // passed in (double preprocessing) — it carries 'recipients' and has no
  // gateway 'mail_id' (stripped by the first pass).
  if (!payload.mail_id && (payload as Record<string, unknown>).recipients) {
    console.warn(
      'processInboundMail received a preprocessed payload instead of the gateway RAW payload ' +
      '(double preprocessing) — meta/log may be derived from stale fields.',
    )
  }

  // step 14c: always-write local meta for inbound (回复链依赖, 不受快照开关
  // 控制) — mirror Python store_inbound_message (meta/{xx}/{mid}.json).
  const midRaw = (result.message_id as string) ?? ''
  const myAddrMeta = result.my_aimail_addr as string
  if (midRaw && myAddrMeta) {
    const refs = Array.isArray(payload.references) ? payload.references : []
    await saveLocalMeta(cfg.email, midRaw, refs, myAddrMeta, 'inbound')
  }

  // step 15: inbound log
  const mid = (result.message_id as string) ?? ''
  const myAddr = result.my_aimail_addr as string
  if (mid && myAddr) {
    const fromHdr = rawHeaders['from'] ?? (payload.from as string) ?? ''
    const subjHdr = rawHeaders['subject'] ?? subjectRaw
    await logAimailInbound(cfg.email, fromHdr, myAddr, subjHdr)
  }

  // board extras: [WHOAMI] / board_id+board_role (mirror Python — the
  // [WHOAMI] branch early-returns, so board extras only run for non-whoami)
  const subj = subjectRaw.trim()
  if (subj.toUpperCase().startsWith('[WHOAMI]')) {
    const whoamiRaw = await readRoleFile(cfg, 'whoami')
    if (whoamiRaw) result._whoami_prompt = fillTemplate(whoamiRaw, buildBoardCtx(result))
    return result as unknown as EnrichedPayload
  }
  // B3: Role_Calibrator (persona 更新请求 / welcome 引导) — mirror Python B3.
  // 2026-09-22 合并后两种识别形式:
  //   (a) 兼容旧: 主题含 "update persona"(保留一版; 下一版删除)
  //   (b) 合并后的 welcome: **主题标记 + 正文三标签同时命中**
  //       主题含 "welcome to aimail world" 且正文行首有 persona:/signature:/current_time:
  // 命中 ⇒ 注入 role_calibrator.md 并 early-return(避免被 board 角色覆盖);
  // 只命中其一 ⇒ 不注入 + warn, 使模板漂移可见(不静默退化为默认 prompt)。
  const subjLc = subj.toLowerCase()
  const bodyLc = String((result.body as string) ?? '').toLowerCase()
  const welcomeMarker = subjLc.includes('welcome to aimail world')
  const labelsOk = ['persona', 'signature', 'current_time'].every((k) =>
    bodyLc.split('\n').some((ln) => ln.trimStart().startsWith(`${k}:`)),
  )
  if (subjLc.includes('update persona') || (welcomeMarker && labelsOk)) {
    const calibRaw = await readRoleFile(cfg, 'role_calibrator')
    if (calibRaw) result._role_prompt = fillTemplate(calibRaw, buildBoardCtx(result))
    return result as unknown as EnrichedPayload
  }
  if (welcomeMarker || labelsOk) {
    console.warn(
      `[aimail] role prompt marker mismatch (welcome_marker=${welcomeMarker} labels=${labelsOk}) — default role prompt used`,
    )
  }
  const boardId = (result.board_id as string) ?? ''
  const boardRole = (result.board_role as string) ?? ''
  if (boardId && boardRole) {
    const roleRaw = await readRoleFile(cfg, boardRole)
    if (roleRaw) result._role_prompt = fillTemplate(roleRaw, buildBoardCtx(result))
    result._a2a_session_key = `a2a:${boardId}:${(result.from as string) ?? ''}`
  }

  // ── L4 gateway X-AIMail-Prompt header → L5 local prompt_rules
  // (ruling 2026-09-23; fixed chain: [WHOAMI] > welcome > board > header >
  // local name-ordered first hit. Missing role file (all three lookup tiers,
  // incl. common.md) ⇒ WARN + fall through; never blocks delivery. Mirror:
  // pysdk aimail_base.preprocess_mail_payload.) ──
  if (!result._role_prompt) {
    const ctx = buildBoardCtx(result)
    let injected = false
    // header 双源(网关盖头位置未锁死 = 扩展点): 邮件头 payload.headers 优先,
    // HTTP 头 _headers 兜底; key 大小写不敏感, 空值视为未给。
    const hdr =
      headerValue(rawHeaders as unknown as Record<string, unknown>, 'x-aimail-prompt') ||
      headerValue(_headers as unknown as Record<string, unknown>, 'x-aimail-prompt')
    if (hdr) {
      const hdrRaw = await readRoleFile(cfg, hdr)
      if (hdrRaw) {
        result._role_prompt = fillTemplate(hdrRaw, ctx)
        injected = true
      } else {
        console.warn(`[aimail] X-AIMail-Prompt names a missing role file (${hdr}) — falling through to local prompt_rules`)
      }
    }
    if (!injected) {
      const senderTxt = String(result.sender ?? result.from ?? '')
      const recpTxt = recipientsText(result)
      const subjectTxt = String(result.subject ?? subj ?? '')
      const bodyTxt = String(result.body ?? '')
      for (const rule of readPromptRules(cfg)) {
        if (!promptRuleMatches(rule, subjectTxt, bodyTxt, senderTxt, recpTxt)) continue // first hit wins
        const raw = await readRoleFile(cfg, rule.file)
        if (!raw) {
          console.warn(`[aimail] prompt rule ${rule.name} matched but role file '${rule.file}' missing — next rule`)
          continue
        }
        result._role_prompt = fillTemplate(raw, ctx)
        break
      }
    }
  }

  // ── LAST: ping/pong interception ──
  if (subj.startsWith(PING_PREFIX)) {
    const pingId = subj.split(':', 1)[1]?.trim() ?? subj.slice(PING_PREFIX.length).trim()
    await logPingEvent('ping_intercepted', pingId, payload)
    // sendPong logs pong_sent with the REAL outcome (ok/error) internally;
    // no extra constant log here — that would double-write and lie 'sent'
    // on the failure path (see sendPong doc, line ~285).
    await sendPong(cfg, payload, pingId)
    return null
  }
  if (subj.startsWith(PONG_PREFIX)) {
    const pingId = subj.split(':', 1)[1]?.trim() ?? subj.slice(PONG_PREFIX.length).trim()
    await logPingEvent('pong_returned', pingId, payload)
    return null
  }
  return result as unknown as EnrichedPayload
}

/**
 * HMAC verify (X-Webhook-Signature, hex sha256 of raw body with
 * webhook_secret). Constant-time comparison (timingSafeEqual) so the
 * result does not leak how many leading hex chars matched.
 */
export function verifySignature(rawBody: Buffer | string, signature: string, secret: string): boolean {
  if (!signature || !secret) return false
  const expected = createHmac('sha256', secret).update(rawBody).digest('hex')
  const sigBuf = Buffer.from(signature, 'utf8')
  const expBuf = Buffer.from(expected, 'utf8')
  if (sigBuf.length !== expBuf.length) return false
  return timingSafeEqual(sigBuf, expBuf)
}
