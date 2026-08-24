/**
 * email_summary / set_email_summary LOCAL threads/{xx}/{tid}.json tests.
 * Contract: mirror Python email_summary/set_email_summary (2026-08-24):
 *   - mid → thread_id resolved via local meta (meta/{xx}/), NOT a gateway call
 *   - read/write threads/{xx}/{thread_id}.json
 *   - empty summary = delete thread file; 2000-char cap + error_code semantics
 * No network I/O (no GatewayClient). FS sandboxed via AIMAIL_HOME → tmp.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { emailSummary, setEmailSummary } from '../src/tools.js'
import { saveLocalMeta, threadPath, agentMailDir } from '../src/meta.js'
import { cleanAddr } from '../src/config.js'

const SYSTEM_ID = 'system-test'
const EMAIL = 'agent1@token.tm'

let home: string

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-thread-'))
  process.env.AIMAIL_HOME = home
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  const cfg = {
    email: EMAIL, gateway_url: 'http://127.0.0.1:9', domain: 'token.tm',
    system_id: SYSTEM_ID, api_key: 'deadbeef', session_id: 'session-1',
  }
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(EMAIL))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(path.join(dir, 'agentmail.json'), JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
})

afterAll(async () => {
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

const ctx = { systemId: SYSTEM_ID, email: EMAIL }

describe('email_summary / set_email_summary (local threads)', () => {
  it('round-trips a summary keyed by the thread resolved from local meta', async () => {
    const mid = '<m1@token.tm>'
    await saveLocalMeta(EMAIL, mid, '<root@token.tm>', EMAIL, 'inbound')

    const set = await setEmailSummary(ctx, { message_id: mid, summary: 'Thread about Q3 budget' })
    expect(set.success).toBe(true)

    // stored under the THREAD id (root), not the message id
    const tpath = threadPath(EMAIL, '<root@token.tm>')
    expect((await fs.stat(tpath)).isFile()).toBe(true)

    const got = await emailSummary(ctx, { message_id: mid })
    expect(got.success).toBe(true)
    expect(got.thread_id).toBe('<root@token.tm>')
    expect(got.summary).toBe('Thread about Q3 budget')
  })

  it('messages sharing a thread read the same summary', async () => {
    const root = '<root@token.tm>'
    const mid1 = '<m1@token.tm>'
    const mid2 = '<m2@token.tm>'
    // both in the same thread (references root)
    await saveLocalMeta(EMAIL, mid1, root, EMAIL, 'inbound')
    await saveLocalMeta(EMAIL, mid2, `${root} ${mid1}`, EMAIL, 'inbound')

    await setEmailSummary(ctx, { message_id: mid1, summary: 'shared' })
    const got = await emailSummary(ctx, { message_id: mid2 })
    expect(got.thread_id).toBe(root)
    expect(got.summary).toBe('shared')
  })

  it('empty summary deletes the thread file (gateway-identical semantics)', async () => {
    const mid = '<m1@token.tm>'
    await saveLocalMeta(EMAIL, mid, '<root@token.tm>', EMAIL, 'inbound')
    await setEmailSummary(ctx, { message_id: mid, summary: 'will be cleared' })

    const del = await setEmailSummary(ctx, { message_id: mid, summary: '   ' })
    expect(del.success).toBe(true)

    const tpath = threadPath(EMAIL, '<root@token.tm>')
    let exists = true
    try { await fs.stat(tpath) } catch { exists = false }
    expect(exists).toBe(false)

    const got = await emailSummary(ctx, { message_id: mid })
    expect(got.summary).toBe('')
  })

  it('missing message_id → error_code MESSAGE_ID_REQUIRED', async () => {
    const r = await setEmailSummary(ctx, { message_id: '   ', summary: 'x' })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('MESSAGE_ID_REQUIRED')
  })

  it('summary over 2000 chars → error_code SUMMARY_TOO_LONG + max_length', async () => {
    const mid = '<m1@token.tm>'
    await saveLocalMeta(EMAIL, mid, '<root@token.tm>', EMAIL, 'inbound')
    const r = await setEmailSummary(ctx, { message_id: mid, summary: 'x'.repeat(2001) })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('SUMMARY_TOO_LONG')
    expect(r.max_length).toBe(2000)
  })

  it('non-string summary → error_code SUMMARY_MUST_BE_STRING', async () => {
    const r = await setEmailSummary(ctx, { message_id: '<m@x>', summary: 123 as unknown as string })
    expect(r.success).toBe(false)
    expect(r.error_code).toBe('SUMMARY_MUST_BE_STRING')
  })

  it('emailSummary with no meta falls back to the mid as thread_id, empty summary', async () => {
    const got = await emailSummary(ctx, { message_id: '<orphan@token.tm>' })
    expect(got.success).toBe(true)
    expect(got.thread_id).toBe('<orphan@token.tm>')
    expect(got.summary).toBe('')
  })

  it('threads dir is sharded by first 2 chars of the thread id', async () => {
    const mid = '<m1@token.tm>'
    await saveLocalMeta(EMAIL, mid, '<root@token.tm>', EMAIL, 'inbound')
    await setEmailSummary(ctx, { message_id: mid, summary: 's' })
    // thread id '<root@token.tm>' sanitizes to 'root_token.tm' → shard 'ro'
    const expected = path.join(agentMailDir(EMAIL), 'threads', 'ro', 'root_token.tm.json')
    expect((await fs.stat(expected)).isFile()).toBe(true)
  })
})
