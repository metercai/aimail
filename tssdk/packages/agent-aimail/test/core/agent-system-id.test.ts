import { describe, expect, it } from 'vitest'
import * as os from 'node:os'
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

describe('AgentSystemId host-home resolver', () => {
  it('derives from the host resolver, not the OS home', async () => {
    const {
      getAgentSystemId,
      setAgentHomeResolver,
      resetAgentSystemIdForTests,
    } = await import('../../src/core/agent-system-id.js')
    const prevFp = process.env.AIMAIL_HARDWARE_FINGERPRINT
    const prevAsid = process.env.AIMAIL_AGENT_SYSTEM_ID
    const prevHome = process.env.AIMAIL_AGENT_HOME
    delete process.env.AIMAIL_AGENT_SYSTEM_ID
    delete process.env.AIMAIL_AGENT_HOME
    process.env.AIMAIL_HARDWARE_FINGERPRINT = 'resolver-test-machine'
    try {
      setAgentHomeResolver(() => '/agents/dsh/profiles/web')
      const dsh = await getAgentSystemId()
      setAgentHomeResolver(() => '/agents/pi')
      const pi = await getAgentSystemId()
      expect(dsh).toMatch(/^asid_[0-9a-f]{32}$/)
      expect(pi).toMatch(/^asid_[0-9a-f]{32}$/)
      expect(dsh).not.toBe(pi)
      setAgentHomeResolver(undefined)
      resetAgentSystemIdForTests()
      expect(await getAgentSystemId()).toBe(deriveAgentSystemId({
        agentHome: os.homedir(),
        hardwareFingerprint: 'resolver-test-machine',
      }))
    } finally {
      setAgentHomeResolver(undefined)
      resetAgentSystemIdForTests()
      if (prevFp === undefined) delete process.env.AIMAIL_HARDWARE_FINGERPRINT
      else process.env.AIMAIL_HARDWARE_FINGERPRINT = prevFp
      if (prevAsid === undefined) delete process.env.AIMAIL_AGENT_SYSTEM_ID
      else process.env.AIMAIL_AGENT_SYSTEM_ID = prevAsid
      if (prevHome === undefined) delete process.env.AIMAIL_AGENT_HOME
      else process.env.AIMAIL_AGENT_HOME = prevHome
    }
  })
})
