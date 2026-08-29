/**
 * 12 tool functions (mail-core) — thin wrappers over GatewayClient mirroring
 * Python agentmail_tools.py. Contract: DSH-PREPROCESS-CONTRACT.md §3 +
 * amail_mcp_server.py tool registry (names/descriptions/params identical).
 */
import { randomUUID } from 'node:crypto'
import { promises as fsp } from 'node:fs'
import * as path from 'node:path'
import { existsSync, readFileSync } from 'node:fs'
import { GatewayClient } from './gateway.js'
import { AIMAIL_HOME, loadAgentConfig } from './config.js'
import { readLocalMeta, saveLocalMeta, saveOutboundSnapshot, resolveThreadId, threadPath } from './meta.js'
import { logAmailOutbound } from './log.js'
import type { AgentConfig } from './types.js'

export interface ToolResult {
  success: boolean
  error?: string
  note?: string
  [k: string]: unknown
}

// ── identity ────────────────────────────────────────────────────

let _identityOverride = ''
let _detectedIdentity = ''

/** Set outbound X-AIMail-Agent (real detected value only, no guessing). */
export function setAgentIdentity(v: string): void {
  _identityOverride = v
}

/** Walk up from cwd to find the dsh host package (@deepseek-ai/dsh-root). */
function detectDshIdentity(): string {
  let dir = process.cwd()
  for (let i = 0; i < 10; i++) {
    try {
      const pkgPath = path.join(dir, 'package.json')
      if (existsSync(pkgPath)) {
        const pkg = JSON.parse(readFileSync(pkgPath, 'utf-8')) as { name?: string; version?: string }
        if (pkg.name === '@deepseek-ai/dsh-root' && typeof pkg.version === 'string' && pkg.version) {
          return `dsh/${pkg.version}`
        }
      }
    } catch {
      /* keep walking */
    }
    const parent = path.dirname(dir)
    if (parent === dir) break
    dir = parent
  }
  return 'dsh/unknown'
}

function agentIdentity(): string {
  if (_identityOverride) return _identityOverride
  if (_detectedIdentity) return _detectedIdentity
  const detected = detectDshIdentity()
  // Cache only successful detection; unknown is re-probed each call
  // (cheap) so a later cwd/override change can still resolve.
  if (detected !== 'dsh/unknown') _detectedIdentity = detected
  return detected
}

// ── config/context ─────────────────────────────────────────────

async function requireConfig(systemId: string, email?: string): Promise<AgentConfig> {
  const cfg = email ? await loadAgentConfig(systemId, email) : undefined
  if (!cfg) throw new Error('agentmail not configured for this agent (agentmail.json missing)')
  if (!cfg.api_key) throw new Error('agentmail api_key not available')
  return cfg
}

export interface ToolCtx {
  systemId: string
  email?: string
}

// ── message id helpers (mirror _build_message_id / _sanitize_message_id) ──

function buildMessageId(cfg: AgentConfig): string {
  const domain = cfg.domain || 'amail.local'
  return `<${randomUUID().replace(/-/g, '')}@${domain}>`
}

// sanitizeMessageId is canonicalized in meta.ts (shared with the local meta
// layer); re-exported here so existing importers (preprocess, index) keep working.
export { sanitizeMessageId } from './meta.js'
import type { LocalMeta } from './meta.js'

// ── message meta (LOCAL meta/{xx}/{mid}.json, always written) ─────
// Replaces the former gateway agent_state msg:{mid} key (removed gateway-side
// in the 2026-08-24 localization refactor). Zero HTTP round-trips.

async function loadMessageMeta(email: string, messageId: string): Promise<LocalMeta | undefined> {
  return readLocalMeta(email, messageId)
}

async function storeMessageMeta(
  email: string,
  messageId: string,
  references?: string,
  myAmailAddr = '',
  direction = 'outbound',
): Promise<void> {
  await saveLocalMeta(email, messageId, references, myAmailAddr, direction)
}

// ── attachment resolution (mirror _resolve_attachments) ────────

const ATTACH_MAX_SIZE_MB = 10
const ATTACH_SKIP_DIRS = new Set([
  '.git', '__pycache__', 'node_modules', 'venv', '.venv', '.hermes', 'target',
  '.pytest_cache', '.mypy_cache', '.tox', '.eggs', 'dist', 'build', '__pypackages__',
])

