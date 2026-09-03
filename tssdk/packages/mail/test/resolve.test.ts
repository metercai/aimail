/**
 * @aimail/mail resolver contract tests (sandboxed via AIMAIL_HOME → tmp dir).
 *   - resolveBySessionId / resolveByEmail / resolveCtx (existing semantics)
 *   - resolveByRecipient: exact match + persona-strip fallback
 *     (mirror of Python route_agent_for_email)
 */
import { beforeAll, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const TMP = await fs.mkdtemp(path.join(os.tmpdir(), 'amail-mail-test-'))
process.env.AIMAIL_HOME = TMP
// import AFTER env is set (AIMAIL_HOME() is a lazy getter)
const { resolveBySessionId, resolveByEmail, resolveByRecipient, resolveCtx } =
  await import('../src/index.js')

const SYSTEM = 'system-test'
const AGENT = 'alice@token.tm'

beforeAll(async () => {
  const agentDir = path.join(TMP, 'systems', SYSTEM, 'alice_token.tm')
  await fs.mkdir(agentDir, { recursive: true })
  await fs.writeFile(
    path.join(agentDir, 'agentmail.json'),
    JSON.stringify({
      system_id: SYSTEM,
      agent_id: 'main',
      email: AGENT,
      session_id: 'sess-123',
      gateway_url: 'http://127.0.0.1:9',
      api_key: 'k',
      domain: 'token.tm',
    }),
  )
})

afterAll(async () => {
  await fs.rm(TMP, { recursive: true, force: true })
})

describe('resolvers (existing semantics)', () => {
  it('resolveBySessionId finds by session_id', async () => {
    const cfg = await resolveBySessionId('sess-123')
    expect(cfg.email).toBe(AGENT)
  })

  it('resolveBySessionId throws when unbound', async () => {
    await expect(resolveBySessionId('nope')).rejects.toThrow(/no aimail binding/)
  })

  it('resolveByEmail finds by registered address', async () => {
    const cfg = await resolveByEmail(AGENT)
    expect(cfg.system_id).toBe(SYSTEM)
  })

  it('resolveCtx returns {systemId, email}', async () => {
    const ctx = await resolveCtx('sess-123')
    expect(ctx).toEqual({ systemId: SYSTEM, email: AGENT })
  })
})

describe('resolveByRecipient (inbound routing)', () => {
  it('exact registered address matches', async () => {
    const cfg = await resolveByRecipient(AGENT)
    expect(cfg?.email).toBe(AGENT)
  })

  it('persona alias routes to the owning agent (strip fallback)', async () => {
    const cfg = await resolveByRecipient('support.alice@token.tm')
    expect(cfg?.email).toBe(AGENT)
  })

  it('multi-segment persona prefix routes too', async () => {
    const cfg = await resolveByRecipient('team.support.alice@token.tm')
    expect(cfg?.email).toBe(AGENT)
  })

  it('unknown recipient → undefined (no_agent)', async () => {
    expect(await resolveByRecipient('stranger@token.tm')).toBeUndefined()
  })

  it('prefix must be dot-separated (no substring false-positive)', async () => {
    // "notalice" ends with "alice" but not ".alice" — must NOT match
    expect(await resolveByRecipient('notalice@token.tm')).toBeUndefined()
  })
})
