/**
 * inbound-pipeline-parity — the inbound chain has ONE decision, host dirs only adapt.
 *
 * Why this test exists (owner 2026-11-27): "SSE 收到邮件到调起 session 之前的方法在 dsh/pi/openclaw
 * 三个 agent 都是一样的，只在地址绑定策略的路由和 session 的 mapping 上有各自的适配。不应该出现
 * 处理逻辑的不同。"
 *
 * That is exactly what had drifted: `dsh` used the shared `runtime/routing.ts
 * decideInboundDelivery()` (E1/E2/E3 + C16/I2 anchor check + C17 session-gone), while
 * `openclaw` kept a local `selectSessionKey()` that never looked at anchors — so a reply whose
 * parent session lived in ANOTHER anchor was dispatched straight into it, and the mandatory
 * `anchor-moved` bookkeeping could not appear (L2 S8: "no marker", "edge='-'").
 *
 * The rule enforced here:
 *   1. every host adapter with an inbound entry calls the shared decision;
 *   2. no host adapter re-implements routing (no local session-selection heuristic);
 *   3. the anchor-moved bookkeeping (§9.2 I2 / §9.3) appears on the dispatch record.
 *
 * pi is the documented exception and is asserted as such: it has no inbound module at all — its
 * session identity belongs to the CLI (HOST_CAN_ALLOCATE_SESSION=0, C17 `no-session-model`), so
 * there is no session for the inbound path to route to. That is a structural reason, not drift;
 * if pi ever grows an inbound module it must use the shared decision like everybody else.
 */
import { readdirSync, readFileSync } from 'node:fs'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'

const SRC = join(__dirname, '..', '..', 'src')

function read(rel: string): string {
  return readFileSync(join(SRC, rel), 'utf8')
}

const hostDirs = readdirSync(SRC, { withFileTypes: true })
  .filter((d) => d.isDirectory())
  .map((d) => d.name)

/** Hosts that carry an inbound entry today (dsh, openclaw) — discovered, not hardcoded. */
const inboundHosts = hostDirs.filter((h) => {
  try {
    read(join(h, 'inbound.ts'))
    return true
  } catch {
    return false
  }
})

describe('inbound pipeline parity', () => {
  it('finds the host inbound modules it is supposed to police', () => {
    expect(inboundHosts.sort()).toEqual(['dsh', 'openclaw'])
  })

  for (const host of inboundHosts) {
    it(`${host}: routes through the shared decideInboundDelivery()`, () => {
      const src = read(join(host, 'inbound.ts'))
      expect(src, `${host}/inbound.ts must import the shared routing decision`).toMatch(
        /decideInboundDelivery/,
      )
      expect(src, `${host}/inbound.ts must actually call it`).toMatch(
        /await\s+decideInboundDelivery\s*\(/,
      )
    })

    it(`${host}: does not re-implement session selection`, () => {
      // Strip comments first: the modules legitimately TALK about the removed heuristic, and a
      // plain text match flags documentation as a violation (it did, on the first run).
      const code = read(join(host, 'inbound.ts'))
        .split('\n')
        .filter((line) => {
          const t = line.trim()
          return !t.startsWith('*') && !t.startsWith('//') && !t.startsWith('/*')
        })
        .join('\n')
      // A host may MAP a route onto its own handle, but it must not decide the edge itself.
      expect(code, `${host}/inbound.ts must not call a local selectSessionKey()`).not.toMatch(
        /selectSessionKey\s*\(/,
      )
      // ...and the route it maps must be the shared one.
      expect(code).toMatch(/route\.kind/)
    })

    it(`${host}: records the anchor-moved bookkeeping`, () => {
      const src = read(join(host, 'inbound.ts'))
      expect(src, `${host}/inbound.ts must record anchor-moved (§9.2 I2 / §9.3)`).toMatch(
        /anchorMovedEdge/,
      )
      expect(src).toMatch(/anchorMovedFrom/)
    })
  }

  it('pi has no inbound module (structural: the CLI owns the session)', () => {
    // If this ever starts failing, pi grew an inbound path — route it through the shared decision
    // instead of relaxing this assertion.
    expect(inboundHosts).not.toContain('pi')
  })

  it('the shared decision is the only place that ranks the edges', () => {
    const routing = readFileSync(join(SRC, 'runtime', 'routing.ts'), 'utf8')
    for (const edge of ['direct', 'followup', 'new', 'gone']) {
      expect(routing, `routing.ts must keep edge ${edge}`).toMatch(new RegExp(`'${edge}'`))
    }
    // The anchor check that outranks I1 must stay in the shared layer, not be re-derived per host.
    expect(routing).toMatch(/anchorMoved/)
  })
})
