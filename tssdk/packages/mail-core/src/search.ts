/**
 * Local mail full-text search — outbound snapshot FTS5 index + searchMail.
 * 1:1 semantic port of Python aimail_tools search_mail / _index_* (2026-09-05).
 *
 * Contract: snapshot file lands on disk FIRST, then an incremental FTS5 row
 * is upserted (idempotent by message id). Engine: node:sqlite (built-in,
 * node >= 22.5) with the trigram tokenizer; when unavailable (older node),
 * searchLocalMail falls back to an ordered scan of snapshot JSON files with
 * identical filtering semantics. Failures are silent (warn-only), never
 * blocking mail flow.
 *
 * NOTE: on TS platforms inbound mail intentionally has NO raw snapshot
 * (mail-core preprocess.ts: 'no raw snapshot; log events MUST be kept'), so
 * the TS index covers outbound snapshots only — Python platforms (hermes,
 * deer-flow) index inbound + outbound.
 */
import { promises as fsp } from 'node:fs'
import * as fs from 'node:fs'
import * as path from 'node:path'
import { agentMailDir } from './meta.js'

const TEXT_ATTACH_EXTS = new Set([
  '.txt', '.md', '.markdown', '.json', '.csv', '.eml', '.log',
  '.yaml', '.yml', '.toml', '.xml', '.html', '.py', '.js', '.ts',
  '.rs', '.sh', '.ini', '.conf', '.rst',
])
const ATTACH_MAX = 512 * 1024

export interface IndexRecord {
  mid: string
  dir: string
  ts: string
  subject: string
  fromAddr: string
  toJson: string
  body: string
  attText: string
  threadId: string
}

export interface SearchHit {
  mid: string
  dir: string
  ts: string
  subject: string
  from: string
  to: string[]
  matched_in: string
  snippet: string
  thread_id: string
}

export interface SearchOptions {
  query?: string
  scope?: 'all' | 'inbound' | 'outbound'
  since?: string
  until?: string
  from?: string
  limit?: number
}

export function searchIndexPath(email: string): string {
  return path.join(agentMailDir(email), '.search', 'index.db')
}

export function searchIndexPathForAddr(email: string): string {
  return searchIndexPath(email)
}

// ── index write path ─────────────────────────────────────────────

/** Open (create) the FTS5 index. Returns null when node:sqlite is missing
 * or the db cannot be created (callers then fall back to file scans). */
export async function openSearchIndex(email: string): Promise<any | null> {
  try {
    const mod: any = await import('node:sqlite')
    const DatabaseSync = mod.DatabaseSync
    const p = searchIndexPath(email)
    await fsp.mkdir(path.dirname(p), { recursive: true })
    const db = new DatabaseSync(p)
    try {
      db.exec(
        'CREATE TABLE IF NOT EXISTS emails(' +
        ' mid TEXT PRIMARY KEY, dir TEXT NOT NULL, ts TEXT NOT NULL,' +
        " subject TEXT DEFAULT '', from_addr TEXT DEFAULT ''," +
        " to_json TEXT DEFAULT '', body TEXT DEFAULT ''," +
        " att_text TEXT DEFAULT '', thread_id TEXT DEFAULT '');" +
        "CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(" +
        " subject, body, att_text, tokenize='trigram');",
      )
    } catch {
      // older SQLite without trigram → unicode61 tokenizer
      db.exec(
        'CREATE TABLE IF NOT EXISTS emails(' +
        ' mid TEXT PRIMARY KEY, dir TEXT NOT NULL, ts TEXT NOT NULL,' +
        " subject TEXT DEFAULT '', from_addr TEXT DEFAULT ''," +
        " to_json TEXT DEFAULT '', body TEXT DEFAULT ''," +
        " att_text TEXT DEFAULT '', thread_id TEXT DEFAULT '');" +
        "CREATE VIRTUAL TABLE IF NOT EXISTS emails_fts USING fts5(" +
        ' subject, body, att_text);',
      )
    }
    return db
  } catch {
    return null
  }
}

/** Incremental UPSERT, idempotent by mid. Silent on failure. */
export async function indexSnapshotRecord(email: string, rec: IndexRecord): Promise<void> {
  let db: any = null
  try {
    db = await openSearchIndex(email)
    if (!db) return
    const del = db.prepare(
      'DELETE FROM emails_fts WHERE rowid IN (SELECT rowid FROM emails WHERE mid = ?)',
    )
    del.run(rec.mid)
    db.prepare('DELETE FROM emails WHERE mid = ?').run(rec.mid)
    const ins = db.prepare(
      'INSERT INTO emails(mid,dir,ts,subject,from_addr,to_json,body,att_text,thread_id)' +
      ' VALUES (?,?,?,?,?,?,?,?,?)',
    )
    const r = ins.run(rec.mid, rec.dir, rec.ts, rec.subject, rec.fromAddr,
      rec.toJson, rec.body, rec.attText, rec.threadId)
    db.prepare('INSERT INTO emails_fts(rowid,subject,body,att_text) VALUES (?,?,?,?)')
      .run(Number(r.lastInsertRowid), rec.subject, rec.body, rec.attText)
  } catch (e) {
    console.warn(`[aimail-search] index upsert failed for ${rec.mid}: ${e instanceof Error ? e.message : String(e)}`)
  } finally {
    try { db?.close() } catch { /* ignore */ }
  }
}

