import { describe, expect, test, beforeAll, beforeEach, afterAll } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { ensureSystem } from '../src/index.js'
import type { EnsureSystemOptions } from '../src/index.js'

let iso: string
let prevHome: string | undefined

const fakeExec =
  (code: number, stdout: string) =>
  async (): Promise<{ code: number; stdout: string }> => ({ code, stdout })

/** Exec that records its argv and answers with a scripted JSON line. */
function scriptedExec(script: { code: number; stdout: string }) {
  const calls: Array<{ cmd: string; args: string[] }> = []
  const exec = async (cmd: string, args: string[]): Promise<{ code: number; stdout: string }> => {
    calls.push({ cmd, args })
    return script
  }
  return Object.assign(exec, { _calls: calls })
}

const optsWith = (extra: EnsureSystemOptions & { exec: (c: string, a: string[]) => Promise<{ code: number; stdout: string }> }) => extra

beforeAll(() => {
  prevHome = process.env.AIMAIL_HOME
})
beforeEach(() => {
  if (iso) fs.rmSync(iso, { recursive: true, force: true })
  iso = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-es-'))
  process.env.AIMAIL_HOME = iso
})
afterAll(() => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  fs.rmSync(iso, { recursive: true, force: true })
})

describe('ensureSystem (CLI reverse-call ABI)', () => {
  test('system already present (no home scope) → ok, NO call-out (exec would throw)', async () => {
    const sid = 'sys-here'
    fs.mkdirSync(path.join(iso, 'systems', sid), { recursive: true })
    fs.writeFileSync(
      path.join(iso, 'systems', sid, 'aimail_gateway.json'),
      JSON.stringify({ system_id: sid }),
    )
    const r = await ensureSystem({
      exec: (() => {
        throw new Error('must not be called')
      }) as never,
    })
    expect(r.ok).toBe(true)
    expect(r.systemId).toBe(sid) // sole system
    expect(r.activated).toBe(false)
  })

  test('home not owned (other platform has a system) → reverse-calls CLI', async () => {
    // Another platform's system exists, but NOT for this home → must NOT
    // short-circuit (multi-platform machine: each platform gets its own).
    const sid = 'sys-hermes'
    fs.mkdirSync(path.join(iso, 'systems', sid), { recursive: true })
    fs.writeFileSync(
      path.join(iso, 'systems', sid, 'aimail_gateway.json'),
      JSON.stringify({ system_id: sid, system_home: '/home/u/.hermes' }),
    )
    const ex = scriptedExec({
      code: 0,
      stdout: JSON.stringify({ success: true, system_id: 'sys-dsh2', path: 'activation' }),
    })
    const r = await ensureSystem(optsWith({ systemHome: '/home/u/.dsh', exec: ex }))
    expect(r.ok).toBe(true)
    expect(r.activated).toBe(true)
    expect(r.systemId).toBe('sys-dsh2')
    const calls = (ex as unknown as { _calls: Array<{ cmd: string; args: string[] }> })._calls
    expect(calls[0]?.args).toEqual(['ensure-system', '-H', '/home/u/.dsh'])
  })

  test('home owned by THIS platform → short-circuits, no call-out', async () => {
    const sid = 'sys-dsh'
    fs.mkdirSync(path.join(iso, 'systems', sid), { recursive: true })
    fs.writeFileSync(
      path.join(iso, 'systems', sid, 'aimail_gateway.json'),
      JSON.stringify({ system_id: sid, system_home: '/home/u/.dsh' }),
    )
    const r = await ensureSystem({
      systemHome: '/home/u/.dsh',
      exec: (() => {
        throw new Error('must not be called')
      }) as never,
    })
    expect(r.ok).toBe(true)
    expect(r.systemId).toBe(sid)
    expect(r.activated).toBe(false)
  })

  test('empty machine → reverse-calls `aimail ensure-system -H` and parses JSON', async () => {
    const ex = scriptedExec({
      code: 0,
      stdout: JSON.stringify({
        success: true,
        system_id: 'sys-act',
        gateway_url: 'https://gw.invalid',
        domain: 'example.com',
        system_name: 'wguo',
        path: 'activation',
      }),
    })
    const r = await ensureSystem(optsWith({
      systemHome: '/home/u/.dsh',
      exec: ex,
    }))
    expect(r.ok).toBe(true)
    expect(r.activated).toBe(true)
    expect(r.systemId).toBe('sys-act')
    expect(r.domain).toBe('example.com')
    const calls = (ex as unknown as { _calls: Array<{ cmd: string; args: string[] }> })._calls
    expect(calls).toEqual([{ cmd: 'aimail', args: ['ensure-system', '-H', '/home/u/.dsh'] }])
  })

  test('reuse path (path=admin_key) → ok with activated=false', async () => {
    const ex = scriptedExec({
      code: 0,
      stdout: JSON.stringify({ success: true, system_id: 'sys-old', path: 'admin_key' }),
    })
    const r = await ensureSystem(optsWith({ systemHome: '/home/u/.dsh', exec: ex }))
    expect(r.ok).toBe(true)
    expect(r.activated).toBe(false)
    expect(r.systemId).toBe('sys-old')
  })

  test('CLI error JSON → surfaced error + hint', async () => {
    const ex = scriptedExec({
      code: 1,
      stdout: JSON.stringify({
        success: false,
        error: 'Activation code already claimed',
        hint: 'export AIMAIL_URL + AIMAIL_PRODUCT_CODE then retry',
      }),
    })
    const r = await ensureSystem(optsWith({ exec: ex }))
    expect(r.ok).toBe(false)
    expect(r.error).toBe('Activation code already claimed')
    expect(r.hint).toMatch(/AIMAIL_PRODUCT_CODE/)
  })

  test('CLI missing (ENOENT) → actionable bootstrap hint', async () => {
    const exec = async (): Promise<{ code: number; stdout: string }> => {
      throw new Error("aimail CLI not found ('aimail') — run bootstrap first")
    }
    const r = await ensureSystem(optsWith({ exec }))
    expect(r.ok).toBe(false)
    expect(r.error).toMatch(/bootstrap/)
    expect(r.hint).toMatch(/bootstrap/)
  })

  test('unparsable output → actionable error', async () => {
    const ex = scriptedExec({ code: 1, stdout: 'Traceback ... boom' })
    const r = await ensureSystem(optsWith({ exec: ex }))
    expect(r.ok).toBe(false)
    expect(r.error).toMatch(/unparsable/)
  })
})