async function fileExists(p: string): Promise<boolean> {
  try {
    return (await fsp.stat(p)).isFile()
  } catch {
    return false
  }
}

async function resolveAttachments(rawPaths: string[]): Promise<{ resolved: string[]; errors: string[] }> {
  const resolved: string[] = []
  const errors: string[] = []
  const cwd = process.cwd()
  const workspaceRoots = [cwd, AIMAIL_HOME()]

  for (const raw of rawPaths) {
    const r = (raw ?? '').trim()
    if (!r) continue
    const abs = path.isAbsolute(r) ? r : path.resolve(cwd, r)
    if (await fileExists(abs)) {
      resolved.push(abs)
      continue
    }
    // bare filename — walk workspace roots for a unique match (depth-limited)
    let matches: string[] = []
    for (const root of workspaceRoots) {
      try {
        matches = await findUnique(root, path.basename(r), 0)
      } catch {
        /* skip */
      }
      if (matches.length === 1) break
    }
    if (matches.length === 1) {
      const m0 = matches[0]
      if (m0) resolved.push(m0)
    } else if (matches.length > 1) errors.push(`Ambiguous attachment '${r}' (${matches.length} matches)`)
    else errors.push(`Attachment not found: ${r}`)
  }
  return { resolved, errors }
}

async function findUnique(root: string, name: string, depth: number): Promise<string[]> {
  if (depth > 5) return []
  const out: string[] = []
  let entries: import('node:fs').Dirent[]
  try {
    entries = await fsp.readdir(root, { withFileTypes: true })
  } catch {
    return out
  }
  for (const ent of entries) {
    if (ATTACH_SKIP_DIRS.has(ent.name)) continue
    const p = path.join(root, ent.name)
    if (ent.isFile() && ent.name === name) {
      out.push(p)
      if (out.length >= 50) return out
    } else if (ent.isDirectory()) {
      out.push(...(await findUnique(p, name, depth + 1)))
      if (out.length >= 50) return out
    }
  }
  return out
}

// ── send_mail ──────────────────────────────────────────────────

export interface SendMailArgs {
  to: string | string[]
  subject: string
  body: string
  cc?: string | string[]
  attachments?: string[]
  message_id?: string
}

