/**
 * Pull entry wiring (source level): `startInboundPull` is armed only AFTER the
 * host's inbound entry is live, and every host that RETAINS the handles stops
 * them when its lifecycle ends.
 *
 * Companion to startup-hook.test.ts (route side) — same technique (assert the
 * source order, read from the shipped sources), different wire. Why the order
 * is the substance: pulled mail is handed to the SAME inbound chain the live
 * entry serves (`deliverInbound` / `deliverPiInbound`), so arming the poll
 * before the listener/route exists delivers into a dead entry (and a retained
 * handle nobody stops outlives the host it belongs to).
 *
 * openclaw is the one host whose plugin API exposes no dispose hook: it does
 * NOT retain the handles, and mail-core unrefs every poll timer, so a live loop
 * can never pin the process. That claim is asserted here against mail-core's
 * own source, not taken on faith.
 *
 * Contract values come from mail-core (`INBOUND_PATH`) — never written as
 * literals in this file (the L0 literal ratchet counts file x literal and only
 * allows decreases).
 *
 * COVERAGE GAP (registered, not hidden): hermes/deer-flow are Python hosts with
 * no `startInboundPull` symbol; their pull wiring is asserted on their own side
 * (`tests/test_*.py`) and behaviourally in the SDK gate E2-pull scenario
 * (aimail-advanced tests/SDK/docker-regression/hosts/run-e2-pull-host.sh).
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { describe, expect, test } from 'vitest'
import { INBOUND_PATH } from '../packages/mail-core/src/contract'

const REPO = path.resolve(__dirname, '..')

/** mail-core's own poll loop, which every retained handle comes from. */
const CORE_POLL = 'packages/mail-core/src/address-code.ts'
/** The stop line both retaining hosts use for the handles they keep. */
const STOP_LINE = 'for (const h of pullHandles) h.stop()'

interface HostPull {
  name: string
  file: string
  /** Marker for the inbound entry the pulled mail must enter. */
  live: string
  liveLabel: string
  /** Marker for the adapter's own poll arming call. */
  pull: string
  /**
   * Lifecycle-end marker the handles are stopped from; `''` ⇒ the host does
   * NOT retain them (process-lifetime by construction).
   */
  dispose: string
  /** The host file that DEFINES the pull entry (openclaw defines it elsewhere). */
  impl: string
}

const HOSTS: HostPull[] = [
  {
    name: 'dsh',
    file: 'packages/dsh-aimail/src/inbound.ts',
    live: 'server.listen(port, host',
    liveLabel: 'the inbound listener',
    pull: 'void startInboundPull(ctx, ',
    dispose: 'return () => {',
    impl: 'packages/dsh-aimail/src/inbound.ts',
  },
  {
    name: 'openclaw',
    file: 'packages/openclaw-aimail/src/index.ts',
    live: 'api.registerHttpRoute({',
    liveLabel: 'the in-gateway HTTP route',
    pull: 'void startInboundPull(api, ',
    // OpenClaw's plugin API has no dispose hook ⇒ nothing to register a stop on.
    dispose: '',
    impl: 'packages/openclaw-aimail/src/inbound.ts',
  },
  {
    name: 'pi',
    file: 'packages/pi-aimail/src/index.ts',
    live: "server.listen(port, '127.0.0.1'",
    liveLabel: 'the inbound listener',
    pull: 'void startInboundPull(pi, ',
    dispose: "pi.on('session_shutdown', () => {",
    impl: 'packages/pi-aimail/src/index.ts',
  },
]

function read (rel: string): string {
  return fs.readFileSync(path.join(REPO, rel), 'utf-8')
}

/** The substance: the poll is armed downstream of the inbound entry's liveness. */
function pullArmedAfterInboundLive (src: string, live: string, pull: string): boolean {
  const liveAt = src.indexOf(live)
  const pullAt = src.indexOf(pull)
  return liveAt > -1 && pullAt > -1 && pullAt > liveAt
}

/**
 * Lines where `literal` sits in CODE (not inside a comment). Hosts legitimately
 * restate the default inbound path in JSDoc; a code line carrying it is the
 * drift the contract constant exists to prevent.
 */
function codeLiteralLines (src: string, literal: string): string[] {
  const out: string[] = []
  let inBlock = false
  for (const line of src.split('\n')) {
    const at = line.indexOf(literal)
    if (at >= 0) {
      let commentBefore = inBlock
      for (let i = 0; i < at && !commentBefore; i++) {
        if (line.startsWith('/*', i) || line.startsWith('//', i)) commentBefore = true
      }
      if (!commentBefore) out.push(line)
    }
    for (let j = 0; j < line.length; j++) {
      if (!inBlock && line.startsWith('/*', j)) { inBlock = true; j++ } else if (inBlock && line.startsWith('*/', j)) { inBlock = false; j++ }
    }
  }
  return out
}

