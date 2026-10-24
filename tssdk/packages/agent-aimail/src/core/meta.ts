/**
 * Local per-message meta + thread-summary layer.
 * 1:1 port of Python aimail_tools.py _save_local_meta / _read_local_meta /
 * _resolve_thread_id / _local_meta_path / _thread_path / _sanitize_message_id.
 *
 * Layout (per-agent leaf dir = {AIMAIL_HOME}/mail/{clean_addr}/):
 *   meta/{first2}/{safe_mid}.json     — always written (NOT gated by snapshot switch)
 *   threads/{first2}/{safe_tid}.json  — thread summary
 * Sharded by first 2 chars of the sanitized id (256 buckets) to avoid flat-dir
 * explosion. Replaces the former gateway agent_state msg:{mid} key (removed
 * gateway-side in the 2026-08-24 localization refactor).
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import { AIMAIL_HOME, cleanAddr, systemIdForEmail } from './config.js'
import { indexSnapshotRecord, collectAttachmentsMd, joinAttMd } from './search.js'

/**
 * Sanitize a Message-ID into a filesystem-safe key (must match Python
 * `_sanitize_message_id`: strip all leading < / trailing >, then map the
 * reserved char set to '_').
 */
export function sanitizeMessageId(messageId: string): string {
  let mid = messageId.trim().replace(/^<+/, '').replace(/>+$/, '')
  for (const ch of '/\\:*?"<>|@ ') {
    mid = mid.split(ch).join('_')
  }
  return mid
}

/** Per-agent mail data leaf: {AIMAIL_HOME}/systems/{sid}/{clean_addr}/mail/
 * (Three-layer convergence 2026-09-23, top-level no longer has mail/; in/out snapshots, attch, meta, threads,
 * .search entire leaf belongs to agent layer; sid resolution failure converges to _unassigned). */
export function agentMailDir(email: string): string {
  const sid = systemIdForEmail(email) || '_unassigned'
  return path.join(AIMAIL_HOME(), 'systems', sid, cleanAddr(email), 'mail')
}

/** meta/{first2}/{safe_mid}.json — first-2-char shard (256 buckets). */
export function localMetaPath(email: string, messageId: string): string {
  const k = sanitizeMessageId(messageId)
  return path.join(agentMailDir(email), 'meta', k.slice(0, 2), `${k}.json`)
}

/** threads/{first2}/{safe_tid}.json — same sharding strategy as meta. */
export function threadPath(email: string, threadId: string): string {
  const k = sanitizeMessageId(threadId)
  return path.join(agentMailDir(email), 'threads', k.slice(0, 2), `${k}.json`)
}

export interface LocalMeta {
  message_id: string
  references: string[]
  thread_id: string
  my_aimail_addr: string
  direction: string
  at: string
  [k: string]: unknown
}

function normalizeRefs(references: string | string[] | undefined): string[] {
  if (Array.isArray(references)) {
    return references.map(r => String(r).trim()).filter(Boolean)
  }
  if (typeof references === 'string') {
    return references.split(/\s+/).map(r => r.trim()).filter(Boolean)
  }
  return []
}

/**
 * Store per-message metadata (frequently written — always written, NOT controlled by
 * save_raw_snapshots). references/thread_id/my_aimail_addr replace the former
 * gateway agent_state msg:{mid} key.
 */
export async function saveLocalMeta(
  email: string,
  messageId: string,
  references: string | string[] | undefined,
  myAimailAddr: string,
  direction: string,
): Promise<void> {
  const mid = (messageId || '').trim()
  if (!mid) return
  const refs = normalizeRefs(references)
  const payload: LocalMeta = {
    message_id: mid,
    references: refs,
    thread_id: refs[0] || mid,
    my_aimail_addr: myAimailAddr || '',
    direction,
    at: new Date().toISOString(),
  }
  try {
    const p = localMetaPath(email, mid)
    await fs.mkdir(path.dirname(p), { recursive: true })
    const tmp = `${p}.tmp`
    await fs.writeFile(tmp, JSON.stringify(payload, null, 2), 'utf-8')
    await fs.rename(tmp, p)
  } catch (e) {
    console.warn(`Failed to save local meta for ${mid}: ${e instanceof Error ? e.message : String(e)}`)
  }
}

/** Load per-message metadata from the local mail dir. undefined if not found. */
export async function readLocalMeta(
  email: string,
  messageId: string,
): Promise<LocalMeta | undefined> {
  const mid = (messageId || '').trim()
  if (!mid) return undefined
  try {
    const raw = await fs.readFile(localMetaPath(email, mid), 'utf-8')
    return JSON.parse(raw) as LocalMeta
  } catch {
    return undefined
  }
}

/** thread_id = first References entry (thread root), else the message_id itself. */
export async function resolveThreadId(email: string, messageId: string): Promise<string> {
  const meta = await readLocalMeta(email, messageId)
  return meta?.thread_id || (messageId || '').trim()
}