export async function sendMail(ctx: ToolCtx, args: SendMailArgs): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)

  // Recipients kept as lists end-to-end (no join→split round-trip); joined
  // with ',' only at send time (mirrors Python to_list/cc_list).
  const toList = (Array.isArray(args.to) ? args.to : String(args.to).split(','))
    .map(s => s.trim()).filter(Boolean)
  const ccList = args.cc == null ? undefined
    : (Array.isArray(args.cc) ? args.cc : String(args.cc).split(',')).map(s => s.trim()).filter(Boolean)

  // Resolve message meta once (threading + persona sender)
  const msgMeta = args.message_id ? await loadMessageMeta(cfg.email, args.message_id) : undefined

  // sender: dsh has no persona — base email (persona normalization contract)
  let sender = cfg.email
  if (msgMeta?.my_amail_addr && typeof msgMeta.my_amail_addr === 'string' && msgMeta.my_amail_addr.includes('@')) {
    sender = msgMeta.my_amail_addr
  }

  const isForward = Boolean(args.message_id && args.subject && args.subject.toLowerCase().startsWith('fw:'))

  let inReplyTo: string | undefined
  let references: string | undefined
  if (args.message_id) {
    if (!isForward) inReplyTo = args.message_id
    if (msgMeta) {
      const refs = [...(msgMeta.references ?? []), args.message_id]
      references = [...new Set(refs)].join(' ')
    } else {
      references = args.message_id
    }
  }

  // attachments: resolve → size check → upload
  const uploadErrors: string[] = []
  const attachmentIds: Array<{ id: string }> = []
  let resolvedPaths: string[] = []
  if (args.attachments?.length) {
    const r = await resolveAttachments(args.attachments)
    resolvedPaths = r.resolved
    uploadErrors.push(...r.errors)
    for (const p of resolvedPaths) {
      try {
        const st = await fsp.stat(p)
        if (st.size > ATTACH_MAX_SIZE_MB * 1024 * 1024) {
          uploadErrors.push(`${path.basename(p)} exceeds ${ATTACH_MAX_SIZE_MB}MB`)
          continue
        }
        const resp = await client.uploadAttachment(p)
        if (resp.status === 201) {
          const id = (resp.attachment_id as string) || (resp.id as string) || ''
          if (id) attachmentIds.push({ id })
        } else {
          uploadErrors.push(`Upload failed for ${path.basename(p)}: ${resp.error ?? `HTTP ${resp.status}`}`)
        }
      } catch (e) {
        uploadErrors.push(`Upload failed for ${path.basename(p)}: ${e instanceof Error ? e.message : String(e)}`)
      }
    }
  }
  if (uploadErrors.length && attachmentIds.length === 0) {
    return { success: false, error: 'All attachments failed', details: uploadErrors }
  }

  // ── 先存再调: meta 常写 + outbox 快照按开关, 随后才调 API ──
  // Message-ID 本地生成并传给 gateway, 本地值即线上值(不再回填 API 返回值)。
  const generatedMid = buildMessageId(cfg)
  await storeMessageMeta(cfg.email, generatedMid, references, sender, 'outbound')
  if (cfg.save_raw_snapshots) {
    await saveOutboundSnapshot(cfg.email, generatedMid, sender, toList.join(','), args.subject, args.body,
      ccList ?? [], resolvedPaths, attachmentIds, inReplyTo ?? '', references ?? '')
  }

  const sendOpts: {
    to: string
    subject: string
    body: string
    cc?: string[]
    attachments?: Array<{ id: string }>
    inReplyTo?: string
    references?: string
    sender: string
    messageId: string
    headers: Record<string, string>
  } = {
    to: toList.join(','),
    subject: args.subject,
    body: args.body,
    sender,
    messageId: generatedMid,
    headers: { 'X-AIMail-Agent': agentIdentity() },
  }
  // cc 透传数组 — 网关 SendEmailRequest.cc: Option<Vec<String>>,join(',') 会 422
  if (ccList?.length) sendOpts.cc = ccList
  if (attachmentIds.length) sendOpts.attachments = attachmentIds
  if (inReplyTo) sendOpts.inReplyTo = inReplyTo
  if (references) sendOpts.references = references

  // ── Submit with bounded retry + terminal semantics (mirrors Python) ──
  // The LLM must never see a "fixable" failure state — that is what drove
  // the 2026-08-28 duplicate-reply storm (422 → agent wrote diag scripts →
  // re-sent the same reply 3× out-of-band). The tool owns the failure loop:
  //   - retryable (network drop / 429 / 5xx): retry w/ backoff, ≤5
  //   - 409 duplicate_send: the email IS already out → success
  //   - anything else: TERMINAL failure with an explicit guardrail
  const RETRYABLE = new Set([0, 429, 500, 502, 503, 504])
  const MAX_ATTEMPTS = 5
  const GUARDRAIL =
    'This is terminal. Do NOT retry by any other means ' +
    '(no terminal commands, curl, scripts, or direct gateway API calls). ' +
    'Report this failure in your reply to the sender.'
  const sleep = (ms: number) => new Promise(r => setTimeout(r, ms))

  let result = await client.sendMail(sendOpts)
  for (let attempt = 1; attempt < MAX_ATTEMPTS; attempt++) {
    const ok = result.status >= 200 && result.status < 300
    const dup = result.status === 409 && result.error === 'duplicate_send'
    if (ok || dup) break
    if (!RETRYABLE.has(result.status)) break
    const delay = Math.min(2 ** (attempt - 1), 8) * 1000
    console.warn(`[mail-core] send attempt ${attempt}/${MAX_ATTEMPTS} failed (HTTP ${result.status}: ${(result.error as string) || '?'}), retrying in ${delay}ms`)
    await sleep(delay)
    result = await client.sendMail(sendOpts)
  }

  // auto-bootstrap thread summary for new (non-reply) emails — keyed on the
  // local generated mid (mirrors Python: `if not message_id:`)
  let threadBootstrapped = false
  if (!args.message_id) {
    try {
      await setEmailSummary(ctx, { message_id: generatedMid, summary: `Subject: ${args.subject}\nStatus: awaiting response` })
      threadBootstrapped = true
    } catch {
      /* non-fatal */
    }
  }

  if (result.status >= 200 && result.status < 300) {
    // Outbound line to the per-agent agentmail.log (Python parity: _log_amail
    // "outbound" on the success branch) — the welcome CLI polls this file to
    // detect the agent's reply; TS used to skip it, breaking that poll.
    await logAmailOutbound(cfg.email, sender, toList.join(','), args.subject, generatedMid)
    const out: ToolResult = { success: true, ...result }
    if (threadBootstrapped) out.thread_bootstrapped = true
    if (uploadErrors.length) out.note = `Sent, but ${uploadErrors.length} attachment(s) had issues: ${uploadErrors.slice(0, 3).join('; ')}`
    return out
  }
  if (result.status === 409 && result.error === 'duplicate_send') {
    // An identical (to/cc/subject/body) email was already accepted by the
    // gateway within the dedup window — the content IS out.
    return {
      success: true,
      duplicate: true,
      note: 'An identical email was already sent (gateway dedup window). The content is already out — no further action needed.',
    }
  }
  const err = (result.error as string) || (result.detail as string) || `HTTP ${result.status}`
  return { success: false, error: `Send failed (terminal, HTTP ${result.status}): ${err}`, instruction: GUARDRAIL }
}

