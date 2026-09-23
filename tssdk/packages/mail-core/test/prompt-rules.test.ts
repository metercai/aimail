/**
 * prompt_rules 契约(TS 侧, 与 pysdk tests/test_prompt_rules.py 同型)。
 *
 * 裁决 2026-09-23:
 *   ① 字段=subject/body/sender/recipient 包含匹配(大小写不敏感);
 *      字段内多关键字=或, 字段间=且
 *   ② name={10-99 序号}_{filename}; 实际加载看 rule.file
 *   ③ 链序固定: [WHOAMI] > welcome > board > X-AIMail-Prompt header >
 *      本地规则(name 字母序, 首中即止); 命中但文件缺失(含 common 兜底都无)
 *      ⇒ WARN 续走; 任何一层不阻断送达
 *   ④ 键 prompt_rules(存 agentmail.json, 仅该 agent)
 *   ⑦ header 是网关扩展点: 双源 payload.headers / HTTP 头参数, 大小写不敏感
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { processInboundMail, readPromptRules, promptRuleMatches } from '../src/preprocess.js'
import { cleanAddr } from '../src/config.js'
import type { AgentConfig, InboundPayload } from '../src/types.js'

const SYSTEM_ID = 'system-test'
const AGENT_EMAIL = 'agent1@token.tm'
const FAKE_GATEWAY = 'http://127.0.0.1:9'
const CTX = { systemId: SYSTEM_ID, email: AGENT_EMAIL }

let home: string

async function writeAgentConfig(extra: Record<string, unknown> = {}): Promise<void> {
  const cfg = {
    email: AGENT_EMAIL,
    gateway_url: FAKE_GATEWAY,
    domain: 'token.tm',
    system_id: SYSTEM_ID,
    api_key: 'deadbeef',
    ...extra,
  }
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(AGENT_EMAIL))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(path.join(dir, 'agentmail.json'), JSON.stringify(cfg, null, 2) + '\n', { mode: 0o600 })
}

function mail(over: Partial<InboundPayload>): InboundPayload {
  return {
    mail_id: 'm-1',
    message_id: '<mid-1@token.tm>',
    subject: 'quarterly review',
    body: 'please review the numbers',
    to: [AGENT_EMAIL],
    from: 'boss@corp.com',
    ...over,
  }
}

async function roleDir(): Promise<string> {
  const d = path.join(home, 'systems', SYSTEM_ID, 'board', 'role_prompt')
  await fs.mkdir(d, { recursive: true })
  return d
}

function cfgOf(prompt_rules?: unknown): AgentConfig {
  return {
    email: AGENT_EMAIL,
    gateway_url: FAKE_GATEWAY,
    domain: 'token.tm',
    system_id: SYSTEM_ID,
    api_key: 'deadbeef',
    ...(prompt_rules !== undefined ? { prompt_rules } : {}),
  } as AgentConfig
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-pr-'))
  process.env.AIMAIL_HOME = home
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.AIMAIL_HOME = home
  await writeAgentConfig()
})

afterAll(async () => {
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('promptRuleMatches (① field-internal OR / cross-field AND)', () => {
  const rule = { name: '10_a', file: 'a', subject: ['[incident]', '告警'], sender: ['ops@x.com'] }
  it('OR inside a field, case-insensitive, AND across fields', () => {
    expect(promptRuleMatches(rule, '[INCIDENT] cluster down', '', 'ops@x.com', '')).toBe(true)
    expect(promptRuleMatches(rule, '集群有告警', '', 'OPS@X.COM', '')).toBe(true)
    expect(promptRuleMatches(rule, 'plain mail', '', 'ops@x.com', '')).toBe(false)
    expect(promptRuleMatches(rule, '[incident]', '', 'boss@corp.com', '')).toBe(false)
  })
  it('absent field skips; empty list = not given; wrong type = no match', () => {
    expect(promptRuleMatches({ name: '10_a', file: 'a', sender: ['boss@'] } as never, 'anything', '', 'boss@corp.com', '')).toBe(true)
    expect(promptRuleMatches({ name: '10_a', file: 'a', subject: [], sender: ['boss@'] } as never, 'anything', '', 'boss@corp.com', '')).toBe(true)
    expect(promptRuleMatches({ name: '10_a', file: 'a', subject: { k: 1 } } as never, 'x', '', '', '')).toBe(false)
  })
})

describe('readPromptRules (②③ loader filter + name order)', () => {
  it('drops bad items and sorts by name (serial order)', () => {
    const out = readPromptRules(cfgOf([
      { name: '12_incident', file: 'incident', subject: ['x'] },
      { name: '10_audit', file: 'audit', body: ['a'] },
      { name: '09_bad_serial', file: 'bad', subject: ['x'] },
      { name: '13_nofile', subject: ['x'] },
      { name: '14_off', file: 'off', subject: ['x'], enabled: false },
      { name: '15_nofield', file: 'nf' },
      { name: '16_badtype', file: 'bt', subject: 123 },
      'not-an-object',
    ]))
    expect(out.map((r) => r.name)).toEqual(['10_audit', '12_incident'])
  })
  it('missing key / no config → [] (never throws)', () => {
    expect(readPromptRules(cfgOf())).toEqual([])
    expect(readPromptRules(cfgOf(null))).toEqual([])
    expect(readPromptRules(cfgOf('nope'))).toEqual([])
  })
})

describe('selection chain (③⑦ header layer + board + first hit)', () => {
  it('L4 header (payload.headers) wins over local rules', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'hdr_role.md'), 'HDR {{INQUIRY_SUBJECT}}', 'utf-8')
    await fs.writeFile(path.join(d, 'local_role.md'), 'LOCAL', 'utf-8')
    await writeAgentConfig({ prompt_rules: [{ name: '10_local', file: 'local_role', subject: ['quarterly'] }] })
    const r = await processInboundMail(
      mail({ headers: { 'X-AIMail-Prompt': 'hdr_role' } }), {}, CTX)
    expect(r?._role_prompt).toBe('HDR quarterly review')
  })

  it('L4 header also read from HTTP headers param (双源)', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'hdr_role.md'), 'HDR-VIA-HTTP', 'utf-8')
    const r = await processInboundMail(mail({}), { 'x-aimail-prompt': 'hdr_role' }, CTX)
    expect(r?._role_prompt).toBe('HDR-VIA-HTTP')
  })

  it('L4 header names a missing file → warns and falls through to L5', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'local_role.md'), 'LOCAL', 'utf-8')
    await writeAgentConfig({ prompt_rules: [{ name: '10_local', file: 'local_role', subject: ['quarterly'] }] })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const r = await processInboundMail(
      mail({ headers: { 'X-AIMail-Prompt': 'ghost' } }), {}, CTX)
    expect(r?._role_prompt).toBe('LOCAL')
    expect(warn.mock.calls.some((c) => String(c[0]).includes('X-AIMail-Prompt'))).toBe(true)
    warn.mockRestore()
  })

  it('board role (L3) blocks L4/L5', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'worker.md'), 'WORKER {{BOARD_ID}}', 'utf-8')
    await fs.writeFile(path.join(d, 'local_role.md'), 'LOCAL', 'utf-8')
    await writeAgentConfig({ prompt_rules: [{ name: '10_local', file: 'local_role', subject: ['quarterly'] }] })
    const r = await processInboundMail(
      mail({ board_id: 'B-7', board_role: 'worker', headers: { 'X-AIMail-Prompt': 'ghost' } }), {}, CTX)
    expect(r?._role_prompt).toBe('WORKER B-7')
    expect(r?._a2a_session_key).toBe('a2a:B-7:boss@corp.com')
  })

  it('L5 name order first hit (10 before 12)', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'audit.md'), 'AUDIT', 'utf-8')
    await fs.writeFile(path.join(d, 'incident.md'), 'INCIDENT', 'utf-8')
    await writeAgentConfig({ prompt_rules: [
      { name: '12_incident', file: 'incident', subject: ['quarterly'] },
      { name: '10_audit', file: 'audit', subject: ['quarterly'] },
    ] })
    const r = await processInboundMail(mail({}), {}, CTX)
    expect(r?._role_prompt).toBe('AUDIT')
  })

  it('matched rule with missing file → warn + next rule (③)', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'real.md'), 'REAL', 'utf-8') // ghost 三级都无(无 common)
    await writeAgentConfig({ prompt_rules: [
      { name: '10_ghost', file: 'ghost', subject: ['quarterly'] },
      { name: '12_real', file: 'real', subject: ['quarterly'] },
    ] })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    const r = await processInboundMail(mail({}), {}, CTX)
    expect(r?._role_prompt).toBe('REAL')
    expect(warn.mock.calls.some((c) => String(c[0]).includes('10_ghost') && String(c[0]).includes('missing'))).toBe(true)
    warn.mockRestore()
  })

  it('built-ins preempt custom rules (WHOAMI early-return)', async () => {
    const d = await roleDir()
    await fs.writeFile(path.join(d, 'whoami.md'), 'WHOAMI', 'utf-8')
    await writeAgentConfig({ prompt_rules: [{ name: '10_local', file: 'local_role', subject: ['who'] }] })
    const r = await processInboundMail(mail({ subject: '[WHOAMI] who?' }), {}, CTX)
    expect(r?._whoami_prompt).toBe('WHOAMI')
    expect(r?._role_prompt).toBeUndefined()
  })
})
