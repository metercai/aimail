/**
 * sendMail 先存再调 tests (tools.ts + meta.ts).
 * Contract: mirror Python send_mail (2026-08-24 localization):
 *   - Message-ID generated locally ONCE, stored to local meta BEFORE the API
 *     call, and sent as the Message-ID header — the API-returned id is NOT
 *     written back (no email_id backfill).
 *   - outbox snapshot (out-{safe}.json + attch copy) only when
 *     save_raw_snapshots=true; meta is always written.
 *   - recipients stay lists end-to-end; payload.to is ','-joined (Python parity).
 * fetch is stubbed to capture the request and return a canned API response.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { sendMail } from '../src/tools.js'
import { readLocalMeta, agentMailDir, saveLocalMeta } from '../src/meta.js'
import { cleanAddr } from '../src/config.js'

const SYSTEM_ID = 'system-test'
const EMAIL = 'agent1@token.tm'
const FAKE_GATEWAY = 'http://127.0.0.1:9' // never reached; fetch is stubbed

let home: string

async function writeConfigSync(extra: Record<string, unknown> = {}): Promise<void> {
  const cfg: Record<string, unknown> = {
    email: EMAIL,
    gateway_url: FAKE_GATEWAY,
    domain: 'token.tm',
    system_id: SYSTEM_ID,
    api_key: 'deadbeef',
    session_id: 'session-1',
    ...extra,
  }
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(EMAIL))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(path.join(dir, 'agentmail.json'), JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
}

/** List all local meta json files for the agent (across shards). */
async function findMetaFiles(): Promise<string[]> {
  const base = path.join(agentMailDir(EMAIL), 'meta')
  try {
    const out: string[] = []
    for (const shard of await fs.readdir(base)) {
      const shardDir = path.join(base, shard)
      if (!shardDir) continue
      for (const f of await fs.readdir(shardDir)) {
        out.push(path.join(shardDir, f))
      }
    }
    return out
  } catch {
    return []
  }
}

/** Stub global fetch; capture send payloads; canned responses per route. */
function stubFetch(
  overrides: Partial<Record<'send' | 'upload', [number, Record<string, unknown>]>> = {},
): { sendBodies: Array<Record<string, unknown>>; restore: () => void } {
  const sendBodies: Array<Record<string, unknown>> = []
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url)
    let status = 200
    let payload: Record<string, unknown> = { status: 200 }
    if (u.includes('/api/v1/send')) {
      sendBodies.push(JSON.parse(new TextDecoder().decode(init?.body as ArrayBuffer)))
      ;[status, payload] = overrides.send ?? [200, { email_id: '<api-returned-id@token.tm>', status: 200 }]
    } else if (u.includes('/api/v1/upload')) {
      ;[status, payload] = overrides.upload ?? [201, { attachment_id: 'att-1', status: 201 }]
    }
    return new Response(JSON.stringify(payload), { status, headers: { 'Content-Type': 'application/json' } })
  })
  vi.stubGlobal('fetch', mock)
  return { sendBodies, restore: () => vi.unstubAllGlobals() }
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-send-'))
  process.env.AIMAIL_HOME = home
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  await writeConfigSync()
})

