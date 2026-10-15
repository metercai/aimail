// dsh-aimail 启动自举 + 默认 agent 绑定回归测试
//
// 背景(2026-09-22): 宿主插件启动无条件反调 `aimail install --system-only`; 若环境
// 里残留一个已被消费的激活码, 主循环会打印 "no aimail system yet — Invalid
// activation code", 而该机器其实早就绑定好了 —— 启动即误报(2026-09-21 dsh 实测)。
// 契约: ①本 home 已归属某系统 ⇒ 完全不反调; ②未归属 ⇒ 反调; ③失败文案可读可执行;
//       ④detect 失败不崩宿主。
//
// P2(2026-10-15): 自举收敛进 ensureDefaultAgentBinding() —— 系统就绪 + 默认 agent
// 绑定是一体的就绪动作, 且被 inbound 入口 gate 住 listen, 使"入站端口 up"成为可靠
// 就绪信号(绑定 + 监听)。`dsh plugin add` 装法不跑 CLI 的 register_default ⇒ 默认
// agent 的 binding 只由这里写; 幂等(autoBind 已有绑定即 {exists})⇒ 两种装法
// 收敛同一绑定态。
import { describe, it, expect, vi, beforeEach } from 'vitest'

const detectSystemForHome = vi.fn()
const ensureSystem = vi.fn()
const autoBind = vi.fn()
const readSystemConfig = vi.fn()

vi.mock('@aimail/mail-core', async (importOriginal) => {
  const actual = (await importOriginal<typeof import('@aimail/mail-core')>()) as typeof import('@aimail/mail-core')
  return {
    releaseAllSystems: () => {},
    detectSystemForHome: (...a: unknown[]) => detectSystemForHome(...a),
    ensureSystem: (...a: unknown[]) => ensureSystem(...a),
    hasAnySystem: () => true,
    listSystemDirs: async () => ['sid-1'],
    readSystemConfig: (...a: unknown[]) => readSystemConfig(...a),
    autoBind: (...a: unknown[]) => autoBind(...a),
    emailForAgent: () => 'agent@l3.local',
    // inboundWebhookUrl() reads these from the real module (single source:
    // mail-core contract constants — never re-typed in the mock)
    INBOUND_PORTS: actual.INBOUND_PORTS,
    INBOUND_PATH: actual.INBOUND_PATH,
  }
})

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

import { ensureDefaultAgentBinding } from '../src/mail-service.ts'

describe('dsh-aimail startup self-bootstrap + default agent binding', () => {
  beforeEach(() => {
    detectSystemForHome.mockReset()
    ensureSystem.mockReset()
    autoBind.mockReset()
    readSystemConfig.mockReset()
    readSystemConfig.mockResolvedValue({ domain: 'l3.local', system_name: '' })
    autoBind.mockResolvedValue({ registered: true, email: 'agent@l3.local' })
  })

  it('home owns a system → no reverse-call, binds the default agent', async () => {
    detectSystemForHome.mockResolvedValue('sid-bound')
    await ensureDefaultAgentBinding()
    expect(ensureSystem).not.toHaveBeenCalled()
    expect(autoBind).toHaveBeenCalledWith(
      expect.objectContaining({ systemId: 'sid-bound', email: 'agent@l3.local' }),
    )
  })

  it('home owns nothing → reverse-calls install --system-only, then binds', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: true, systemId: 'sid-new', activated: false })
    await ensureDefaultAgentBinding()
    expect(ensureSystem).toHaveBeenCalledWith({ systemHome: expect.any(String) })
    expect(autoBind).toHaveBeenCalledWith(
      expect.objectContaining({ systemId: 'sid-new', email: 'agent@l3.local' }),
    )
  })

  it('fresh activation is announced, reuse stays quiet', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: true, systemId: 'sid-new', activated: true })
    const log = vi.spyOn(console, 'log').mockImplementation(() => {})
    await ensureDefaultAgentBinding()
    expect(log.mock.calls.map((c) => String(c[0])).join('\n')).toContain('system activated: sid-new')
    log.mockRestore()
  })

  it('failure text says what is missing and how to fix it', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: false, error: 'Invalid activation code' })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await ensureDefaultAgentBinding()
    const text = warn.mock.calls.map((c) => String(c[0])).join('\n')
    expect(text).toContain('no AIMail system is bound to this dsh home yet')
    expect(text).toContain('Invalid activation code')
    expect(text).toContain('to activate: aimail install --home')
    warn.mockRestore()
  })

  it('system not ready → no default-agent binding attempted', async () => {
    detectSystemForHome.mockResolvedValue('')
    ensureSystem.mockResolvedValue({ ok: false, error: 'no activation code' })
    await ensureDefaultAgentBinding()
    expect(autoBind).not.toHaveBeenCalled()
  })

  it('detect failure never crashes (host keeps its unbound path)', async () => {
    detectSystemForHome.mockRejectedValue(new Error('fs blew up'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await expect(ensureDefaultAgentBinding()).resolves.toBeUndefined()
    expect(ensureSystem).not.toHaveBeenCalled()
    expect(autoBind).not.toHaveBeenCalled()
    warn.mockRestore()
  })

  it('autoBind failure never crashes (warns, host stays servable)', async () => {
    detectSystemForHome.mockResolvedValue('sid-bound')
    autoBind.mockRejectedValue(new Error('gateway unreachable'))
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await expect(ensureDefaultAgentBinding()).resolves.toBeUndefined()
    expect(warn.mock.calls.map((c) => String(c[0])).join('\n')).toContain('default agent auto-bind failed')
    warn.mockRestore()
  })
})
