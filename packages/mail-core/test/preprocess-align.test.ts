/**
 * Task 5 inbound preprocess alignment tests (preprocess.ts).
 * Contract: mirror Python aimail_base.py (2026-08-24):
 *   - D8: board_id = sha256("{short}.a2a@{gw-domain}")[:20] (gateway
 *     derive_board_id), NOT a body-regex guess
 *   - D14: board_creds.json stores {gateway_url, token}
 *   - D9: pong body = {"ping_id", "event": {"mail_id"}}, pong_sent log
 *     carries the REAL outcome (ok/error), keyed on the ping's mail_id
 *   - D11: [WHOAMI] → _whoami_prompt (role file, {{KEY}} fill) + early return;
 *     board_id+board_role → _role_prompt (3-level role lookup)
 *   - inbound local meta is always written (meta/{xx}/{mid}.json, direction
 *     inbound)
 * Fetch is stubbed (pong send); FS sandboxed via AIMAIL_HOME → tmp.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { createHash } from 'node:crypto'
import { processInboundMail, fillTemplate } from '../src/preprocess.js'
import { readLocalMeta, localMetaPath } from '../src/meta.js'
import { cleanAddr, systemDir } from '../src/config.js'
import type { InboundPayload } from '../src/types.js'

const SYSTEM_ID = 'system-test'
const EMAIL = 'agent1@token.tm'

let home: string
let sendBodies: Array<{ url: string; body: Record<string, unknown> }>

function mail(over: Partial<InboundPayload> = {}): InboundPayload {
  return {
    mail_id: 'm-1',
    message_id: '<in-1@token.tm>',
    subject: 'hello',
    body: 'hi there',
    from: 'peer@example.com',
    to: [EMAIL],
    ...over,
  }
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-t5-'))
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

  sendBodies = []
  const mock = vi.fn(async (url: string, init?: RequestInit) => {
    const u = String(url)
    if (u.includes('/api/v1/send')) {
      sendBodies.push({ url: u, body: JSON.parse(new TextDecoder().decode(init?.body as ArrayBuffer)) })
      return new Response(JSON.stringify({ status: 200 }), { status: 200 })
    }
    return new Response('{}', { status: 200 })
  })
  vi.stubGlobal('fetch', mock)
})

afterAll(async () => {
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

const CTX = { systemId: SYSTEM_ID, email: EMAIL }

describe('D8: board_id sha256 derivation (gateway derive_board_id)', () => {
  it('derives board_id from {short}.a2a@{gw-domain} and stores creds {gateway_url, token}', async () => {
    const short = 'proj1'
    const gwUrl = 'https://board.example.com'
    const payload = mail({
      from: `${short}.a2a@board.example.com`,
      subject: 'task assigned',
      body: 'API: https://board.example.com\nToken: bdt_secret123\nboard_id: GUESS-WRONG-VALUE',
    })
    // the body-regex value must NOT win
    await processInboundMail(payload, {}, CTX)

    const expected = createHash('sha256').update(`${short}.a2a@board.example.com`, 'utf-8').digest('hex').slice(0, 20)
    const credsPath = path.join(systemDir(SYSTEM_ID), cleanAddr(EMAIL), 'board_creds.json')
    const creds = JSON.parse(await fs.readFile(credsPath, 'utf-8')) as Record<string, { gateway_url?: string; token?: string }>
    expect(Object.keys(creds)).toEqual([expected])
    expect(creds[expected]).toEqual({ gateway_url: gwUrl, token: 'bdt_secret123' })
    expect(creds[expected]!.token).not.toBe('GUESS-WRONG-VALUE')
  })

  it('fixed vector: proj1.a2a@board.example.com → 2ec629a788e961ab1bee (Python sha256 cross-check)', async () => {
    const expected = '2ec629a788e961ab1bee'
    const short = 'proj1'
    const payload = mail({
      from: `${short}.a2a@board.example.com`,
      body: 'API: https://board.example.com\nToken: bdt_t',
    })
    await processInboundMail(payload, {}, CTX)
    const credsPath = path.join(systemDir(SYSTEM_ID), cleanAddr(EMAIL), 'board_creds.json')
    const creds = JSON.parse(await fs.readFile(credsPath, 'utf-8')) as Record<string, unknown>
    expect(creds).toHaveProperty(expected)
  })
})

describe('D9: pong body + real pong_sent status', async () => {
  it('sends pong with ping_id/event.mail_id body, keyed on the ping mail_id', async () => {
    const payload = mail({
      subject: '__agentmail_ping__:ping-abc',
      mail_id: 'm-ping-1',
    })
    const r = await processInboundMail(payload, {}, CTX)
    expect(r).toBeNull()

    const pong = sendBodies.find(b => String((b.body.subject as string) ?? '').startsWith('__amail_pong__:'))
    expect(pong).toBeDefined()
    const bodyObj = JSON.parse(String(pong!.body.markdown)) as { ping_id: string; event: { mail_id: string } }
    expect(bodyObj.ping_id).toBe('ping-abc')
    expect(bodyObj.event.mail_id).toBe('m-ping-1')
    // pong is a reply to the ping mail (threading resolves)
    const hdrs = pong!.body.headers as Record<string, string>
    expect(hdrs['In-Reply-To']).toBe('m-ping-1')
  })

  it('pong_sent log carries the real outcome (ok for a 200 send)', async () => {
    const payload = mail({ subject: '__agentmail_ping__:ping-xyz', mail_id: 'm-p' })
    await processInboundMail(payload, {}, CTX)
    const logPath = path.join(home, 'logs', `agentmail.${cleanAddr(EMAIL)}.log`)
    const lines = (await fs.readFile(logPath, 'utf-8')).trim().split('\n').map(l => JSON.parse(l))
    const pongSent = lines.find(l => l.dir === 'pong_sent')
    expect(pongSent).toBeDefined()
    expect(pongSent!.pong_status).toBe('ok')
  })
})

describe('D11: role template injection', () => {
  async function writeRole(rel: string, content: string): Promise<void> {
    const p = path.join(home, rel)
    await fs.mkdir(path.dirname(p), { recursive: true })
    await fs.writeFile(p, content, 'utf-8')
  }

  it('[WHOAMI] injects _whoami_prompt from role file with {{KEY}} fill (and early-returns)', async () => {
    await writeRole(
      `systems/${SYSTEM_ID}/board/role_prompt/whoami.md`,
      'You are {{AGENTMAIL_ADDRESS}}. Reply whoami for {{INQUIRY_SUBJECT}}.',
    )
    const r = await processInboundMail(mail({ subject: '[WHOAMI] who are you' }), {}, CTX)
    expect(r?._whoami_update_public).toBe(true)
    expect(r?._whoami_prompt).toBe(`You are ${EMAIL}. Reply whoami for [WHOAMI] who are you.`)
  })

  it('address-level role overrides system-level', async () => {
    await writeRole(`systems/${SYSTEM_ID}/board/role_prompt/worker.md`, 'SYSTEM worker for {{BOARD_ID}}')
    await writeRole(`systems/${SYSTEM_ID}/${cleanAddr(EMAIL)}/role_prompt/worker.md`, 'ADDR worker for {{BOARD_ID}}')
    const r = await processInboundMail(mail({ board_id: 'B-1', board_role: 'worker' }), {}, CTX)
    expect(r?._role_prompt).toBe('ADDR worker for B-1')
  })

  it('common.md is the fallback when the named role is missing', async () => {
    await writeRole(`systems/${SYSTEM_ID}/board/role_prompt/common.md`, 'COMMON for {{BOARD_ROLE}}')
    const r = await processInboundMail(mail({ board_id: 'B-2', board_role: 'missing-role' }), {}, CTX)
    expect(r?._role_prompt).toBe('COMMON for missing-role')
  })

  it('no role file → no _role_prompt, session key still set', async () => {
    const r = await processInboundMail(mail({ board_id: 'B-3', board_role: 'ghost' }), {}, CTX)
    expect(r?._role_prompt).toBeUndefined()
    expect(r?._a2a_session_key).toBe(`a2a:B-3:peer@example.com`)
  })
})

describe('inbound local meta (always written)', () => {
  it('writes meta/{xx}/{mid}.json with direction inbound + references', async () => {
    await processInboundMail(mail({ references: ['<root@token.tm>', '<in-1@token.tm>'] }), {}, CTX)
    const m = await readLocalMeta(EMAIL, '<in-1@token.tm>')
    expect(m).toBeDefined()
    expect(m!.direction).toBe('inbound')
    expect(m!.my_amail_addr).toBe(EMAIL)
    expect(m!.references).toEqual(['<root@token.tm>', '<in-1@token.tm>'])
    expect(m!.thread_id).toBe('<root@token.tm>')
    expect((await fs.stat(localMetaPath(EMAIL, '<in-1@token.tm>'))).isFile()).toBe(true)
  })
})

describe('fillTemplate', () => {
  it('replaces all occurrences of each {{KEY}}', () => {
    expect(fillTemplate('{{A}}-{{B}}-{{A}}', { A: 'x', B: 'y' })).toBe('x-y-x')
  })
})
