/**
 * Minimal contract tests for the inbound chain:
 *   - verifySignature (webhook HMAC)
 *   - cleanAddr / parseAmailPersona / baseEmail (address contract)
 *   - processInboundMail (15-step preprocess + B1/B2/B3 + ping/pong intercept + logs)
 *
 * All filesystem access is sandboxed via AIMAIL_HOME → tmp dir.
 * The gateway URL points at 127.0.0.1:9 (discard, immediate ECONNREFUSED)
 * so no real network I/O happens.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { createHmac } from 'node:crypto'
import {
  verifySignature,
  processInboundMail,
  parseAmailPersona,
  baseEmail,
  PING_PREFIX,
  PONG_PREFIX,
} from '../src/preprocess.js'
import { cleanAddr } from '../src/config.js'
import { threadPath } from '../src/meta.js'
import type { InboundPayload } from '../src/types.js'

const SYSTEM_ID = 'system-test'
const AGENT_EMAIL = 'agent1@token.tm'
const FAKE_GATEWAY = 'http://127.0.0.1:9' // discard port → ECONNREFUSED, no real I/O

/** Make the tool's retry backoff sleeps (1s/2s/4s/8s) elapse instantly.
 *  Only those exact delays are intercepted — vitest's own test-timeout
 *  timer (5s) and everything else stays on the real clock. */
function mockImmediateSleep() {
  const BACKOFFS = new Set([1000, 2000, 4000, 8000])
  const real = globalThis.setTimeout
  const spy = vi.spyOn(globalThis, 'setTimeout')
  spy.mockImplementation(((fn: (...a: unknown[]) => void, delay?: number, ...args: unknown[]) => {
    if (typeof delay === 'number' && BACKOFFS.has(delay)) {
      void Promise.resolve().then(() => fn(...args))
      return 0 as unknown as ReturnType<typeof setTimeout>
    }
    return (real as unknown as (...a: unknown[]) => unknown).call(globalThis, fn, delay, ...args)
  }) as typeof setTimeout)
  return spy
}

let home: string

async function writeAgentConfig(email: string, extra: Record<string, unknown> = {}): Promise<void> {
  const cfg = {
    email,
    gateway_url: FAKE_GATEWAY,
    domain: 'token.tm',
    system_id: SYSTEM_ID,
    api_key: 'deadbeef',
    session_id: 'session-1',
    ...extra,
  }
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(email))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(path.join(dir, 'agentmail.json'), JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
}

function sign(body: string | Buffer, secret: string): string {
  return createHmac('sha256', secret).update(body).digest('hex')
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-test-'))
  process.env.AIMAIL_HOME = home
})

beforeEach(async () => {
  // fresh sandbox per test (wipe + rebind the standard agent)
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  await writeAgentConfig(AGENT_EMAIL)
})

