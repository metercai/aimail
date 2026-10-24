// Contract v1.0 §4.1(2) regression nail (owner 2026-10-11 ruling: node side uniformly node_entry):
// Independent entry `dist/register-cli.js` stdout must be **exactly one line JSON**.
//
// Relationship with op-stdout-clean.test.ts: that nail guarantees "if dispatched via host register(api) also must not
// console.log"; this nail guarantees independent entry **does not load host plugin at all** envelope still holds, and covers
// usage/call two types of errors exit code and shape (same shape as python gate, dsh/pi entry).
//
// main() is exported ⇒ no need for process.exit in test process (entry self-run guarded by argv[1] tail name).
import { describe, it, expect, vi, afterEach, beforeEach } from 'vitest'

import { main } from '../../src/openclaw/register-cli.js'

type Captured = { out: string[]; err: string[] }

function capture(): Captured {
  const c: Captured = { out: [], err: [] }
  vi.spyOn(process.stdout, 'write').mockImplementation((chunk: unknown) => {
    c.out.push(String(chunk))
    return true
  })
  vi.spyOn(process.stderr, 'write').mockImplementation((chunk: unknown) => {
    c.err.push(String(chunk))
    return true
  })
  return c
}

function envelope(c: Captured): { lines: string[]; json: Record<string, unknown> } {
  const lines = c.out.join('').split('\n').filter((l) => l.length > 0)
  expect(lines).toHaveLength(1) // Exactly one line ⇒ no host lifecycle diagnostics mixed into stdout
  return { lines, json: JSON.parse(lines[0]!) as Record<string, unknown> }
}

describe('openclaw-aimail register-cli (node_entry, contract §4.1(2))', () => {
  beforeEach(() => {
    vi.restoreAllMocks()
  })
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('missing --op ⇒ usage envelope + exit 2', async () => {
    const c = capture()
    const rc = await main([])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['ok']).toBe(false)
    expect(json['kind']).toBe('usage')
    expect(String(json['error'])).toContain('--op')
  })

  it('unknown op ⇒ usage envelope + exit 2 (listing expected set)', async () => {
    const c = capture()
    const rc = await main(['--op', 'bogus', '--args', '{}'])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['kind']).toBe('usage')
    expect(String(json['error'])).toContain("unknown op 'bogus'")
    expect(String(json['error'])).toContain('prompt-test')
  })

  it('--args not a JSON object ⇒ usage envelope + exit 2', async () => {
    const c = capture()
    const rc = await main(['--op', 'assemble', '--args', 'not-json'])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['kind']).toBe('usage')
    expect(String(json['error'])).toContain('not valid JSON')
  })

  it('assemble missing system_id ⇒ UsageError inside gate bubbles up as usage envelope + exit 2 (no network)', async () => {
    const c = capture()
    const rc = await main(['--op', 'assemble', '--args', '{}'])
    const { json } = envelope(c)
    expect(rc).toBe(2)
    expect(json['kind']).toBe('usage')
    expect(json['exc']).toBe('UsageError')
    expect(String(json['error'])).toContain('system_id')
    // Diagnostics are allowed on stderr (contract only constrains stdout)
    expect(c.err.join('')).toContain('register-cli: assemble failed')
  })
})
