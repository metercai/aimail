/**
 * Minimal contract tests for the inbound chain:
 *   - verifySignature (webhook HMAC)
 *   - cleanAddr / parseAmailPersona / baseEmail (address contract)
 *   - processInboundMail (13-step preprocess + ping/pong intercept + logs)
 *
 * All filesystem access is sandboxed via AIMAIL_HOME → tmp dir.
 * The gateway URL points at 127.0.0.1:9 (discard, immediate ECONNREFUSED)
 * so no real network I/O happens.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
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
import type { InboundPayload } from '../src/types.js'

const SYSTEM_ID = 'system-test'
const AGENT_EMAIL = 'agent1@token.tm'
const FAKE_GATEWAY = 'http://127.0.0.1:9' // discard port → ECONNREFUSED, no real I/O

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
  it('keeps non-ASCII word chars (Python re \\w is Unicode-aware, JS \\w is not)', () => {
    // 1:1 with Python _clean_agent_dir_name — a non-ASCII local part must land
    // in the SAME leaf dir on both sides, otherwise the shared ~/.agentmail/
    // layout splits. Old ASCII-only /[^\\w.-]/ turned ü into _.
    expect(cleanAddr('münchen.addr@token.tm')).toBe('münchen.addr_token.tm')
    expect(cleanAddr('中文.用户@x.y')).toBe('中文.用户_x.y')
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

  it('flags [WHOAMI] subjects for public whoami update', async () => {
    const r = await processInboundMail(mail({ subject: '[WHOAMI] who are you' }), {}, CTX)
    expect(r?._whoami_update_public).toBe(true)
  })

  it('adds _a2a_session_key when board_id + board_role present', async () => {
    const r = await processInboundMail(mail({ board_id: 'B-42', board_role: 'worker' }), {}, CTX)
    expect(r?._a2a_session_key).toBe(`a2a:B-42:${mail({}).from}`)
  })

  it('intercepts ping: returns null, sends pong (best-effort), logs both stages', async () => {
    const r = await processInboundMail(mail({ subject: `${PING_PREFIX}ping-123` }), {}, CTX)
    expect(r).toBeNull()
    // three-stage contract: ping_intercepted + pong_sent must be logged
    const logFile = path.join(home, 'logs', `agentmail.${cleanAddr(AGENT_EMAIL)}.log`)
    const lines = (await fs.readFile(logFile, 'utf-8')).trim().split('\n')
    const dirs = lines.map(l => JSON.parse(l).dir)
    expect(dirs).toContain('ping_intercepted')
    expect(dirs).toContain('pong_sent')
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
})