afterAll(async () => {
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

// ── HMAC (X-Webhook-Signature) ─────────────────────────────────

describe('verifySignature', () => {
  const body = JSON.stringify({ subject: 'hi', to: [AGENT_EMAIL] })

  it('accepts a valid sha256 hmac of the raw body', () => {
    expect(verifySignature(Buffer.from(body), sign(Buffer.from(body), 's3cret'), 's3cret')).toBe(true)
  })

  it('accepts a valid signature computed from a string body', () => {
    expect(verifySignature(body, sign(body, 's3cret'), 's3cret')).toBe(true)
  })

  it('rejects a signature from a different secret', () => {
    expect(verifySignature(Buffer.from(body), sign(Buffer.from(body), 'other'), 's3cret')).toBe(false)
  })

  it('rejects a signature computed over a tampered body', () => {
    const sig = sign(Buffer.from(body), 's3cret')
    const tampered = JSON.stringify({ subject: 'hijack', to: [AGENT_EMAIL] })
    expect(verifySignature(Buffer.from(tampered), sig, 's3cret')).toBe(false)
  })

  it('rejects empty signature or secret', () => {
    expect(verifySignature(Buffer.from(body), '', 's3cret')).toBe(false)
    expect(verifySignature(Buffer.from(body), sign(Buffer.from(body), 's3cret'), '')).toBe(false)
  })

  it('rejects wrong-length signatures without comparing content', () => {
    expect(verifySignature(Buffer.from(body), 'ab', 's3cret')).toBe(false)
  })
})

// ── address contract ───────────────────────────────────────────

describe('cleanAddr', () => {
  it('maps every non [\\w.-] character to underscore (Python parity)', () => {
    expect(cleanAddr('agent1@token.tm')).toBe('agent1_token.tm')
    expect(cleanAddr('a.b-c_d@x.y')).toBe('a.b-c_d_x.y')
    expect(cleanAddr('has space@x.y')).toBe('has_space_x.y')
  })
  it('keeps only ASCII word chars (email addresses are 7-bit ASCII, RFC 5321)', () => {
    // 1:1 with Python _clean_agent_dir_name (re.ASCII) — non-ASCII chars,
    // which can't appear in a valid SMTP address, are defensively mapped to
    // _ so the leaf dir stays ASCII on both sides.
    expect(cleanAddr('münchen.addr@token.tm')).toBe('m_nchen.addr_token.tm')
    expect(cleanAddr('中文.用户@x.y')).toBe('__.___x.y')
  })
})

describe('parseAmailPersona / baseEmail', () => {
  it('short form sys_name@domain → default profile of that system', () => {
    expect(parseAmailPersona('alice@token.tm', 'alice')).toEqual({
      persona: '',
      profile: 'default',
      sysName: 'alice',
    })
  })

  it('three-part persona.profile.sys_name@domain', () => {
    expect(parseAmailPersona('support.alice.sys@token.tm', 'sys')).toEqual({
      persona: 'support',
      profile: 'alice',
      sysName: 'sys',
    })
  })

  it('traditional two-part persona.profile@domain', () => {
    expect(parseAmailPersona('support.alice@token.tm', '')).toEqual({
      persona: 'support',
      profile: 'alice',
      sysName: '',
    })
  })

  it('baseEmail strips the persona prefix (shared domain keeps system segment)', () => {
    expect(baseEmail('support.alice@token.tm', '')).toBe('alice@token.tm')
    expect(baseEmail('support.alice.sys@token.tm', 'sys')).toBe('alice.sys@token.tm')
    expect(baseEmail('alice@token.tm', '')).toBe('alice@token.tm')
  })
})

// ── inbound chain ──────────────────────────────────────────────

const CTX = { systemId: SYSTEM_ID, email: AGENT_EMAIL }

function mail(over: Partial<InboundPayload>): InboundPayload {
  return {
    mail_id: 'm-1',
    message_id: '<mid-1@token.tm>',
    subject: 'hello agent',
    body: 'please review',
    to: [AGENT_EMAIL],
    from: 'boss@corp.com',
    ...over,
  }
}

describe('processInboundMail', () => {
  it('enriches a direct message: my_amail_addr, direct_message, stripped fields', async () => {
    const r = await processInboundMail(mail({}), {}, CTX)
    expect(r).not.toBeNull()
    if (!r) return
    expect(r.my_amail_addr).toBe(AGENT_EMAIL)
    expect(r.direct_message).toBe(true)
    expect(r.mentioned).toBe(false)
    expect(r.message_id).toBe('<mid-1@token.tm>')
    // step 12: backend-only fields stripped
    expect(r).not.toHaveProperty('mail_id')
    expect(r).not.toHaveProperty('to')
    expect(r).not.toHaveProperty('cc')
    expect(r).not.toHaveProperty('headers')
    expect(r).not.toHaveProperty('created_at')
    // recipients built from bare list (no display-name headers given)
    expect((r.recipients as { to: string[] }).to).toEqual([AGENT_EMAIL])
  })

  it('builds display-name recipients/sender from headers', async () => {
    const r = await processInboundMail(
      mail({ headers: { to: 'Agent One <agent1@token.tm>', from: 'Boss <boss@corp.com>' } }),
      {},
      CTX,
    )
    expect(r).not.toBeNull()
    if (!r) return
    expect((r.recipients as { to: string[] }).to).toEqual(['Agent One <agent1@token.tm>'])
    expect(r.sender).toBe('Boss <boss@corp.com>')
  })

  it('direct_message=false when another recipient shares the mail', async () => {
    const r = await processInboundMail(mail({ to: ['other@corp.com', AGENT_EMAIL] }), {}, CTX)
    expect(r).not.toBeNull()
    expect(r?.direct_message).toBe(false)
  })

  it('detects mentions of the agent local part in the body', async () => {
    const r = await processInboundMail(mail({ body: 'hi @agent1, check this' }), {}, CTX)
    expect(r?.mentioned).toBe(true)
  })

  it('normalizes persona addresses to the base address (PERSONA_SUPPORTED=false)', async () => {
    const r = await processInboundMail(mail({ to: [`support.${AGENT_EMAIL}`] }), {}, CTX)
    expect(r).not.toBeNull()
    if (!r) return
    expect(r.my_amail_addr).toBe(AGENT_EMAIL)
    // persona-addressed mail still resolves as a direct message on the base
    expect(r.direct_message).toBe(true)
  })

  it('returns _preprocess_error when the agent has no config', async () => {
    const r = await processInboundMail(mail({}), {}, { systemId: SYSTEM_ID, email: 'ghost@token.tm' })
    expect(r).not.toBeNull()
    expect(r?._preprocess_error).toBe('agentmail email not configured')
    expect(r?.my_amail_addr).toBe('')
  })

  it('flags [WHOAMI] subjects for whoami prompt (early-return)', async () => {
    const r = await processInboundMail(mail({ subject: '[WHOAMI] who are you' }), {}, CTX)
    // _whoami_update_public was removed (dead field) — only the prompt flag remains
    expect(r?._whoami_update_public).toBeUndefined()
    expect(r?._a2a_session_key).toBeUndefined()
  })

  it('adds _a2a_session_key when board_id + board_role present', async () => {
    const r = await processInboundMail(mail({ board_id: 'B-42', board_role: 'worker' }), {}, CTX)
    expect(r?._a2a_session_key).toBe(`a2a:B-42:${mail({}).from}`)
  })

  it('intercepts ping: returns null, sends pong (best-effort), logs both stages', async () => {
    // pong goes through sendMail which now retries transient failures
    // (status 0 = ECONNREFUSED on the discard gateway) — collapse the
    // backoff sleeps so the test does not wait 15s.
    const sleepSpy = mockImmediateSleep()
    try {
      const r = await processInboundMail(mail({ subject: `${PING_PREFIX}ping-123` }), {}, CTX)
      expect(r).toBeNull()
      // three-stage contract: ping_intercepted + pong_sent must be logged
      const logFile = path.join(home, 'logs', `agentmail.${cleanAddr(AGENT_EMAIL)}.log`)
      const lines = (await fs.readFile(logFile, 'utf-8')).trim().split('\n')
      const dirs = lines.map(l => JSON.parse(l).dir)
      expect(dirs).toContain('ping_intercepted')
      expect(dirs).toContain('pong_sent')
    } finally {
      sleepSpy.mockRestore()
    }
  })

  it('intercepts pong: returns null and logs pong_returned', async () => {
    const r = await processInboundMail(mail({ subject: `${PONG_PREFIX}ping-123` }), {}, CTX)
    expect(r).toBeNull()
    const logFile = path.join(home, 'logs', `agentmail.${cleanAddr(AGENT_EMAIL)}.log`)
    const lines = (await fs.readFile(logFile, 'utf-8')).trim().split('\n')
    const dirs = lines.map(l => JSON.parse(l).dir)
    expect(dirs).toContain('pong_returned')
  })

  it('logs inbound for non-intercepted mail', async () => {
    await processInboundMail(mail({}), {}, CTX)
    const logFile = path.join(home, 'logs', `agentmail.${cleanAddr(AGENT_EMAIL)}.log`)
    const lines = (await fs.readFile(logFile, 'utf-8')).trim().split('\n')
    expect(lines.some(l => JSON.parse(l).event === 'inbound')).toBe(true)
  })

  // ── B1: batch profile injection ──────────────────────────────

  it('B1: one batch GET (sender first) → my_profile/sender_profile/recipients_profile', async () => {
    const mock = vi.fn(async (url: string) => {
      const u = String(url)
      if (u.includes('/api/v1/contacts?')) {
        return new Response(JSON.stringify({
          my_profile: { address: AGENT_EMAIL, profile: 'Agent One — the agent persona' },
          sender_profile: { 'boss@corp.com': 'Boss — manager' },
          recipients_profile: { 'peer@corp.com': 'Peer — teammate' },
          results: [],
        }), { status: 200 })
      }
      return new Response('{}', { status: 200 })
    })
    vi.stubGlobal('fetch', mock)
    const r = await processInboundMail(mail({ to: [AGENT_EMAIL, 'peer@corp.com'] }), {}, CTX)
    vi.unstubAllGlobals()
    expect(r?.my_profile).toBe('Agent One — the agent persona')
    expect(r?.sender_profile).toEqual({ 'boss@corp.com': 'Boss — manager' })
    expect(r?.recipients_profile).toEqual({ 'peer@corp.com': 'Peer — teammate' })
    const call = mock.mock.calls.find(c => String(c[0]).includes('/api/v1/contacts?'))
    expect(String(call?.[0])).toContain('addresses=boss%40corp.com%2Cagent1%40token.tm%2Cpeer%40corp.com')
  })

  it('B1: gateway failure → no profile fields (non-fatal)', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 500 })))
    const r = await processInboundMail(mail({}), {}, CTX)
    vi.unstubAllGlobals()
    expect(r).not.toBeNull()
    expect(r?.my_profile).toBeUndefined()
    expect(r?.sender_profile).toBeUndefined()
    expect(r?.recipients_profile).toBeUndefined()
  })

  // ── B2: thread_summary preload ────────────────────────────────

  it('B2: preloads thread_summary from the local threads/ file (refs[0] = thread root)', async () => {
    const tid = '<root@token.tm>'
    const p = threadPath(AGENT_EMAIL, tid)
    await fs.mkdir(path.dirname(p), { recursive: true })
    await fs.writeFile(p, JSON.stringify({ thread_id: tid, summary: 'Prior context: discussed Q3 roadmap' }), 'utf-8')
    const r = await processInboundMail(mail({ references: [tid] }), {}, CTX)
    expect(r?.thread_summary).toBe('Prior context: discussed Q3 roadmap')
  })

  it('B2: first mail in a thread (no thread file) → no thread_summary', async () => {
    const r = await processInboundMail(mail({}), {}, CTX)
    expect(r?.thread_summary).toBeUndefined()
  })

  // ── B3: Role_Calibrator ───────────────────────────────────────

  it('B3: subject containing "update persona" injects role_calibrator.md (early-return)', async () => {
    const roleDir = path.join(home, 'systems', SYSTEM_ID, 'board', 'role_prompt')
    await fs.mkdir(roleDir, { recursive: true })
    await fs.writeFile(
      path.join(roleDir, 'role_calibrator.md'),
      'Draft a persona for {{AGENTMAIL_ADDRESS}}; inquiry: {{INQUIRY_SUBJECT}}',
      'utf-8',
    )
    const r = await processInboundMail(
      mail({ subject: 'Please update persona for support', board_id: 'B-9', board_role: 'worker' }),
      {},
      CTX,
    )
    expect(r?._role_prompt).toBe(`Draft a persona for ${AGENT_EMAIL}; inquiry: Please update persona for support`)
    // early-return: the board session key must NOT be set
    expect(r?._a2a_session_key).toBeUndefined()
  })

  it('B3: missing role file → no _role_prompt (persona update proceeds without one)', async () => {
    const r = await processInboundMail(mail({ subject: 'update persona please' }), {}, CTX)
    expect(r?._role_prompt).toBeUndefined()
    expect(r?._a2a_session_key).toBeUndefined()
  })

  it('role lookup is case-insensitive (readRoleFile lowercases the name)', async () => {
    const roleDir = path.join(home, 'systems', SYSTEM_ID, 'board', 'role_prompt')
    await fs.mkdir(roleDir, { recursive: true })
    await fs.writeFile(path.join(roleDir, 'worker.md'), 'worker role for {{BOARD_ID}}', 'utf-8')
    const r = await processInboundMail(mail({ board_id: 'B-7', board_role: 'Worker' }), {}, CTX)
    expect(r?._role_prompt).toBe('worker role for B-7')
  })
})
