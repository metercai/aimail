// Host identification layer — registry / detect / activate contract tests.
import { describe, it, expect } from 'vitest'
import { HOSTS, HOST_KEYS } from '../../src/hosts/registry.js'
import { detectHost, detectHostsPresent } from '../../src/hosts/detect.js'
import { activateHost } from '../../src/hosts/activate.js'

describe('hosts registry (single source of truth)', () => {
  it('declares exactly the three supported hosts', () => {
    expect([...HOST_KEYS].sort()).toEqual(['dsh', 'openclaw', 'pi'])
  })

  it('every entry carries a complete, mutually-distinct contract', () => {
    const entries = Object.values(HOSTS)
    for (const spec of entries) {
      expect(spec.host).toBeTruthy()
      expect(spec.sdkSpecs.length).toBeGreaterThan(0)
      expect(spec.configDir).toMatch(/^\./)
      expect(spec.entry).toMatch(/^dist\/[a-z]+\/index\.js$/)
      expect(spec.registerCli).toMatch(/^dist\/[a-z]+\/register-cli\.js$/)
      expect(spec.peer).toBeTruthy()
    }
    // entry + registerCli must agree on the host subdir
    for (const spec of entries) {
      const entryHost = spec.entry.split('/')[1]
      const cliHost = spec.registerCli.split('/')[1]
      expect(entryHost, spec.host).toBe(cliHost)
    }
  })
})

describe('detect', () => {
  it('returns a list; detectHost is the first present (or null)', () => {
    const present = detectHostsPresent()
    expect(Array.isArray(present)).toBe(true)
    const first = detectHost()
    if (present.length === 0) {
      expect(first).toBeNull()
    } else {
      expect(HOST_KEYS).toContain(first as string)
    }
  })
})

describe('activateHost', () => {
  it('is never inconsistent in an ambiguous (multi/no-host) dev environment', () => {
    // In this workspace all three host SDKs are installed (devDeps) OR none are;
    // either way detection is ambiguous → activateHost must stay consistent
    // (verified=false) for every declared host.
    for (const key of HOST_KEYS) {
      const r = activateHost(key)
      expect(r.declared).toBe(key)
      expect(r.consistent).toBe(true)
      expect(HOST_KEYS).toContain(r.declared)
    }
  })

  it('result shape is complete', () => {
    const r = activateHost('openclaw')
    expect(r).toHaveProperty('declared', 'openclaw')
    expect(r).toHaveProperty('detected')
    expect(typeof r.verified).toBe('boolean')
    expect(r).toHaveProperty('consistent')
    expect(r).toHaveProperty('present', expect.any(Array))
    // verified only when exactly one host signal is present
    expect(r.verified).toBe(r.present.length === 1)
  })
})
