/**
 * E3/S1 (owner ruling 2026-09-27): the adapter must wire its OWN wake path.
 *
 * token alone is not enough — the host serves /hooks/* only when `hooks.enabled` is true
 * (host docs /gateway/config-hooks: "404 ... Disabled hooks fall through"). Measured:
 * with a token but enabled unset, the dispatch died with "hooks/agent HTTP 404".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { mkdirSync, mkdtempSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import * as path from 'node:path'
import { ensureHooksWiring, hooksPath, resolveAgentId } from '../src/identity.js'

let home = ''
const cfgDir = () => path.join(home, '.openclaw')
const cfgFile = () => path.join(cfgDir(), 'openclaw.json')

beforeEach(() => {
  home = mkdtempSync(path.join(tmpdir(), 'oc-hooks-'))
  vi.stubEnv('HOME', home)
})
afterEach(() => {
  vi.unstubAllEnvs()
  rmSync(home, { recursive: true, force: true })
})

describe('ensureHooksWiring', () => {
  it('leaves a missing config alone (never fabricates one)', () => {
    expect(ensureHooksWiring()).toBe('no-config')
    expect(() => readFileSync(cfgFile(), 'utf-8')).toThrow()
  })

  it('creates the token AND enables the endpoints, keeping other keys', () => {
    mkdirSync(cfgDir(), { recursive: true })
    writeFileSync(cfgFile(), JSON.stringify({ gateway: { mode: 'local' } }))
    expect(ensureHooksWiring()).toBe('created')
    const after = JSON.parse(readFileSync(cfgFile(), 'utf-8'))
    expect(after.hooks.token).toMatch(/^[0-9a-f]{48}$/)
    expect(after.hooks.enabled).toBe(true)
    expect(after.gateway.mode).toBe('local')
  })

  it('is idempotent once wired', () => {
    mkdirSync(cfgDir(), { recursive: true })
    writeFileSync(cfgFile(), JSON.stringify({ hooks: { enabled: true, token: 'x'.repeat(48) } }))
    expect(ensureHooksWiring()).toBe('kept')
  })

  it('never overrides an operator who disabled hooks', () => {
    mkdirSync(cfgDir(), { recursive: true })
    writeFileSync(cfgFile(), JSON.stringify({ hooks: { enabled: false, token: 'operator' } }))
    expect(ensureHooksWiring()).toBe('disabled')
    const after = JSON.parse(readFileSync(cfgFile(), 'utf-8'))
    expect(after.hooks.enabled).toBe(false)
    expect(after.hooks.token).toBe('operator')
  })

  it('hooksPath honours the host prefix and defaults to /hooks', () => {
    expect(hooksPath()).toBe('/hooks')
    mkdirSync(cfgDir(), { recursive: true })
    writeFileSync(cfgFile(), JSON.stringify({ hooks: { path: 'gateway-hooks/' } }))
    expect(hooksPath()).toBe('/gateway-hooks')
  })
})

describe('resolveAgentId (S1b)', () => {
  it('prefers the configured owner over entries', () => {
    mkdirSync(cfgDir(), { recursive: true })
    writeFileSync(cfgFile(), JSON.stringify({ agents: { defaults: { sessionStore: { agentId: 'owner' } }, entries: { other: {} } } }))
    expect(resolveAgentId()).toBe('owner')
  })
  it('never guesses: empty config means empty id', () => {
    expect(resolveAgentId()).toBe('')
  })
})
