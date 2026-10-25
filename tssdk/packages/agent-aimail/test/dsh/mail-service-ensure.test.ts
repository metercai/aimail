// dsh-aimail startup bootstrap + default agent binding regression test
//
// P2 (2026-10-15): bootstrap converged into ensureDefaultAgentBinding() — system ready + default agent
// binding is the readiness action of a single body, and the inbound entry gates the listen, making "inbound port up" a reliable
// ready signal (binding + listening). `dsh plugin add` installation method does not run CLI's register_default ⇒ default
// agent's binding is only written here; idempotent ⇒ two installation methods converge to the same binding state.
//
// Batch 1+A (2026-10-16): system ready + address derivation + idempotent autoBind (adopt/register) have been merged into
// mail-core ensureAgentBinding (single source); this test only verifies dsh's thin shell: calls single source with correct parameters,
// failure does not crash host, success/failure logs readable. Single source's own semantics are covered by mail-core/ensure-binding.test.ts.
import { describe, it, expect, vi, beforeEach } from 'vitest'
import { INBOUND_PATH } from '../../src/core/index.js'

const ensureAgentBinding = vi.fn()

vi.mock('../../src/core/index.js', async (importOriginal) => {
  const actual = (await importOriginal<typeof import('../../src/core/index.js')>()) as typeof import('../../src/core/index.js')
  return {
    releaseAllSystems: () => {},
    ensureAgentBinding: (...a: unknown[]) => ensureAgentBinding(...a),
    // inboundWebhookUrl() reads these from the real module (single source:
    // mail-core contract constants — never re-typed in the mock)
    INBOUND_PORT: actual.INBOUND_PORT,
    INBOUND_PATH: actual.INBOUND_PATH,
  }
})

vi.mock('../../src/mail/index.js', () => ({
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

import { ensureDefaultAgentBinding } from '../../src/dsh/mail-service.js'

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
