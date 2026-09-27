/**
 * The adapter's own wake path (owner ruling 2026-09-27, option A).
 *
 * Inbound mail only reaches the agent when openclaw's internal /hooks/agent endpoint
 * accepts the dispatch, and the adapter authenticates with `hooks.token` from the host
 * config (inbound.ts::readHooksToken). Without it the mail lands in the mail dir and
 * NOTHING replies, with no error shown to the operator — so the SDK must wire it itself.
 *
 * Boundary: generating a random token needs no information from the operator, so it is in
 * scope. LLM provider/model/key and agent bindings are NOT (administrator's job).
 */
import { mkdtempSync, mkdirSync, readFileSync, rmSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import * as path from 'node:path'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { ensureHooksToken } from '../src/identity.js'

let home: string
let cfgPath: string

beforeEach(() => {
  home = mkdtempSync(path.join(tmpdir(), 'oc-hooks-'))
  process.env.HOME = home
  cfgPath = path.join(home, '.openclaw', 'openclaw.json')
})

afterEach(() => {
  rmSync(home, { recursive: true, force: true })
})

describe('ensureHooksToken', () => {
  it('leaves a missing openclaw.json alone (never makes a "clobbered" config)', () => {
    expect(ensureHooksToken()).toBe('no-config')
    expect(() => readFileSync(cfgPath, 'utf-8')).toThrow()
  })

  it('creates the token when the config has none, preserving every other key', () => {
    mkdirSync(path.dirname(cfgPath), { recursive: true })
    writeFileSync(
      cfgPath,
      JSON.stringify({
        gateway: { mode: 'local', port: 18789 },
        models: { mode: 'merge', providers: { p: { baseUrl: 'http://127.0.0.1:8000/v1' } } },
        hooks: { internal: { entries: { 'session-memory': { enabled: true } } } },
      }),
    )
    expect(ensureHooksToken()).toBe('created')
    const after = JSON.parse(readFileSync(cfgPath, 'utf-8')) as Record<string, any>
    expect(after.hooks.token).toMatch(/^[0-9a-f]{48}$/)
    expect(after.gateway.mode).toBe('local')
    expect(after.models.providers.p.baseUrl).toBe('http://127.0.0.1:8000/v1')
    expect(after.hooks.internal.entries['session-memory'].enabled).toBe(true)
  })

  it('keeps an existing token untouched (idempotent)', () => {
    mkdirSync(path.dirname(cfgPath), { recursive: true })
    writeFileSync(cfgPath, JSON.stringify({ hooks: { token: 'operator-supplied' } }))
    expect(ensureHooksToken()).toBe('kept')
    const after = JSON.parse(readFileSync(cfgPath, 'utf-8')) as Record<string, any>
    expect(after.hooks.token).toBe('operator-supplied')
  })

  it('does not rewrite an unparsable config', () => {
    mkdirSync(path.dirname(cfgPath), { recursive: true })
    writeFileSync(cfgPath, '{ not json')
    expect(ensureHooksToken()).toBe('no-config')
    expect(readFileSync(cfgPath, 'utf-8')).toBe('{ not json')
  })
})
