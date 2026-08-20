/**
 * 12 tool functions (mail-core) — thin wrappers over GatewayClient mirroring
 * Python agentmail_tools.py. Contract: DSH-PREPROCESS-CONTRACT.md §3 +
 * amail_mcp_server.py tool registry (names/descriptions/params identical).
 */
import { randomUUID } from 'node:crypto'
import { promises as fsp } from 'node:fs'
import * as path from 'node:path'
import { GatewayClient } from './gateway.js'
import { AIMAIL_HOME, loadAgentConfig } from './config.js'
import type { AgentConfig } from './types.js'

export interface ToolResult {
  success: boolean
  error?: string
  note?: string
  [k: string]: unknown
}

// ── identity ────────────────────────────────────────────────────

let _identityOverride = ''

/** Set outbound X-Agentmail-Agent (real detected value only, no guessing). */
export function setAgentIdentity(v: string): void {
  _identityOverride = v
}

function agentIdentity(): string {
  return _identityOverride || 'dsh/unknown'
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

export function sanitizeMessageId(messageId: string): string {
  let mid = messageId.trim().replace(/^</, '').replace(/>$/, '')
  for (const ch of '/\\:*?"<>|@ ') {
    mid = mid.split(ch).join('_')
  }
  return mid
}

// ── message meta (gateway agent_state, key msg:{id}) ───────────

interface MsgMeta {
  references: string[]
  thread_id: string
  [k: string]: unknown
}

async function loadMessageMeta(client: GatewayClient, messageId: string): Promise<MsgMeta | undefined> {
  const r = await client.agentStateGet(`msg:${messageId.trim()}`)
  const v = r.value ?? r.data
  if (typeof v !== 'string' || !v) return undefined
  try {
    return JSON.parse(v) as MsgMeta
  } catch {
    return undefined
  }
}

async function storeMessageMeta(client: GatewayClient, messageId: string, references?: string): Promise<void> {
  const mid = messageId.trim()
  if (!mid) return
  const refs = (references ?? '').split(/\s+/).filter(Boolean)
  const threadId = refs[0] || mid
  await client.agentStatePut(`msg:${mid}`, JSON.stringify({ references: refs, thread_id: threadId }))
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
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key)

  const to = Array.isArray(args.to) ? args.to.join(', ') : args.to
  const cc = Array.isArray(args.cc) ? args.cc.join(', ') : args.cc

  // Resolve message meta once (threading + persona sender)
  const msgMeta = args.message_id ? await loadMessageMeta(client, args.message_id) : undefined

  // sender: dsh has no persona — base email (persona normalization contract)
  let sender = cfg.email
  if (msgMeta?.my_amail_addr && typeof msgMeta.my_amail_addr === 'string' && msgMeta.my_amail_addr.includes('@')) {
    sender = msgMeta.my_amail_addr
  }

  const toList = to.split(',').map(s => s.trim()).filter(Boolean)
  const ccList = cc ? cc.split(',').map(s => s.trim()).filter(Boolean) : undefined

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
  if (args.attachments?.length) {
    const { resolved, errors } = await resolveAttachments(args.attachments)
    uploadErrors.push(...errors)
    for (const p of resolved) {
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

  const sendOpts: {
    to: string
    subject: string
    body: string
    cc?: string
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
    messageId: buildMessageId(cfg),
    headers: { 'X-Agentmail-Agent': agentIdentity() },
  }
  if (ccList?.length) sendOpts.cc = ccList.join(',')
  if (attachmentIds.length) sendOpts.attachments = attachmentIds
  if (inReplyTo) sendOpts.inReplyTo = inReplyTo
  if (references) sendOpts.references = references

  const result = await client.sendMail(sendOpts)

  const outMsgId = (result.message_id as string) || (result.email_id as string) || ''
  if (outMsgId) {
    await storeMessageMeta(client, outMsgId, references)
  }

  // auto-bootstrap thread summary for new (non-reply) emails
  let threadBootstrapped = false
  if (outMsgId && !args.message_id) {
    try {
      await setEmailSummary(ctx, { message_id: outMsgId, summary: `Subject: ${args.subject}\nStatus: awaiting response` })
      threadBootstrapped = true
    } catch {
      /* non-fatal */
    }
  }

  if (result.status >= 200 && result.status < 300) {
    const out: ToolResult = { success: true, ...result }
    if (threadBootstrapped) out.thread_bootstrapped = true
    if (uploadErrors.length) out.note = `Sent, but ${uploadErrors.length} attachment(s) had issues: ${uploadErrors.slice(0, 3).join('; ')}`
    return out
  }
  const err = (result.error as string) || (result.detail as string) || `HTTP ${result.status}`
  return { success: false, error: `Send failed: ${err}` }
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
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key)
  const direction = args.direction ?? 'all'
  const emailAddr = cfg.email

  switch (args.action) {
    case 'check': {
      if (!args.address) return { success: false, error: 'address is required for check' }
      const r = await client.checkWhitelist(emailAddr, args.address, direction)
      return { success: true, in_contacts: r.in_contacts ?? false, direction: r.direction ?? direction, address: args.address }
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
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key)
  if (args.address) {
    const r = await client.contactGet(args.address)
    if (r.status === 200) return { success: true, profile: r.profile ?? r }
    if (r.status === 404) return { success: true, profile: null }
    return { success: false, error: r.error ?? `HTTP ${r.status}` }
  }
  const name = args.name
  if (!name) {
    return { success: false, error: 'name is required for name lookup' }
  }
  const r = await client.contactSearch(name)
  if (r.status !== 200) {
    return { success: false, error: r.error ?? `HTTP ${r.status}` }
  }
  return { success: true, data: r.data ?? r }
}

export async function setContactProfile(ctx: ToolCtx, args: { address: string; profile: string }): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key)
  const r = await client.contactPut(args.address, args.profile)
  return { success: r.status >= 200 && r.status < 300, ...r }
}

// ── email_summary / set_email_summary ──────────────────────────

export interface EmailSummaryArgs {
  message_id: string
}

export async function emailSummary(ctx: ToolCtx, args: EmailSummaryArgs): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key)
  const r = await client.threadSummaryGet(args.message_id)
  if (r.status === 200) {
    return { success: true, summary: r.summary ?? r.value ?? '' }
  }
  if (r.status === 404) return { success: true, summary: null }
  return { success: false, error: r.error ?? `HTTP ${r.status}` }
}

export async function setEmailSummary(ctx: ToolCtx, args: { message_id: string; summary: string }): Promise<ToolResult> {
  const cfg = await requireConfig(ctx.systemId, ctx.email)
  const client = new GatewayClient(cfg.gateway_url, cfg.api_key)
  const r = await client.threadSummaryPut(args.message_id, args.summary)
  return { success: r.status >= 200 && r.status < 300, ...r }
}
