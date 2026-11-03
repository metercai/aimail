/**
 * L3 2026-11-02 (dsh-aimail grids) — deterministic regression for the
 * "agent cannot read delivered inbound mail" defect, plus defects E/F.
 *
 * Root cause (proven, not the probabilistic L3 symptom): the inbound snapshot
 * + FTS-index write (saveInboundSnapshot, mirror of Python
 * store_inbound_message) was stranded in core/agent-level.ts (scenario-1
 * chain only). The dsh/openclaw/pi shared chain (processInboundMail) wrote
 * only local meta, so the FTS index never existed for a delivered inbound
 * mail → search_mail count=0 + "no local mail index yet". One call site
 * moved into the shared pipeline (preprocess step 14c); this file pins it.
 *
 * QA criteria covered (see .hermes/qa/2026-11-02-handoff-dsh-inbound-index.md):
 *   ① push a mail → COUNT(*)>=1 in the index AND search_mail(inbound) hits
 *      the mid with non-empty body (via the snapshot file + FTS)
 *   ③ email_summary on a delivered mid → found=true (distinguishes "no
 *      summary yet" from "id not here")
 *   ⑤/⑥ message_id normalization: HTML-escaped ids are accepted at the tool
 *      boundary (send_mail threading + email_summary lookup), platform-
 *      independent
 *   ⑦ search_mail limit is a scalar integer (dsh schema exposes integer, not
 *      array) and the handler accepts a scalar
 *   (gate) save_raw_snapshots=false ⇒ meta written, snapshot+index skipped
 *
 * FS sandboxed via AIMAIL_HOME → tmp (preprocess.test.ts / search.test.ts).
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { processInboundMail } from '../../src/core/preprocess.js'
import { agentMailDir, saveLocalMeta } from '../../src/core/meta.js'
import { searchLocalMail, searchIndexPath } from '../../src/core/search.js'
import { searchMail, emailSummary, normalizeMessageId, sendMail } from '../../src/core/tools.js'
import { BINDING_FILE } from '../../src/core/contract.js'
import { toTypeBoxParams } from '../../src/core/typebox-params.js'
import { MAIL_TOOLS } from '../../src/core/tool-registry.js'
import { cleanAddr } from '../../src/core/config.js'
import type { InboundPayload } from '../../src/core/types.js'

const SYSTEM_ID = 'system-test'
const AGENT_EMAIL = 'agent1@token.tm'
const FAKE_GATEWAY = 'http://127.0.0.1:9' // discard port → ECONNREFUSED
const CTX = { systemId: SYSTEM_ID, email: AGENT_EMAIL }
const MID = '<mid-dsh-1@l3.local>'
const ESC_MID = '&lt;mid-dsh-1@l3.local&gt;' // what some model routes copy

let home: string

async function writeAgentConfig(email: string, extra: Record<string, unknown> = {}): Promise<void> {
  const cfg = {
    email, gateway_url: FAKE_GATEWAY, domain: 'token.tm',
    system_id: SYSTEM_ID, api_key: 'deadbeef', session_id: 'session-1', ...extra,
  }
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(email))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(path.join(dir, BINDING_FILE), JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
}

function inboundMail(): InboundPayload {
  return {
    mail_id: 'm-1', message_id: MID, subject: 'quarterly report',
    body: 'please review the quarterly numbers', to: [AGENT_EMAIL], from: 'boss@corp.com',
  }
}

async function indexRowCount(email: string): Promise<number> {
  const p = searchIndexPath(email)
  let exists = false
  try { await fs.access(p); exists = true } catch { /* missing */ }
  if (!exists) return -1 // -1 = db file never created
  const mod: any = await import('node:sqlite')
  const db = new mod.DatabaseSync(p, { readOnly: true })
  try {
    const n = (db.prepare('SELECT COUNT(*) AS n FROM emails').get() as { n: number }).n
    return n
  } finally { db.close() }
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-inbound-index-'))  // contract-allowed: temp dir prefix, not the hermes route registration name
  process.env.AIMAIL_HOME = home
})
beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  await writeAgentConfig(AGENT_EMAIL)
})
afterAll(async () => {
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('defect 1: delivered inbound mail is searchable (the shared chain)', () => {
  it('processInboundMail writes the FTS row + snapshot (QA ①)', async () => {
    const enriched = await processInboundMail(inboundMail(), {}, CTX)
    expect(enriched).not.toBeNull()
    const mid = String((enriched as Record<string, unknown>)['message_id'] ?? '')
    expect(mid).toBe(MID)

    // the index DB now exists and has the row (pre-fix: file absent → -1)
    const count = await indexRowCount(AGENT_EMAIL)
    expect(count).toBeGreaterThanOrEqual(1)

    // search_mail(scope=inbound) hits the exact mid
    const byScope = await searchLocalMail(AGENT_EMAIL, { scope: 'inbound' })
    expect(byScope.success).toBe(true)
    expect(byScope.count).toBeGreaterThanOrEqual(1)
    expect(byScope.results!.some((h) => h.mid === MID)).toBe(true)

    // body is indexed (FTS keyword hit on a body word)
    const byWord = await searchLocalMail(AGENT_EMAIL, { query: 'quarterly' })
    expect(byWord.results!.some((h) => h.mid === MID)).toBe(true)

    // agent-visible snapshot file is on disk (search_mail's fallback scans it)
    const snap = path.join(agentMailDir(AGENT_EMAIL),
      new Date().toISOString().slice(0, 7).replace('-', ''), 'in-mid-dsh-1_l3.local.json')
    let snapExists = false
    try { await fs.access(snap); snapExists = true } catch { /* */ }
    expect(snapExists, 'inbound snapshot file must be written').toBe(true)
  })

  it('save_raw_snapshots=false ⇒ meta written, snapshot+index skipped (gate parity)', async () => {
    await fs.rm(home, { recursive: true, force: true })
    await fs.mkdir(home, { recursive: true })
    await writeAgentConfig(AGENT_EMAIL, { save_raw_snapshots: false })
    await processInboundMail(inboundMail(), {}, CTX)
    // reply-chain meta still written
    const metaDir = path.join(agentMailDir(AGENT_EMAIL), 'meta')
    const metaFiles = ((await fs.readdir(metaDir, { recursive: true }).catch(() => [])) as unknown as string[])
      .filter((f) => f.endsWith('.json'))
    expect(metaFiles.length).toBe(1)
    // but no snapshot index row
    expect(await indexRowCount(AGENT_EMAIL)).toBe(-1)
  })
})

describe('defect E: message_id normalization at the tool boundary (QA ⑤⑥)', () => {
  it('normalizeMessageId unescapes HTML entities; raw ids pass through', () => {
    expect(normalizeMessageId(ESC_MID)).toBe(MID)
    expect(normalizeMessageId(MID)).toBe(MID)
    expect(normalizeMessageId('  <a@b.c>  ')).toBe('<a@b.c>')
    expect(normalizeMessageId('')).toBe('')
  })

  it('email_summary accepts an HTML-escaped id for a delivered mail (QA ③⑤)', async () => {
    await processInboundMail(inboundMail(), {}, CTX)
    const got = await emailSummary(CTX, { message_id: ESC_MID })
    expect(got.success).toBe(true)
    expect(got.found).toBe(true) // meta hit ⇒ the id is in the local mailbox
    expect(got.thread_id).toBe(MID) // no references ⇒ thread_id = the mid itself
  })

  it('email_summary distinguishes "no summary yet" from "id not here" (QA ③)', async () => {
    await processInboundMail(inboundMail(), {}, CTX)
    const delivered = await emailSummary(CTX, { message_id: MID })
    expect(delivered.found).toBe(true)
    const orphan = await emailSummary(CTX, { message_id: '<orphan@token.tm>' })
    expect(orphan.found).toBe(false)
  })

  it('send_mail threads through an HTML-escaped message_id WITHOUT writing back into args (QA ⑤⑥ + 2026-11-03 frozen-args regression)', async () => {
    // The send fails (no gateway), but the outbound meta — written BEFORE the
    // network call — records the threaded references. With the escaped input,
    // pre-fix the references stored the escaped form (threading broken);
    // post-fix they carry the raw mid.
    //
    // 2026-11-03 regression (QA, run 41553978770): dsh passes args as a
    // FROZEN object; the original fix wrote back into args
    // (`args.message_id = normalizeMessageId(...)`) → TypeError on every
    // send_mail call. args is frozen here so this test fails if any tool
    // ever mutates its input again (criterion ①); the refs assertions pin
    // the normalization (criterion ②). GatewayClient.sendMail is stubbed so
    // the failed send returns instantly (status 400 = terminal, no retry
    // backoff — status 0 is retryable and would sleep ~15s).
    const gw = await import('../../src/core/gateway.js')
    vi.spyOn(gw.GatewayClient.prototype, 'sendMail').mockResolvedValue({ status: 400, error: 'stub' } as never)
    await processInboundMail(inboundMail(), {}, CTX)
    const frozenArgs = Object.freeze({ to: ['x@y.z'], subject: 're', body: 'b', message_id: ESC_MID })
    expect(Object.isFrozen(frozenArgs)).toBe(true)
    const r = await sendMail(CTX, frozenArgs as never) // must NOT throw
    expect(r.success).toBe(false) // stubbed terminal failure — threading already done
    const metaDir = path.join(agentMailDir(AGENT_EMAIL), 'meta')
    const files = ((await fs.readdir(metaDir, { recursive: true }).catch(() => [])) as unknown as string[])
      .filter((f) => f.endsWith('.json'))
    // 1 inbound meta + 1 outbound meta (the reply's own generated mid)
    expect(files).toHaveLength(2)
    const metas = await Promise.all(files.map(async (f) => JSON.parse(await fs.readFile(path.join(metaDir, f), 'utf-8'))))
    const outbound = metas.find((m) => m.direction === 'outbound')
    expect(outbound, 'outbound meta must be written before the network call').toBeTruthy()
    // normalizeRefs stores it as an array; the raw mid must appear (not the escaped form)
    const refs = Array.isArray(outbound!.references) ? outbound!.references : [outbound!.references]
    expect(refs).toContain(MID)
    expect(JSON.stringify(outbound!.references)).not.toContain('&lt;')
    // the caller's args object is untouched (read-only contract)
    expect(frozenArgs.message_id).toBe(ESC_MID)
    vi.restoreAllMocks()
  })
})

describe('defect F: search_mail.limit is a scalar integer', () => {
  it('dsh adapter exposes limit as integer (not array)', async () => {
    const specs: Record<string, unknown> = {}
    vi.doMock('@deepseek-ai/dsh-tools', () => ({
      defineTool: (spec: { name: string; parameters?: Record<string, unknown> }) => {
        specs[spec.name] = spec.parameters ?? {}
        return { name: spec.name }
      },
    }))
    const { apply } = await import('../../src/dsh/tools.js')
    const ctx = {
      get: (key: string) => (key === 'mail' ? { resolveCtx: async () => ({}) } : undefined),
      tools: { register: () => {} },
    }
    apply(ctx as never)
    const searchMailSpec = specs['search_mail'] as { limit?: { type: string } } | undefined
    expect(searchMailSpec?.limit?.type).toBe('integer')
    expect(searchMailSpec?.limit?.type).not.toBe('array')
    vi.doUnmock('@deepseek-ai/dsh-tools')
  })

  it('TypeBox adapter exposes limit as integer (openclaw/pi)', () => {
    const tool = MAIL_TOOLS.find((t) => t.name === 'search_mail')!
    const schema = toTypeBoxParams(tool.parameters) as { properties: Record<string, { anyOf?: { type: string }[]; type?: string }> }
    const limit = schema.properties['limit']
    const types = (limit?.anyOf ?? [limit]).map((x) => x?.type)
    expect(types).toContain('integer')
    expect(types).not.toContain('string')
  })

  it('search_mail handler accepts a scalar limit (QA ⑦)', async () => {
    const r = await searchMail(CTX, { query: 'quarterly', limit: 5 } as never)
    expect(r.success).toBe(true)
    expect(typeof r.count).toBe('number')
  })
})