// ── manage_contacts ────────────────────────────────────────────

export interface ManageContactsArgs {
  action: 'check' | 'add' | 'remove' | 'update'
  address?: string
  direction?: 'from' | 'to' | 'all'
  description?: string
}

export async function manageContacts(ctx: ToolCtx, args: ManageContactsArgs): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
  const direction = args.direction ?? 'all'
  const emailAddr = cfg.email

  switch (args.action) {
    case 'check': {
      if (!args.address) return { success: false, error: 'address is required for check' }
      // gateway returns {whitelisted, domain_addr, value, direction} — read
      // the `whitelisted` field (mirror Python check_whitelist_value; the
      // former `in_contacts` read was always false → D6).
      const r = await client.checkWhitelist(emailAddr, args.address, direction)
      const whitelisted = r.status === 200 && r.whitelisted === true
      const entryDirection = (whitelisted && typeof r.direction === 'string' && r.direction) || direction
      return { success: true, in_contacts: whitelisted, direction: entryDirection, address: args.address }
    }
    case 'add': {
      if (!args.address) return { success: false, error: 'address is required for add' }
      const managerAddr = cfg.manager_address ?? ''
      if (!managerAddr) return { success: false, error: 'No manager_address configured — cannot send approval request' }
      const descLine = args.description ? `\ndescription: ${args.description}` : ''
      const r = await client.sendMail({
        to: managerAddr,
        subject: `[Amail] Contact request: ${args.address}`,
        body: `Please add ${args.address} to ${emailAddr}'s contacts with direction=${direction}.${descLine}\n\n` +
              `To approve, reply to this email with:\nadd ${args.address} to my contacts with direction=${direction}`,
      })
      if (r.status >= 200 && r.status < 300) {
        return { success: true, note: `Approval request sent to manager (${managerAddr})` }
      }
      return { success: false, error: `Failed to send approval request: ${r.error ?? `HTTP ${r.status}`}` }
    }
    case 'remove': {
      if (!args.address) return { success: false, error: 'address is required for remove' }
      const r = await client.deleteWhitelist(emailAddr, args.address)
      if (r.status === 204) return { success: true }
      if (r.status === 404) return { success: false, error: `${args.address} not found in whitelist` }
      return { success: false, error: `Failed to remove ${args.address}: ${r.error ?? r.detail ?? `HTTP ${r.status}`}` }
    }
    case 'update': {
      if (!args.address) return { success: false, error: 'address is required for update' }
      const newDirection = args.direction ?? direction
      if (!newDirection) return { success: false, error: 'direction is required for update' }
      const r = await client.setWhitelist(emailAddr, args.address, newDirection)
      if (r.status >= 200 && r.status < 300) {
        return { success: true, note: `direction updated to ${newDirection}` }
      }
      return { success: false, error: `Failed to update ${args.address}: ${r.error ?? r.detail ?? `HTTP ${r.status}`}` }
    }
    default:
      return { success: false, error: `Unknown action: ${String(args.action)}` }
  }
}

