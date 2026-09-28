/**
 * Host startup hooks: every host TELLS THE ENVIRONMENT MASTER (the `aimail` CLI)
 * that its inbound is live, once its INBOUND IS UP (owner ruling 2026-09-28,
 * SDK 去桥化).
 *
 * Why this file exists: the restore contract has two halves. The route side is now
 * the CLI's business (`cli/bridge_wire.py`: `aimail address --inbound-live` pushes
 * the route only while inbound really serves); the SDK's half is exactly one
 * thing — a best-effort, per-address notification emitted from the startup path.
 * The notification BEHAVIOUR (argv, timeout, missing CLI, never throwing) is
 * asserted in tssdk/test/inbound-notify.test.ts against an injected runner.
 *
 * What is asserted here is the WIRING, at source level, for all three hosts:
 *   - the startup path notifies the CLI, and
 *   - the call comes AFTER the inbound endpoint is live (the timing substance:
 *     pushing a route before the listener is up is what got it pruned in
 *     production on 2026-09-26 — created 15:53:17, removed 15:56:07), and
 *   - it is fire-and-forget + failure-tolerant (`void … .catch(…)`: it can never
 *     reach the host), and
 *   - the outcome is reported (never swallowed), and
 *   - the host source carries ZERO bridge symbols (the SDK speaks no bridge).
 *
 * Shutdown: the two hosts that own a teardown hook (dsh fiber dispose, pi
 * session_shutdown) also notify `--inbound-down`; openclaw's plugin API exposes
 * no dispose hook (its own comment says so), so it notifies live only.
 *
 * COVERAGE GAP (registered, not hidden): the dsh hook cannot be exercised
 * end-to-end here — importing packages/dsh-aimail/src/inbound.ts pulls
 * '@deepseek-ai/dsh-llm', whose peer dep '@deepseek-ai/dsh-timeout' is not
 * installed in this workspace (module resolution fails before any mock applies).
 * dsh's hook therefore runs for real only in the L2 host container with the
 * packaged plugin; this file keeps its wiring pinned until that is assertable.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { describe, expect, test } from 'vitest'

const REPO = path.resolve(__dirname, '..')

/** Retired bridge symbols: the SDK (and therefore every host) must not carry one. */
const BRIDGE_SYMBOLS = [
  'ensureBridgeRoutesForSystem',
  'ensureBridgeRoute',
  'registerBridgeRoute',
  'formatBridgeRouteLine',
  'isBridgeRouteWarning',
  'bridgeListening',
  'resolveBridgeAdminPort',
  'BridgeRouteOutcome',
  'BRIDGE_DEFAULT_PATH',
]

const HOSTS = [
  {
    name: 'dsh',
    file: 'packages/dsh-aimail/src/inbound.ts',
    live: 'server.listen(port, host',
    liveLabel: 'the inbound listener',
    down: true,
  },
  {
    name: 'openclaw',
    file: 'packages/openclaw-aimail/src/index.ts',
    live: 'api.registerHttpRoute({',
    liveLabel: 'the in-gateway HTTP route',
    down: false,
  },
  {
    name: 'pi',
    file: 'packages/pi-aimail/src/index.ts',
    live: "server.listen(port, '127.0.0.1'",
    liveLabel: 'the inbound listener',
    down: true,
  },
]

describe('host startup hooks (inbound notification, zero bridge)', () => {
  for (const h of HOSTS) {
    test(`${h.name}: notifies the CLI after ${h.liveLabel} is live, fire-and-forget, and reports the outcome`, () => {
      const src = fs.readFileSync(path.join(REPO, h.file), 'utf-8')
      const liveAt = src.indexOf(h.live)
      const call = `void notifyInboundForSystem('live')`
      const hookAt = src.indexOf(call)
      expect(liveAt, `${h.name}: inbound registration not found (${h.live})`).toBeGreaterThan(-1)
      expect(hookAt, `${h.name}: the inbound notification is not wired`).toBeGreaterThan(-1)
      expect(hookAt, `${h.name}: the notification must come after the inbound endpoint is live`)
        .toBeGreaterThan(liveAt)
      // fire-and-forget + failure-tolerant: the host must not be able to see it
      const tail = src.slice(hookAt, hookAt + 900)
      expect(tail.includes('.catch('), `${h.name}: the notification must not escape into the host`)
        .toBe(true)
      expect(src.includes('formatInboundNotifyLine('), `${h.name}: outcome must be reported, not swallowed`)
        .toBe(true)
      expect(src.includes('isInboundNotifyWarning('), `${h.name}: a failure must be a warning line`)
        .toBe(true)
    })

    test(`${h.name}: carries zero bridge symbols`, () => {
      const src = fs.readFileSync(path.join(REPO, h.file), 'utf-8')
      for (const sym of BRIDGE_SYMBOLS) {
        expect(src.includes(sym), `${h.name}: retired bridge symbol ${sym} is still referenced`)
          .toBe(false)
      }
      expect(src.includes('/api/v1/routes'), `${h.name}: no private route POST from a host`).toBe(false)
    })
  }

  test('the helper is imported from the shared core (no per-host reimplementation)', () => {
    for (const h of HOSTS) {
      const src = fs.readFileSync(path.join(REPO, h.file), 'utf-8')
      expect(src.includes("from '@aimail/mail-core'"), `${h.name}: must use the shared core`).toBe(true)
      expect(src.includes('child_process'), `${h.name}: the notification must be shared, not re-spawned here`)
        .toBe(false)
    }
  })

  test('hosts with a teardown hook also notify --inbound-down', () => {
    for (const h of HOSTS.filter(x => x.down)) {
      const src = fs.readFileSync(path.join(REPO, h.file), 'utf-8')
      expect(src.includes("void notifyInboundForSystem('down')"), `${h.name}: shutdown must notify down`)
        .toBe(true)
    }
  })
})
