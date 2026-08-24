/**
 * Local meta / thread layer tests (meta.ts).
 * Contract: 1:1 mirror of Python aimail_tools.py local meta functions.
 *   - sanitizeMessageId (strip all leading < / trailing >, reserved chars → _)
 *   - meta/{first2}/{safe}.json sharding (256 buckets)
 *   - save/read round-trip, thread_id resolution
 *   - always-written (NOT gated by any snapshot switch)
 * All filesystem access sandboxed via AIMAIL_HOME → tmp dir.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  sanitizeMessageId,
  agentMailDir,
  localMetaPath,
  threadPath,
  saveLocalMeta,
  readLocalMeta,
  resolveThreadId,
} from '../src/meta.js'
import { cleanAddr } from '../src/config.js'

const EMAIL = 'agent1@token.tm'

let home: string

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-meta-'))
  process.env.AIMAIL_HOME = home
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
})

afterAll(async () => {
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('sanitizeMessageId', () => {
  it('strips angle brackets and maps reserved chars to _', () => {
    expect(sanitizeMessageId('<abc-123@token.tm>')).toBe('abc-123_token.tm')
  })
  it('strips ALL leading < and trailing > (matches Python lstrip/rstrip)', () => {
    expect(sanitizeMessageId('<<x>>')).toBe('x')
  })
  it('maps the full reserved set /\\:*?"<>|@ space', () => {
    expect(sanitizeMessageId('a/b\\c:d*e?f"g<h>i|j k@l')).toBe('a_b_c_d_e_f_g_h_i_j_k_l')
  })
})

describe('path sharding', () => {
  it('meta path sharded by first 2 chars of sanitized id', () => {
    const p = localMetaPath(EMAIL, '<deadbeef-1111-2222-3333-444444444444@token.tm>')
    const safe = sanitizeMessageId('<deadbeef-1111-2222-3333-444444444444@token.tm>')
    expect(p).toBe(path.join(agentMailDir(EMAIL), 'meta', safe.slice(0, 2), `${safe}.json`))
    expect(p).toContain(path.join('mail', cleanAddr(EMAIL), 'meta'))
  })
  it('thread path uses same sharding under threads/', () => {
    const p = threadPath(EMAIL, 'tid-abc')
    expect(p).toBe(path.join(agentMailDir(EMAIL), 'threads', 'ti', 'tid-abc.json'))
  })
})

describe('saveLocalMeta / readLocalMeta', () => {
  it('round-trips all fields', async () => {
    const mid = '<m1@token.tm>'
    await saveLocalMeta(EMAIL, mid, '<root@x> <mid1@x>', 'persona@token.tm', 'outbound')
    const m = await readLocalMeta(EMAIL, mid)
    expect(m).toBeDefined()
    // message_id keeps angle brackets (Python _save_local_meta only strips
    // whitespace; sanitize applies to the filesystem key only)
    expect(m!.message_id).toBe('<m1@token.tm>')
    // references keep angle brackets (Python _save_local_meta only trims
    // whitespace; sanitize applies to the filesystem key only)
    expect(m!.references).toEqual(['<root@x>', '<mid1@x>'])
    expect(m!.thread_id).toBe('<root@x>')
    expect(m!.my_amail_addr).toBe('persona@token.tm')
    expect(m!.direction).toBe('outbound')
    expect(typeof m!.at).toBe('string')
  })

  it('accepts references as a space-separated string', async () => {
    await saveLocalMeta(EMAIL, '<m2@token.tm>', 'a@x b@x c@x', '', 'inbound')
    const m = await readLocalMeta(EMAIL, '<m2@token.tm>')
    expect(m!.references).toEqual(['a@x', 'b@x', 'c@x'])
  })

  it('thread_id falls back to message_id when no references', async () => {
    await saveLocalMeta(EMAIL, '<solo@token.tm>', '', '', 'outbound')
    const m = await readLocalMeta(EMAIL, '<solo@token.tm>')
    expect(m!.thread_id).toBe('<solo@token.tm>')
    expect(m!.references).toEqual([])
  })

  it('returns undefined for a missing id', async () => {
    expect(await readLocalMeta(EMAIL, '<nope@token.tm>')).toBeUndefined()
  })

  it('is always written (no snapshot switch gate)', async () => {
    // No config / snapshot flag involved — the file must exist on disk.
    await saveLocalMeta(EMAIL, '<always@token.tm>', '', '', 'outbound')
    const p = localMetaPath(EMAIL, '<always@token.tm>')
    expect((await fs.stat(p)).isFile()).toBe(true)
  })

  it('ignores empty message id (no-op)', async () => {
    await saveLocalMeta(EMAIL, '   ', '', '', 'outbound')
    expect(await readLocalMeta(EMAIL, '' as unknown as string)).toBeUndefined()
  })
})

describe('resolveThreadId', () => {
  it('resolves to stored thread_id', async () => {
    await saveLocalMeta(EMAIL, '<r1@token.tm>', '<root@x> <r1@x>', '', 'outbound')
    expect(await resolveThreadId(EMAIL, '<r1@token.tm>')).toBe('<root@x>')
  })
  it('falls back to the message id itself when no meta', async () => {
    expect(await resolveThreadId(EMAIL, '<orphan@token.tm>')).toBe('<orphan@token.tm>')
  })
})
