// 契约 v1.0 §4.1(2) 回归钉子（owner 2026-10-11 裁定：node 侧一律 node_entry）：
// 独立入口 `dist/register-cli.js` 的 stdout 必须**恰一行 JSON**。
//
// 与 op-stdout-clean.test.ts 的关系：那条钉子保证"若经宿主 register(api) 派发也不得
// console.log"；本条钉子保证独立入口**根本不加载宿主插件**时信封依旧成立，并覆盖
// usage/call 两类错误的 exit 码与形状（与 python 门、dsh/pi 入口同形）。
//
// main() 被导出 ⇒ 无需在测试进程里 process.exit（入口自跑由 argv[1] 尾名守卫）。
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'

import { main } from '../../src/openclaw/register-cli.js'

type Captured = { out: string[]; err: string[] }

function capture(): Captured {
  const c: Captured = { out: [], err: [] }
  vi.spyOn(process.stdout, 'write').mockImplementation((chunk: unknown) => {
    c.out.push(String(chunk))
    return true
  })
  vi.spyOn(process.stderr, 'write').mockImplementation((chunk: unknown) => {
    c.err.push(String(chunk))
    return true
  })
  return c
}

function envelope(c: Captured): { lines: string[]; json: Record<string, unknown> } {
  const lines = c.out.join('').split('\n').filter((l) => l.length > 0)
  expect(lines).toHaveLength(1) // 恰一行 ⇒ 无宿主生命周期诊断混入 stdout
  return { lines, json: JSON.parse(lines[0]!) as Record<string, unknown> }
}

describe('openclaw-aimail register-cli (node_entry, 契约 §4.1(2))', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('缺 --op ⇒ usage 信封 + exit 2', async () => {
    const c = capture()
    const rc = await main([])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['ok']).toBe(false)
    expect(json['kind']).toBe('usage')
    expect(String(json['error'])).toContain('--op')
  })

  it('未知 op ⇒ usage 信封 + exit 2（列出期望集合）', async () => {
    const c = capture()
    const rc = await main(['--op', 'bogus', '--args', '{}'])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['kind']).toBe('usage')
    expect(String(json['error'])).toContain("unknown op 'bogus'")
    expect(String(json['error'])).toContain('prompt-test')
  })

  it('--args 非 JSON 对象 ⇒ usage 信封 + exit 2', async () => {
    const c = capture()
    const rc = await main(['--op', 'assemble', '--args', 'not-json'])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['kind']).toBe('usage')
    expect(String(json['error'])).toContain('not valid JSON')
  })

  it('assemble 缺 system_id ⇒ 门内 UsageError 冒泡为 usage 信封 + exit 2（无网络）', async () => {
    const c = capture()
    const rc = await main(['--op', 'assemble', '--args', '{}'])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['kind']).toBe('usage')
    expect(json['exc']).toBe('UsageError')
    expect(String(json['error'])).toContain('system_id')
    // 诊断允许走 stderr（契约只约束 stdout）
    expect(c.err.join('')).toContain('register-cli: assemble failed')
  })
})