// ── attachment text (md-escaped) ─────────────────────────────────

export async function collectAttachmentsMd(paths: string[]): Promise<Array<{ name: string; text: string }>> {
  const out: Array<{ name: string; text: string }> = []
  for (const p of paths || []) {
    try {
      const st = await fsp.stat(p)
      if (!st.isFile() || st.size > ATTACH_MAX) continue
      if (!TEXT_ATTACH_EXTS.has(path.extname(p).toLowerCase())) continue
      const raw = await fsp.readFile(p, 'utf-8')
      const ext = path.extname(p).slice(1).toLowerCase()
      let fence = '```'
      while (fence.length <= raw.length && raw.includes(fence)) fence += '`'
      out.push({ name: path.basename(p), text: `${fence}${ext}\n${raw.trimEnd()}\n${fence}` })
    } catch {
      /* skip unreadable */
    }
  }
  return out
}

export function joinAttMd(attMd: Array<{ text: string }>): string {
  return (attMd || []).map(a => a.text).join('\n\n')
}

// ── search ───────────────────────────────────────────────────────

export interface SearchResult {
  success: boolean
  count?: number
  results?: SearchHit[]
  note?: string
  error_code?: string
  error?: string
}

const DATE_RE = /^\d{4}-\d{2}-\d{2}$/

function makeSnippet(text: string, words: string[], width = 90): string {
  const low = text.toLowerCase()
  let pos = -1
  for (const w of words) {
    const i = low.indexOf(w)
    if (i >= 0) { pos = i; break }
  }
  if (pos < 0) return ''
  const start = Math.max(0, pos - Math.floor(width / 2))
  const end = Math.min(text.length, pos + Math.floor(width / 2))
  const seg = text.slice(start, end).replace(/\n/g, ' ').trim()
  return (start > 0 ? '…' : '') + seg + (end < text.length ? '…' : '')
}

/** Fallback: ordered scan of snapshot JSON files (index unavailable). */
async function scanSnapshotFiles(email: string, words: string[], scope: string,
  since: string, until: string, fromSub: string, limit: number): Promise<SearchHit[]> {
  const root = agentMailDir(email)
  const out: SearchHit[] = []
  let dirs: fs.Dirent[] = []
  try { dirs = await fsp.readdir(root, { withFileTypes: true }) } catch { return out }
  const months = dirs
    .filter(d => d.isDirectory() && /^\d{6}$/.test(d.name))
    .map(d => d.name)
    .sort()
    .reverse()
  const ym = (d: string) => d.slice(0, 4) + d.slice(4, 6)
  for (const m of months) {
    if (since && m < ym(since.replace(/-/g, ''))) continue
    if (until && m > ym(until.replace(/-/g, ''))) continue
    let files: string[] = []
    try { files = await fsp.readdir(path.join(root, m)) } catch { continue }
    files.sort().reverse()
    for (const fn of files) {
      const isIn = fn.startsWith('in-')
      const isOut = fn.startsWith('out-')
      if (!isIn && !isOut) continue
      if (scope === 'inbound' && !isIn) continue
      if (scope === 'outbound' && !isOut) continue
      try {
        const data = JSON.parse(await fsp.readFile(path.join(root, m, fn), 'utf-8'))
        const dir = isIn ? 'inbound' : 'outbound'
        const subject = String(data.subject ?? '')
        const body = String(data.body ?? '')
        const att = joinAttMd(data.attachments_md ?? [])
        const sender = String(data.sender ?? data.from ?? '')
        if (fromSub && !sender.toLowerCase().includes(fromSub.toLowerCase())) continue
        if (words.length) {
          const hay = [subject, body, att].join(' ').toLowerCase()
          if (!words.every(w => hay.includes(w))) continue
        }
        const ts = String(data.ts ?? data.sent_at ?? data.date ?? `${m.slice(0, 4)}-${m.slice(4, 6)}-01`)
        const matched = words.length ? (words.every(w => subject.toLowerCase().includes(w)) ? 'subject'
          : words.every(w => body.toLowerCase().includes(w)) ? 'body'
            : 'attachment') : ''
        out.push({
          mid: fn.replace(/^(in|out)-/, '').replace(/\.json$/, ''),
          dir, ts, subject, from: sender, to: [],
          matched_in: matched,
          snippet: words.length ? makeSnippet(matched === 'attachment' ? att : matched === 'body' ? body : subject, words) : '',
          thread_id: '',
        })
        if (out.length >= limit) return out
      } catch {
        /* skip unparsable */
      }
    }
  }
  return out
}

/** Search the local mail index (offline). See Python search_mail for the
 * canonical contract — this mirrors it exactly. */
