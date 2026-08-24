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
import { AIMAIL_HOME, cleanAddr } from './config.js'

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

/** Per-agent mail data leaf dir: {AIMAIL_HOME}/mail/{clean_addr}/. */
export function agentMailDir(email: string): string {
  return path.join(AIMAIL_HOME(), 'mail', cleanAddr(email))
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
  my_amail_addr: string
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
 * Store per-message metadata (常写 — always written, NOT controlled by
 * save_raw_snapshots). references/thread_id/my_amail_addr replace the former
 * gateway agent_state msg:{mid} key.
 */
export async function saveLocalMeta(
  email: string,
  messageId: string,
  references: string | string[] | undefined,
  myAmailAddr: string,
  direction: string,
): Promise<void> {
  const mid = (messageId || '').trim()
  if (!mid) return
  const refs = normalizeRefs(references)
  const payload: LocalMeta = {
    message_id: mid,
    references: refs,
    thread_id: refs[0] || mid,
    my_amail_addr: myAmailAddr || '',
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
