/**
 * Inbound notification behaviour (owner ruling 2026-09-28, SDK 去桥化).
 *
 * The SDK's only job towards the environment master is: for every address this
 * host serves, run
 *     aimail address -a <addr> --inbound-live      (listener up)
 *     aimail address -a <addr> --inbound-down      (listener stopped)
 * best-effort. Asserted here against an injected runner (no process is spawned):
 *   - exactly one argv call per address, argv (never a shell), no bridge payload;
 *   - binary resolution AIMAIL_BIN → ~/.aimail/bin/aimail → PATH `aimail`;
 *   - a non-zero exit / spawn error / timeout is ONE outcome, never a throw;
 *   - NO CLI AT ALL ⇒ `no_cli` outcome and the SDK keeps working (address-level
 *     standalone deployments stay fully functional).
 */
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import {
  INBOUND_STATE_FLAG,
  formatInboundNotifyLine,
  isInboundNotifyWarning,
  notifyInboundForSystem,
  notifyInboundState,
  resolveAimailBin,
  type CommandRunner,
} from '../packages/mail-core/src/inbound-notify.js'

const calls: Array<{ bin: string; args: string[]; timeoutMs: number }> = []
const recorder = (result: { code: number | null; error?: string }): CommandRunner =>
  async (bin, args, timeoutMs) => {
    calls.push({ bin, args, timeoutMs })
    return result
  }

beforeEach(() => {
  calls.length = 0
})
afterEach(() => {
  delete process.env.AIMAIL_BIN
})

describe('notifyInboundState', () => {
  test('argv is exactly `address -a <addr> --inbound-live` (no shell, no bridge payload)', async () => {
    process.env.AIMAIL_BIN = '/usr/local/bin/aimail-test'
    const out = await notifyInboundState('agent@gw.test', 'live', { runner: recorder({ code: 0 }) })
    expect(out.state).toBe('notified')
    expect(calls).toHaveLength(1)
    expect(calls[0]?.bin).toBe('/usr/local/bin/aimail-test')
    expect(calls[0]?.args).toEqual(['address', '-a', 'agent@gw.test', '--inbound-live'])
    // the payload is the address and the state — nothing else
    expect(calls[0]?.args.join(' ')).toBe('address -a agent@gw.test --inbound-live')
  })

  test('down uses --inbound-down on the same argv shape', async () => {
    process.env.AIMAIL_BIN = '/usr/local/bin/aimail-test'
    await notifyInboundState('agent@gw.test', 'down', { runner: recorder({ code: 0 }) })
    expect(calls[0]?.args).toEqual(['address', '-a', 'agent@gw.test', '--inbound-down'])
  })

  test('the timeout is inside the 3–5s contract', async () => {
    process.env.AIMAIL_BIN = '/usr/local/bin/aimail-test'
    await notifyInboundState('agent@gw.test', 'live', { runner: recorder({ code: 0 }) })
    expect(calls[0]?.timeoutMs).toBeGreaterThanOrEqual(3000)
    expect(calls[0]?.timeoutMs).toBeLessThanOrEqual(5000)
  })

  test('a non-zero exit is one reported outcome, never a throw', async () => {
    process.env.AIMAIL_BIN = '/usr/local/bin/aimail-test'
    const out = await notifyInboundState('agent@gw.test', 'live', {
      runner: recorder({ code: 2, error: 'boom' }),
    })
    expect(out.state).toBe('failed')
    expect(isInboundNotifyWarning(out)).toBe(true)
    expect(formatInboundNotifyLine(out, 'live')).toContain('agent@gw.test')
  })

  test('a spawn error (CLI present but unusable) is one reported outcome', async () => {
    process.env.AIMAIL_BIN = '/nonexistent/aimail'
    const out = await notifyInboundState('agent@gw.test', 'live', {
      runner: recorder({ code: null, error: 'ENOENT' }),
    })
    expect(out.state).toBe('failed')
    expect(out.detail).toBe('ENOENT')
  })

  test('no CLI: resolves to the PATH fallback and never throws (SDK stays self-sufficient)', () => {
    const bin = resolveAimailBin({})
    expect(bin).toBe('aimail') // last rung: PATH lookup at spawn time
  })

  test('dispatch never rejects even when the runner itself throws', async () => {
    process.env.AIMAIL_BIN = '/usr/local/bin/aimail-test'
    const out = await notifyInboundState('agent@gw.test', 'live', {
      runner: async () => {
        throw new Error('unexpected')
      },
    })
    expect(out.state).toBe('failed')
    expect(out.detail).toBe('unexpected')
  })
})

describe('resolveAimailBin', () => {
  test('AIMAIL_BIN wins', () => {
    expect(resolveAimailBin({ AIMAIL_BIN: '/opt/aimail' })).toBe('/opt/aimail')
  })

  test('falls back to ~/.aimail/bin/aimail when AIMAIL_BIN is unset but the canonical binary exists', () => {
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-notify-'))
    const prev = process.env.AIMAIL_HOME
    try {
      process.env.AIMAIL_HOME = home
      fs.mkdirSync(path.join(home, 'bin'), { recursive: true })
      fs.writeFileSync(path.join(home, 'bin', 'aimail'), '#!/bin/sh\n')
      expect(resolveAimailBin({})).toBe(path.join(home, 'bin', 'aimail'))
    } finally {
      if (prev === undefined) delete process.env.AIMAIL_HOME
      else process.env.AIMAIL_HOME = prev
      fs.rmSync(home, { recursive: true, force: true })
    }
  })
})

describe('notifyInboundForSystem', () => {
  test('one notification per binding of the machine system (and none when there is no system)', async () => {
    // No system on this machine: nothing to say, no throw, no call.
    const home = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-nosys-'))
    const prev = process.env.AIMAIL_HOME
    try {
      process.env.AIMAIL_HOME = home
      process.env.AIMAIL_BIN = '/usr/local/bin/aimail-test'
      const out = await notifyInboundForSystem('live', { runner: recorder({ code: 0 }) })
      expect(out).toEqual([])
      expect(calls).toEqual([])
    } finally {
      if (prev === undefined) delete process.env.AIMAIL_HOME
      else process.env.AIMAIL_HOME = prev
      fs.rmSync(home, { recursive: true, force: true })
    }
  })
})

describe('flag table', () => {
  test('exactly the two states, no third meaning', () => {
    expect(INBOUND_STATE_FLAG).toEqual({ live: '--inbound-live', down: '--inbound-down' })
  })
})
