// contract v1.0 §4.1(2) regression nail: the op path (--op/--args) stdout must be **exactly one line of JSON**.
//
// Defect (after B3 openclaw L2 actual run, J5-7a/7d red):
//   openclaw's op path dispatches via **full plugin register(api)** (unlike dsh/pi which uses independent
//   register-cli.ts). Host lifecycle diagnostics in register(api) (startInboundPull /
//   ensureSystem / inbound notify) use console.log to print to **stdout** ⇒ op succeeds (rc=0)
//   but stdout has 7 lines ⇒ CLI Transport judges "executable gate output abnormal (rc=0, 7 lines)" ⇒ gate red.
//   First line evidence: `[aimail-pull] agent@…: not polling (not-agent-scope) — push/system
//   binding unchanged`。
//
// This test pins "op path boot plugin then stdout zero diagnostic lines" locally:
//   ① register(api) throughout (including async settle) **must not call console.log** (stdout clean);
//   ② Diagnostic lines are allowed to go to console.error (stderr) — the contract only constrains stdout.
//   op result JSON goes through the command action's process.stdout.write, not on this register path.
import { describe, it, expect, vi, afterEach } from 'vitest'

describe('openclaw-aimail op-path stdout cleanliness (Contract §4.1(2))', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('register(api) emits ZERO console.log (stdout stays clean for the op path)', async () => {
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    const mod = (await import('../../src/openclaw/index.js')) as {
      default?: { register?: (api: unknown) => void }
    }
    const entry = mod.default
    expect(typeof entry?.register).toBe('function')

    const api = {
      registerTool(fn: (ctx: unknown) => unknown[] | unknown) {
        const list = fn({ agentId: 'main' })
        return Array.isArray(list) ? list.length : 1
      },
      registerHttpRoute() {},
      registerCommand() {},
      registerCli() {},
    }
    entry!.register!(api)

    // register internally fire-and-forget startInboundPull / ensureSystem /
    // inbound notify (all void async). Give them enough macrotask rounds to drain diagnostic lines,
    // Assert again — otherwise async diagnostic lines will land after the assertion, causing a false green test.
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 10))
    }

    // Contract: op path stdout zero diagnostic lines ⇒ register throughout must not console.log.
    expect(logSpy).not.toHaveBeenCalled()
    // Diagnostic lines are allowed to land on stderr (console.error) — the contract only constrains stdout, so the count is not asserted here.
    void errSpy
    void warnSpy
  })
})
