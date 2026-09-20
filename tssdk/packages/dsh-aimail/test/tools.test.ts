// dsh-aimail 回归守卫（审计 2026-09-21 补齐：本包此前零测试）。
//
// 用最小 stub 的 cordis ctx 驱动 apply()，断言注册的工具集 == MAIL_TOOLS 全集
// (适配器只做"翻译 schema + 绑定身份"，不得漏注册或多注册)。
//
// 注: 宿主包 @deepseek-ai/dsh-tools 会传递依赖未安装的 @deepseek-ai/dsh-scope，
// 工作区里无法真实加载 ⇒ 用 vi.mock 隔离(只替换 defineTool 一个运行时符号，
// 其余导入均为 type-only，不影响被测逻辑)。
import { describe, it, expect, vi } from 'vitest'

vi.mock('@deepseek-ai/dsh-tools', () => ({
  defineTool: (spec: unknown) => spec,
}))

import { MAIL_TOOLS } from '@aimail/mail-core'
import { apply } from '../src/tools.ts'

describe('dsh-aimail tool registration', () => {
  it('apply() registers every MAIL_TOOLS entry exactly once', () => {
    const registered: string[] = []
    const ctx = {
      get: (key: string) =>
        key === 'mail'
          ? { resolveCtx: async () => ({}) }        // MailService stub
          : undefined,                              // agentDefaultModel 缺失 → 走默认分支
      tools: { register: (tool: { name: string }) => registered.push(tool.name) },
    }
    apply(ctx as never)
    expect(registered.length).toBe(MAIL_TOOLS.length)
    expect(registered.length).toBe(15)
    expect([...registered].sort()).toEqual(MAIL_TOOLS.map(t => t.name).sort())
  })

  it('apply() fails loudly when the mail service is not mounted', () => {
    const ctx = { get: () => undefined, tools: { register: () => {} } }
    expect(() => apply(ctx as never)).toThrow(/mail service/)
  })
})