/**
 * Save an outbound email snapshot to {leaf}/{yyyymm}/out-{safe}.json, copying
 * resolved attachments to {leaf}/{yyyymm}/attch/{safe}/. 1:1 port of Python
 * `_save_outbound_snapshot`. Gated by save_raw_snapshots at the call site
 * (meta is always written regardless). Failures are non-fatal (warn only).
 */
export async function saveOutboundSnapshot(
  email: string,
  outMsgId: string,
  sender: string,
  to: string,
  subject: string,
  body: string,
  ccList: string[],
  resolvedPaths: string[],
  attachmentIds: Array<{ id: string }>,
  inReplyTo: string,
  references: string,
): Promise<void> {
  const safeMid = sanitizeMessageId(outMsgId)
  const now = new Date()
  const yyyymm = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}`
  const leaf = agentMailDir(email)
  const snapshotDir = path.join(leaf, yyyymm)
  const snapshotPath = path.join(snapshotDir, `out-${safeMid}.json`)

  const localAtts: string[] = []
  if (resolvedPaths.length) {
    const attchDir = path.join(snapshotDir, 'attch', safeMid)
    try {
      await fs.mkdir(attchDir, { recursive: true })
      for (const src of resolvedPaths) {
        try {
          const name = path.basename(src)
          const dest = path.join(attchDir, name)
          await fs.copyFile(src, dest)
          localAtts.push(dest)
        } catch {
          /* skip unreadable */
        }
      }
    } catch (e) {
      console.warn(`Failed to copy outbound attachments for ${safeMid}: ${e instanceof Error ? e.message : String(e)}`)
    }
  }

  const attMd = await collectAttachmentsMd(localAtts)
  const payload: Record<string, unknown> = {
    message_id: outMsgId,
    direction: 'outbound',
    sender,
    to,
    cc: ccList.length ? ccList.join(', ') : '',
    subject,
    body,
    attachments: localAtts,
    attachment_ids: attachmentIds,
    in_reply_to: inReplyTo,
    references,
    sent_at: now.toISOString(),
  }
  if (attMd.length) payload.attachments_md = attMd
  try {
    await fs.mkdir(snapshotDir, { recursive: true })
    const tmp = `${snapshotPath}.tmp`
    await fs.writeFile(tmp, JSON.stringify(payload, null, 2), 'utf-8')
    await fs.rename(tmp, snapshotPath)
    // Incremental FTS5 index AFTER the snapshot file is on disk.
    const toList = to.split(',').map(t => t.trim()).filter(Boolean).concat(ccList)
    await indexSnapshotRecord(email, {
      mid: outMsgId,
      dir: 'outbound',
      ts: now.toISOString(),
      subject: subject ?? '',
      fromAddr: sender ?? '',
      toJson: JSON.stringify(toList),
      body: body ?? '',
      attText: joinAttMd(attMd),
      threadId: '',
    })
  } catch (e) {
    console.warn(`Failed to save outbound email snapshot for ${safeMid}: ${e instanceof Error ? e.message : String(e)}`)
  }
}

/**
 * Save an inbound snapshot ({yyyymm}/in-{safe}.json) + its FTS row, from the
 * AGENT-VISIBLE payload (after preprocessing). 1:1 with the Python
 * `store_inbound_message`: local meta is always written (reply threading reads
 * it), the snapshot itself is gated by `save_raw_snapshots` at the call site.
 * Failures are non-fatal (warn only).
 */
export async function saveInboundSnapshot(
  email: string,
  messageId: string,
  payload: Record<string, unknown>,
  references: string | string[] | undefined = undefined,
  myAimailAddr: string = '',
): Promise<void> {
  const mid = (messageId || '').trim()
  if (!mid) return
  await saveLocalMeta(email, mid, references, myAimailAddr || email, 'inbound')
  const safeMid = sanitizeMessageId(mid)
  const now = new Date()
  const yyyymm = `${now.getFullYear()}${String(now.getMonth() + 1).padStart(2, '0')}`
  const snapshotDir = path.join(agentMailDir(email), yyyymm)
  const snapshotPath = path.join(snapshotDir, `in-${safeMid}.json`)
  try {
    await fs.mkdir(snapshotDir, { recursive: true })
    const tmp = `${snapshotPath}.tmp`
    await fs.writeFile(tmp, JSON.stringify(payload, null, 2), 'utf-8')
    await fs.rename(tmp, snapshotPath)
    // Incremental FTS5 index AFTER the snapshot file is on disk.
    await indexSnapshotRecord(email, {
      mid,
      dir: 'inbound',
      ts: String(payload['ts'] ?? payload['date'] ?? now.toISOString()),
      subject: String(payload['subject'] ?? ''),
      fromAddr: String(payload['sender'] ?? payload['from'] ?? ''),
      toJson: JSON.stringify(payload['to'] ?? ''),
      body: String(payload['body'] ?? ''),
      attText: '',
      threadId: '',
    })
  } catch (e) {
    console.warn(`Failed to save inbound email snapshot for ${safeMid}: ${e instanceof Error ? e.message : String(e)}`)
  }
}
