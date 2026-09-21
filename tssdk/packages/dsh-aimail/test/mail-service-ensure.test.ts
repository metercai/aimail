// dsh-aimail 启动自举守卫回归测试(2026-09-22)
//
// 背景: 宿主插件 apply() 无条件反调 `aimail ensure-system`; 若环境里残留一个
// 已被消费的激活码, 主循环会打印 "no aimail system yet — Invalid activation
// code", 而该机器其实早就绑定好了 —— 启动即误报(2026-09-21 dsh 实测报告)。
//
// 契约: ①本 home 已归属某系统 ⇒ 完全不反调(已有绑定则不自举);
//       ②未归属 ⇒ 反调; ③失败文案可读且可执行。
import { describe, it, expect, vi, beforeEach } from 'vitest'

const detectSystemForHome = vi.fn()
const ensureSystem = vi.fn()

vi.mock('@aimail/mail-core', () => ({
  releaseAllSystems: () => {},
  detectSystemForHome: (...a: unknown[]) => detectSystemForHome(...a),
  ensureSystem: (...a: unknown[]) => ensureSystem(...a),
  hasAnySystem: () => true,
  listSystemDirs: async () => ['sid-1'],
  readSystemConfig: async () => ({}),
  autoBind: async () => ({ registered: false, exists: false }),
  emailForAgent: () => 'agent',
}))

vi.mock('@aimail/mail', () => ({
  resolveBySessionId: async () => {
    throw new Error('unbound session')
  },
  resolveByEmail: async () => {
    throw new Error('unbound email')
  },
  resolveByRecipient: async () => {
    throw new Error('unbound recipient')
  },
}))

import { apply } from '../src/mail-service.ts'

/** 最小 ctx: apply() 往 ctx 上 provide('mail', service)。 */
function ctxStub(): never {
  return { provide: () => {}, get: () => undefined } as never
}
const tick = () => new Promise((r) => setTimeout(r, 0))

describe('dsh-aimail startup self-bootstrap guard', () => {
  beforeEach(() => {
    detectSystemForHome.mockReset()
    ensureSystem.mockReset()
  })

  it('home already owns a system → never reverse-calls ensure-system', async () => {
    detectSystemForHome.mockResolvedValue('sid-bound')
    apply(ctxStub(), {})
    await tick()
    expect(detectSystemForHome).toHaveBeenCalled()
    expect(ensureSystem).not.toHaveBeenCalled()
  })

  it('home owns nothing → reverse-calls ensure-system', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: true, systemId: 'sid-new', activated: false })
    apply(ctxStub(), {})
    await tick()
    expect(ensureSystem).toHaveBeenCalledWith({ systemHome: expect.any(String) })
  })

  it('fresh activation is announced, reuse stays quiet', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: true, systemId: 'sid-new', activated: true })
    const log = vi.spyOn(console, 'log').mockImplementation(() => {})
    apply(ctxStub(), {})
    await tick()
    expect(log.mock.calls.map((c) => String(c[0])).join('\n')).toContain('system activated: sid-new')
    log.mockRestore()
  })

  it('failure text says what is missing and how to fix it', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: false, error: 'Invalid activation code' })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    apply(ctxStub(), {})
    await tick()
    const text = warn.mock.calls.map((c) => String(c[0])).join('\n')
    expect(text).toContain('no AIMail system is bound to this dsh home yet')
    expect(text).toContain('Invalid activation code')
    expect(text).toContain('to activate: aimail install --home')
    warn.mockRestore()
  })

  it('detect failure never crashes apply()', async () => {
    detectSystemForHome.mockRejectedValue(new Error('fs blew up'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    apply(ctxStub(), {})
    await tick()
    expect(ensureSystem).not.toHaveBeenCalled()
    expect(warn.mock.calls.map((c) => String(c[0])).join('\n')).toContain('system check failed')
    warn.mockRestore()
  })
})