export async function searchLocalMail(email: string, opts: SearchOptions = {}): Promise<SearchResult> {
  const scope = opts.scope ?? 'all'
  if (scope !== 'all' && scope !== 'inbound' && scope !== 'outbound') {
    return { success: false, error_code: 'INVALID_SCOPE', error: `scope must be all|inbound|outbound, got ${scope}` }
  }
  const since = opts.since ?? ''
  const until = opts.until ?? ''
  if ((since && !DATE_RE.test(since)) || (until && !DATE_RE.test(until))) {
    return { success: false, error_code: 'INVALID_DATE', error: 'since/until must be YYYY-MM-DD' }
  }
  if (since && until && until < since) {
    return { success: false, error_code: 'INVALID_DATE', error: 'until must not be earlier than since' }
  }
  let limit = typeof opts.limit === 'number' ? Math.trunc(opts.limit) : 20
  if (!Number.isFinite(limit) || limit < 1) limit = 20
  if (limit > 50) limit = 50
  const words = String(opts.query ?? '').toLowerCase().split(/\s+/).filter(Boolean)
  const fromSub = String(opts.from ?? '')

  const db = await openSearchIndex(email)
  let rows: any[] = []
  let usedFts = false
  let note = ''
  if (db) {
    try {
      const where: string[] = []
      const params: unknown[] = []
      if (scope !== 'all') { where.push('dir = ?'); params.push(scope) }
      if (since) { where.push("substr(ts,1,10) >= ?"); params.push(since) }
      if (until) { where.push("substr(ts,1,10) <= ?"); params.push(until) }
      if (fromSub) { where.push('lower(from_addr) LIKE ?'); params.push(`%${fromSub.toLowerCase()}%`) }
      const cond = where.length ? ' AND ' + where.join(' AND ') : ''
      if (words.length) {
        const safe = words.every(w => w.length >= 3 && /^[a-z0-9\u4e00-\u9fff]+$/i.test(w))
        if (safe) {
          try {
            const q = words.map(w => `"${w}"`).join(' AND ')
            rows = db.prepare(
              'SELECT e.mid,e.dir,e.ts,e.subject,e.from_addr,e.to_json,e.body,e.att_text,e.thread_id' +
              ' FROM emails e JOIN emails_fts f ON f.rowid=e.rowid' +
              ` WHERE emails_fts MATCH ?${cond} ORDER BY e.ts DESC LIMIT ?`,
            ).all(q, ...params, limit)
            usedFts = rows.length > 0
          } catch {
            usedFts = false
          }
        }
      }
      if (!usedFts) {
        const likeCols = ['subject', 'body', 'att_text']
        const likeParams: unknown[] = []
        let likeWhere = ''
        if (words.length) {
          const perWord: string[] = []
          for (const w of words) {
            perWord.push('(' + likeCols.map(c => `${c} LIKE ?`).join(' OR ') + ')')
            likeCols.forEach(() => likeParams.push(`%${w}%`))
          }
          likeWhere = ' WHERE ' + perWord.join(' AND ')
        }
        rows = db.prepare(
          'SELECT mid,dir,ts,subject,from_addr,to_json,body,att_text,thread_id FROM emails' +
          `${likeWhere}${cond} ORDER BY ts DESC LIMIT ?`,
        ).all(...likeParams, ...params, limit)
        usedFts = true
      }
    } catch (e) {
      console.warn(`[aimail-search] query failed (fallback scan): ${e instanceof Error ? e.message : String(e)}`)
      rows = []
    } finally {
      if (!rows.length) {
        try {
          const n = db.prepare('SELECT COUNT(*) AS n FROM emails').get() as { n: number }
          if (n.n === 0) note = 'no local mail index yet — snapshots are saved from now on'
        } catch { /* ignore */ }
      }
      try { db.close() } catch { /* ignore */ }
    }
  }
  if (!db) {
    return { success: true, count: 0, results: await scanSnapshotFiles(email, words, scope, since, until, fromSub, limit) }
  }

  const results: SearchHit[] = rows.map((r: any) => {
    let to: string[] = []
    try { to = JSON.parse(r.to_json ?? '[]') } catch { to = [] }
    let matchedIn = ''
    let snippet = ''
    if (words.length) {
      const subjHit = words.every(w => String(r.subject ?? '').toLowerCase().includes(w))
      const bodyHit = words.every(w => String(r.body ?? '').toLowerCase().includes(w))
      const attHit = words.every(w => String(r.att_text ?? '').toLowerCase().includes(w))
      matchedIn = subjHit ? 'subject' : bodyHit ? 'body' : attHit ? 'attachment' : 'subject'
      const src = subjHit ? String(r.subject ?? '') : bodyHit ? String(r.body ?? '') : String(r.att_text ?? '')
      snippet = makeSnippet(src, words)
    }
    return {
      mid: r.mid, dir: r.dir, ts: r.ts, subject: r.subject ?? '',
      from: r.from_addr ?? '', to,
      matched_in: matchedIn, snippet, thread_id: r.thread_id ?? '',
    }
  })
  const out: SearchResult = { success: true, count: results.length, results }
  if (note) out.note = note
  return out
}
