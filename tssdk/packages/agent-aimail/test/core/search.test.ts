/**
 * Local mail search tests (search.ts).
 *
 * Regression (L3 2026-11-02, dsh-aimail grids): empty query + filter built
 * `... FROM emails AND dir=?` (leading ' AND ' when the words clause was
 * empty) → SQLite syntax error swallowed by the catch → silent 0 results.
 * The fallback WHERE is now assembled as one condition array; these tests
 * pin every empty-words + filter shape plus the untouched FTS branch.
 *
 * All filesystem access sandboxed via AIMAIL_HOME → tmp dir (meta.test.ts pattern).
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  searchLocalMail,
  indexSnapshotRecord,
  type IndexRecord,
} from '../../src/core/search.js'

const EMAIL = 'agent1@token.tm'

const REC_IN: IndexRecord = {
  mid: '<in-1@token.tm>',
  dir: 'inbound',
  ts: '2026-11-01T10:00:00.000Z',
  subject: 'Quarterly report',
  fromAddr: 'boss@corp.example',
  toJson: JSON.stringify(['agent1@token.tm']),
  body: 'numbers attached',
  attText: '',
  threadId: '',
}
const REC_OUT: IndexRecord = {
  mid: '<out-1@token.tm>',
  dir: 'outbound',
  ts: '2026-11-02T12:00:00.000Z',
  subject: 'Re: meeting',
  fromAddr: 'agent1@token.tm',
  toJson: JSON.stringify(['peer@corp.example']),
  body: 'ok then',
  attText: '',
  threadId: '',
}

let home: string

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-search-'))
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  await indexSnapshotRecord(EMAIL, REC_IN)
  await indexSnapshotRecord(EMAIL, REC_OUT)
})

afterAll(async () => {
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('empty query + filters (L3 regression: leading AND)', () => {
  it('scope filter alone returns the filtered rows (not a swallowed SQL error)', async () => {
    const r = await searchLocalMail(EMAIL, { scope: 'inbound' })
    expect(r.success).toBe(true)
    expect(r.count).toBe(1)
    expect(r.results![0].dir).toBe('inbound')
    expect(r.results![0].mid).toBe(REC_IN.mid)
  })

  it('since filter alone returns only later rows', async () => {
    const r = await searchLocalMail(EMAIL, { since: '2026-11-02' })
    expect(r.success).toBe(true)
    expect(r.count).toBe(1)
    expect(r.results![0].mid).toBe(REC_OUT.mid)
  })

  it('no query and no filter returns all rows, newest first', async () => {
    const r = await searchLocalMail(EMAIL, {})
    expect(r.success).toBe(true)
    expect(r.count).toBe(2)
    expect(r.results![0].mid).toBe(REC_OUT.mid)
    expect(r.results![1].mid).toBe(REC_IN.mid)
  })
})

describe('query + filters (normal path)', () => {
  it('query word + scope filter still filters', async () => {
    const r = await searchLocalMail(EMAIL, { query: 'quarterly', scope: 'inbound' })
    expect(r.success).toBe(true)
    expect(r.count).toBe(1)
    expect(r.results![0].mid).toBe(REC_IN.mid)
  })

  it('FTS branch (safe query, no filter) is unaffected', async () => {
    const r = await searchLocalMail(EMAIL, { query: 'quarterly' })
    expect(r.success).toBe(true)
    expect(r.count).toBe(1)
    expect(r.results![0].mid).toBe(REC_IN.mid)
    expect(r.results![0].matched_in).toBe('subject')
  })
})