// ── contact profile ────────────────────────────────────────────

export interface ContactProfileArgs {
  address?: string
  name?: string
}

export async function contactProfile(ctx: ToolCtx, args: ContactProfileArgs): Promise<ToolResult> {
  if (!args.address && !args.name) {
    return { success: false, error: 'address or name is required' }
  }
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
  if (args.address) {
    // exact lookup — gateway returns {address, profile} (404 → none)
    const r = await client.contactGet(args.address)
    if (r.status === 200) return { success: true, address: args.address, profile: r.profile ?? null }
    if (r.status === 404) return { success: true, address: args.address, profile: null }
    return { success: false, error: r.error ?? `HTTP ${r.status}` }
  }
  const name = args.name
  if (!name) {
    return { success: false, error: 'name is required for name lookup' }
  }
  // server-side search — gateway returns {results: [{address, profile}]}
  const r = await client.contactSearch(name.trim())
  if (r.status !== 200) {
    return { success: false, error: r.error ?? `HTTP ${r.status}` }
  }
  const results = (Array.isArray(r.results) ? r.results : []) as Array<{ address: string; profile?: string }>
  if (results.length === 0) {
    return { success: true, address: '', profile: null, searched_name: name }
  }
  if (results.length === 1) {
    const hit = results[0]!
    return { success: true, address: hit.address, profile: hit.profile ?? null }
  }
  // multiple matches — ambiguous, surface the candidates (Python parity)
  return { success: true, ambiguous: true, candidates: results.map(x => x.address) }
}

export async function setContactProfile(ctx: ToolCtx, args: { address: string; profile: string }): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
  const r = await client.contactPut(args.address, args.profile)
  return { success: r.status >= 200 && r.status < 300, ...r }
}

// ── email_summary / set_email_summary — LOCAL threads/{xx}/{tid}.json ──
// Mirror Python email_summary/set_email_summary (2026-08-24 localization):
// mid → thread_id via local meta, then read/write threads/{xx}/{thread_id}.json.
// The former gateway /api/v1/thread-summary endpoint was removed (Task 5).

export interface EmailSummaryArgs {
  message_id: string
}

export async function emailSummary(ctx: ToolCtx, args: EmailSummaryArgs): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const email = cfg.email
  const mid = (args.message_id || '').trim()
  const threadId = mid ? await resolveThreadId(email, mid) : ''
  if (!threadId) return { success: true, thread_id: '', summary: '' }
  try {
    const raw = await fsp.readFile(threadPath(email, threadId), 'utf-8')
    const data = JSON.parse(raw) as { summary?: string }
    return { success: true, thread_id: threadId, summary: data.summary ?? '' }
  } catch {
    return { success: true, thread_id: threadId, summary: '' }
  }
}

export async function setEmailSummary(ctx: ToolCtx, args: { message_id: string; summary: string }): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const email = cfg.email
  if (!args.message_id || !args.message_id.trim()) {
    return { success: false, error_code: 'MESSAGE_ID_REQUIRED' }
  }
  if (typeof args.summary !== 'string') {
    return { success: false, error_code: 'SUMMARY_MUST_BE_STRING' }
  }
  if (args.summary.length > 2000) {
    return { success: false, error_code: 'SUMMARY_TOO_LONG', max_length: 2000 }
  }

  const threadId = await resolveThreadId(email, args.message_id)
  if (!args.summary.trim()) {
    // 空 summary = 删除线程文件(与原 gateway 语义一致)
    try {
      await fsp.rm(threadPath(email, threadId), { force: true })
    } catch {
      /* ignore */
    }
    return { success: true }
  }
  const p = threadPath(email, threadId)
  try {
    await fsp.mkdir(path.dirname(p), { recursive: true })
    const tmp = `${p}.tmp`
    await fsp.writeFile(
      tmp,
      JSON.stringify({ thread_id: threadId, summary: args.summary, updated_at: new Date().toISOString() }, null, 2),
      'utf-8',
    )
    await fsp.rename(tmp, p)
  } catch (e) {
    return { success: false, error: `Failed to store summary: ${e instanceof Error ? e.message : String(e)}` }
  }
  return { success: true }
}