afterAll(async () => {
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('sendMail 先存再调', () => {
  const ctx = { systemId: SYSTEM_ID, email: EMAIL }

  it('stores local meta with the locally generated mid; API-returned id not backfilled', async () => {
    const { sendBodies, restore } = stubFetch()
    const res = await sendMail(ctx, { to: ['a@x', 'b@x'], subject: 'hi', body: 'yo' })
    restore()
    expect(res.success).toBe(true)

    // exactly one local meta, written under the LOCALLY generated mid
    const metas = await findMetaFiles()
    expect(metas.length).toBe(1)
    const raw = JSON.parse(await fs.readFile(metas[0]!, 'utf-8')) as {
      message_id: string; direction: string; my_amail_addr: string
    }
    expect(raw.message_id).toMatch(/^<[0-9a-f]{32}@token\.tm>$/)
    expect(raw.direction).toBe('outbound')
    expect(raw.my_amail_addr).toBe(EMAIL)

    // the Message-ID sent to the gateway == the local mid (local value IS the wire value)
    const sent = sendBodies[0]!
    expect((sent.headers as Record<string, string>)['Message-ID']).toBe(raw.message_id)

    // the API returned a DIFFERENT id — it must NOT have been written back
    expect('<api-returned-id@token.tm>').not.toBe(raw.message_id)
    const metaByLocal = await readLocalMeta(EMAIL, raw.message_id)
    expect(metaByLocal?.message_id).toBe(raw.message_id)
  })

  it('payload.to/cc are comma-joined lists (Python parity, no space)', async () => {
    const { sendBodies, restore } = stubFetch()
    await sendMail(ctx, { to: ['a@x', 'b@x'], cc: ['c@x'], subject: 'hi', body: 'yo' })
    restore()
    const sent = sendBodies[0]!
    expect(sent.to).toBe('a@x,b@x')
    expect(sent.cc).toBe('c@x')
  })

  it('does NOT write an outbox snapshot by default (save_raw_snapshots unset)', async () => {
    const { restore } = stubFetch()
    await sendMail(ctx, { to: 'a@x', subject: 'hi', body: 'yo' })
    restore()
    const leaf = agentMailDir(EMAIL)
    const entries = await fs.readdir(leaf)
    // no {yyyymm} outbox snapshot dir (meta + threads are always written)
    expect(entries.some(e => /^\d{6}$/.test(e))).toBe(false)
    expect(entries).toContain('meta')
    // thread bootstrap did write a thread file
    expect(entries).toContain('threads')
  })

  it('writes outbox snapshot + attch copy when save_raw_snapshots=true', async () => {
    await fs.rm(home, { recursive: true, force: true })
    await fs.mkdir(home, { recursive: true })
    await writeConfigSync({ save_raw_snapshots: true })

    const att = path.join(home, 'report.txt')
    await fs.writeFile(att, 'quarterly numbers')

    const { sendBodies, restore } = stubFetch()
    const res = await sendMail(ctx, { to: 'a@x', subject: 'q3', body: 'see attach', attachments: [att] })
    restore()
    expect(res.success).toBe(true)

    const leaf = agentMailDir(EMAIL)
    const entries = await fs.readdir(leaf)
    const yyyymm = entries.find(e => /^\d{6}$/.test(e))!
    expect(yyyymm).toBeDefined()
    const snapFiles = await fs.readdir(path.join(leaf, yyyymm))
    expect(snapFiles.some(f => f.startsWith('out-') && f.endsWith('.json'))).toBe(true)
    expect(snapFiles).toContain('attch')

    const snap = JSON.parse(await fs.readFile(
      path.join(leaf, yyyymm, snapFiles.find(f => f.startsWith('out-'))!), 'utf-8')) as Record<string, unknown>
    expect(snap.direction).toBe('outbound')
    expect(snap.sender).toBe(EMAIL)
    expect(snap.to).toBe('a@x')
    expect(snap.subject).toBe('q3')
    expect(snap.attachment_ids).toEqual([{ id: 'att-1' }])
    expect(Array.isArray(snap.attachments) && (snap.attachments as string[]).length).toBe(1)

    const attchSubs = await fs.readdir(path.join(leaf, yyyymm, 'attch'))
    expect(attchSubs.length).toBe(1)
    const copied = await fs.readdir(path.join(leaf, yyyymm, 'attch', attchSubs[0]!))
    expect(copied).toContain('report.txt')
    expect(await fs.readFile(path.join(leaf, yyyymm, 'attch', attchSubs[0]!, 'report.txt'), 'utf-8')).toBe('quarterly numbers')

    expect(sendBodies[0]!.attachments).toEqual([{ id: 'att-1' }])
  })

  it('keeps local meta even when the API call fails (failure does not roll back)', async () => {
    const { restore } = stubFetch({ send: [500, { error: 'boom' }] })
    const res = await sendMail(ctx, { to: 'a@x', subject: 'hi', body: 'yo' })
    restore()
    expect(res.success).toBe(false)
    expect(await findMetaFiles()).toHaveLength(1)
  })

  it('reply path: threading headers come from the referenced mid local meta', async () => {
    const refMid = '<orig-1234@token.tm>'
    await saveLocalMeta(EMAIL, refMid, '<root@token.tm>', 'persona@token.tm', 'inbound')

    const { sendBodies, restore } = stubFetch()
    const res = await sendMail(ctx, { to: 'persona@token.tm', subject: 're: hi', body: 'yo', message_id: refMid })
    restore()
    expect(res.success).toBe(true)
    const sent = sendBodies[0]!
    const hdrs = sent.headers as Record<string, string>
    // In-Reply-To set (not a forward), References = original refs + refMid (deduped)
    expect(hdrs['In-Reply-To']).toBe(refMid)
    expect(hdrs['References']).toBe(`<root@token.tm> ${refMid}`)
    // sender persona honored from stored inbound meta
    expect(sent.sender).toBe('persona@token.tm')
  })
})
