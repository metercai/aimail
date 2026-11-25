/**
 * Deliberately thin: this file used to pin the `--inbound-live` ordering ("arm the poll only after
 * the listener/route is up") plus a per-host source inventory. Both sides of that sentence died on
 * 2026-11-25: there is no listener, no route and no `inbound-notify` channel any more, so "after the
 * listener is live" no longer means anything.
 *
 * What still matters, and is cheap to pin at the source level, is the shape of the delivery path:
 *
 *   1. no host module binds a local endpoint (the real guard is
 *      `tests/contract/check-legacy-push-scope.py`; this is the TS-side echo);
 *   2. the SDK-owned hosts pull (`startInboundPull`), and
 *   3. dsh delivers through ONE function (`deliverInbound`), so the hub and the fixtures cannot drift
 *      into two different routers again.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { describe, expect, test } from 'vitest'

const REPO = path.resolve(__dirname, '..')
const SRC = path.join(REPO, 'packages', 'agent-aimail', 'src')

function read(rel: string): string {
  return fs.readFileSync(path.join(SRC, rel), 'utf-8')
}

/** Strip line comments: an explanatory note may name what was retired. */
// Strip comment LINES only (slash-slash, block open, and continuation stars).
// Deliberately not a block-comment regex: host sources contain glob strings such as
//   path.join(home, 'systems', '*', '*', 'skills')
// and a block-comment pass would eat everything from such a literal to the next closer.
function code(rel: string): string {
  return read(rel)
    .split('\n')
    .filter((l) => {
      const t = l.trim()
      return !(t.startsWith('//') || t.startsWith('/*') || t.startsWith('*'))
    })
    .map((l) => l.replace(/\s\/\/.*$/, ''))
    .join('\n')
}

const HOSTS = [
  'pi/index.ts',
  'openclaw/index.ts',
  'openclaw/inbound.ts',
  'dsh/inbound.ts',
  'dsh/mail-hub.ts',
]

describe('delivery path shape (SSE/pull only)', () => {
  test('no host binds or registers a local endpoint', () => {
    const offenders = HOSTS.filter((rel) => {
      const src = code(rel)
      return (
        src.includes('createServer') ||
        src.includes('listenWithRetry') ||
        src.includes('registerHttpRoute') ||
        src.includes('verifySignature')
      )
    })
    expect(offenders).toEqual([])
  })

  test('the SDK-owned hosts arm the shared pull loop', () => {
    for (const rel of ['pi/index.ts', 'openclaw/index.ts']) {
      expect(code(rel)).toContain('startInboundPull(')
    }
  })

  test('dsh delivers through exactly one function', () => {
    const hub = code('dsh/mail-hub.ts')
    expect(hub).toContain('deliverInbound(')
    // The hub must not re-implement routing: it hands the mail to the shared decision.
    expect(hub).not.toContain('decideInboundDelivery')
    expect(hub).not.toContain('routeThread')
    const core = code('dsh/inbound.ts')
    expect(core).toContain('decideInboundDelivery(')
    expect(core).toContain('logAimailDispatch(')
  })
})
