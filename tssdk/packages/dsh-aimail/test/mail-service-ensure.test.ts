// dsh-aimail 启动自举 + 默认 agent 绑定回归测试
//
// P2(2026-10-15): 自举收敛进 ensureDefaultAgentBinding() —— 系统就绪 + 默认 agent
// 绑定是一体的就绪动作, 且被 inbound 入口 gate 住 listen, 使"入站端口 up"成为可靠
// 就绪信号(绑定 + 监听)。`dsh plugin add` 装法不跑 CLI 的 register_default ⇒ 默认
// agent 的 binding 只由这里写; 幂等 ⇒ 两种装法收敛同一绑定态。
//
// 批1+A(2026-10-16): 系统就绪 + 地址派生 + 幂等 autoBind(adopt/register)已归并到
// mail-core ensureAgentBinding(单源); 本测试只验 dsh 的薄壳: 以正确参数调用单源、
// 失败不崩宿主、成功/失败日志可读。单源自身语义由 mail-core/ensure-binding.test.ts 覆盖。
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { INBOUND_PATH } from '@aimail/mail-core'

const ensureAgentBinding = vi.fn()

vi.mock('@aimail/mail-core', async (importOriginal) => {
  const actual = (await importOriginal<typeof import('@aimail/mail-core')>()) as typeof import('@aimail/mail-core')
  return {
    releaseAllSystems: () => {},
    ensureAgentBinding: (...a: unknown[]) => ensureAgentBinding(...a),
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
    ensureAgentBinding.mockReset()
    ensureAgentBinding.mockResolvedValue({ ok: true, email: 'agent@l3.local', exists: false })
  })

  it('calls the single-source binder with home + default agent id + inbound webhook', async () => {
    await ensureDefaultAgentBinding()
    expect(ensureAgentBinding).toHaveBeenCalledWith(
      expect.objectContaining({
        home: expect.any(String),
        agentId: 'agent',
        webhookUrl: expect.stringContaining(INBOUND_PATH),
        log: expect.any(Function),
      }),
    )
  })

  it('success is announced via the log sink', async () => {
    const lines: string[] = []
    ensureAgentBinding.mockImplementation(async (opts: { log?: (l: string) => void }) => {
      opts.log?.('default agent bound: agent@l3.local')
      return { ok: true, email: 'agent@l3.local', exists: false }
    })
    const log = vi.spyOn(console, 'log').mockImplementation(() => {})
    await ensureDefaultAgentBinding()
    expect(log.mock.calls.map((c) => String(c[0])).join('\n')).toContain('default agent bound: agent@l3.local')
    log.mockRestore()
  })

  it('binding failure never crashes the host (warns, stays servable)', async () => {
    ensureAgentBinding.mockResolvedValue({ ok: false, error: 'gateway unreachable' })
    const warn = vi.spyOn(console, 'warn').mockImplementation(() => {})
    await expect(ensureDefaultAgentBinding()).resolves.toBeUndefined()
    expect(warn.mock.calls.map((c) => String(c[0])).join('\n')).toContain('default agent binding failed')
    expect(warn.mock.calls.map((c) => String(c[0])).join('\n')).toContain('gateway unreachable')
    warn.mockRestore()
  })
})