describe('host startup hooks (pull side)', () => {
  test('the ordering predicate is not vacuous (inverted/missing markers ⇒ false)', () => {
    const live = 'LIVE'
    const pull = 'PULL'
    expect(pullArmedAfterInboundLive(`${live}\n${pull}`, live, pull)).toBe(true)
    expect(pullArmedAfterInboundLive(`${pull}\n${live}`, live, pull)).toBe(false)
    expect(pullArmedAfterInboundLive(`${live}\n`, live, pull)).toBe(false)
    expect(pullArmedAfterInboundLive(`${pull}\n`, live, pull)).toBe(false)
  })

  for (const h of HOSTS) {
    test(`${h.name}: arms its own poll after ${h.liveLabel} is live`, () => {
      const src = read(h.file)
      expect(
        src.includes(h.pull),
        `${h.name}: pull entry not wired (${h.pull})`,
      ).toBe(true)
      expect(
        src.indexOf(h.live),
        `${h.name}: inbound registration not found (${h.live})`,
      ).toBeGreaterThan(-1)
      expect(
        pullArmedAfterInboundLive(src, h.live, h.pull),
        `${h.name}: the poll must be armed after the inbound entry is live ` +
          '(pulled mail enters that same chain)',
      ).toBe(true)
    })

    test(`${h.name}: the poll is mail-core's loop, not a hand-rolled one`, () => {
      const impl = read(h.impl)
      expect(
        impl.includes('startAgentPullEntries('),
        `${h.name}: the pull entry must delegate to the shared mail-core loop`,
      ).toBe(true)
      // A local timer would be a second, unowned polling implementation —
      // exactly the "hand-rolled pull" the gate scenario refuses to credit.
      expect(
        impl.includes('setInterval(') || impl.includes('setTimeout('),
        `${h.name}: no private polling loop (mail-core owns it)`,
      ).toBe(false)
    })
  }

  for (const h of HOSTS.filter((x) => x.dispose !== '')) {
    test(`${h.name}: stops the handles it retains at its lifecycle end`, () => {
      const src = read(h.file)
      const pullAt = src.indexOf(h.pull)
      const disposeAt = src.indexOf(h.dispose)
      expect(
        src.includes('pullHandles'),
        `${h.name}: the handles are kept — they must be stopped with the host`,
      ).toBe(true)
      expect(
        disposeAt,
        `${h.name}: no lifecycle end registered (${h.dispose})`,
      ).toBeGreaterThan(-1)
      const stopAt = src.indexOf(STOP_LINE)
      expect(stopAt, `${h.name}: retained handles are never stopped`).toBeGreaterThan(pullAt)
      // `stop()` must sit downstream of the lifecycle registration: that is what
      // makes "shutdown stops the poll" true rather than incidental.
      expect(
        src.slice(disposeAt).includes(STOP_LINE),
        `${h.name}: the stop must live inside the lifecycle callback (${h.dispose})`,
      ).toBe(true)
    })
  }

  const noDispose = HOSTS.filter((x) => x.dispose === '')
  for (const h of noDispose) {
    test(`${h.name}: retains no handle, and mail-core's timers are unref'd (process-lifetime)`, () => {
      const src = read(h.file)
      expect(
        src.includes('pullHandles'),
        `${h.name}: keeps handles but has no dispose hook to stop them from`,
      ).toBe(false)
      // The pairing that makes "no dispose needed" safe: every poll timer must
      // be unref'd, else a live loop would keep the host process alive.
      const core = read(CORE_POLL)
      expect(
        core.includes('.unref?.()'),
        'mail-core: the poll timer must be unref\'d — openclaw relies on it',
      ).toBe(true)
    })
  }

  test('every host takes its inbound path from the contract constant, never a code literal', () => {
    for (const h of HOSTS) {
      const src = read(h.file)
      // Documented in comments is fine (JSDoc restates the default); a CODE line
      // carrying the path is the drift the constant exists to prevent.
      const codeLiterals = codeLiteralLines(src, INBOUND_PATH)
      expect(
        codeLiterals,
        `${h.name}: the inbound path must come from mail-core, not a code literal`,
      ).toEqual([])
      expect(
        src.includes('INBOUND_PATH'),
        `${h.name}: no reference to the shared contract constant`,
      ).toBe(true)
    }
  })
})
