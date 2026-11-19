import { describe, expect, it } from 'vitest'
import { deriveAgentSystemId } from '../../src/core/agent-system-id.js'

describe('AgentSystemId derivation', () => {
  it('is stable for the same home and hardware', () => {
    const a = deriveAgentSystemId({ agentHome: '/home/a', hardwareFingerprint: 'machine-1' })
    const b = deriveAgentSystemId({ agentHome: '/home/a', hardwareFingerprint: 'machine-1' })
    expect(a).toBe(b)
    expect(a).toMatch(/^asid_[0-9a-f]{32}$/)
  })

  it('changes when the agent home changes', () => {
    const a = deriveAgentSystemId({ agentHome: '/home/a', hardwareFingerprint: 'machine-1' })
    const b = deriveAgentSystemId({ agentHome: '/home/b', hardwareFingerprint: 'machine-1' })
    expect(a).not.toBe(b)
  })

  it('changes when hardware changes', () => {
    const a = deriveAgentSystemId({ agentHome: '/home/a', hardwareFingerprint: 'machine-1' })
    const b = deriveAgentSystemId({ agentHome: '/home/a', hardwareFingerprint: 'machine-2' })
    expect(a).not.toBe(b)
  })

  it('rejects an empty fingerprint', () => {
    expect(() => deriveAgentSystemId({ agentHome: '/home/a', hardwareFingerprint: '  ' }))
      .toThrow(/empty hardware fingerprint/)
  })
})
