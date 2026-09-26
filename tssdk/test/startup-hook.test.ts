/**
 * Startup hooks: every host upserts its bridge routes once its INBOUND IS UP
 * (owner ruling 2026-09-27).
 *
 * Why this file exists: the restore contract has two halves and only one of them
 * is assertable in the CLI docker layer (that container does not run the host
 * daemon). The route LOGIC (three states, targets, pruning, idempotency) is
 * asserted behaviourally in packages/mail-core/test/bridge-route.test.ts against
 * a real listener + a fake bridge; the CLI half (hermes/deer-flow, which have no
 * in-process hook) is asserted in aimail tests/test_bridge_route_side.py and in
 * tests/cli/docker/cli-in-host.sh.
 *
 * What is asserted here is the WIRING, at source level, for all three hosts:
 *   - the startup path calls the shared helper, and
 *   - the call comes AFTER the inbound endpoint is registered, and
 *   - the outcome is reported (never swallowed).
 * Order is the substance: pushing the route before the listener is up is exactly
 * what got the route pruned in production on 2026-09-26 (created 15:53:17,
 * removed 15:56:07 by the bridge's own health check).
 *
 * COVERAGE GAP (registered, not hidden): the dsh hook could not be exercised
 * end-to-end here either — importing packages/dsh-aimail/src/inbound.ts pulls
 * '@deepseek-ai/dsh-llm', whose peer dep '@deepseek-ai/dsh-timeout' is not
 * installed in this workspace (module resolution fails before any mock applies).
 * dsh's hook therefore runs for real only in the L2 host container with the
 * packaged plugin; this file keeps its wiring pinned until that is assertable.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { describe, expect, test } from 'vitest'

const REPO = path.resolve(__dirname, '..')

const HOSTS = [
  {
    name: 'dsh',
    file: 'packages/dsh-aimail/src/inbound.ts',
    live: 'server.listen(port, host',
    liveLabel: 'the inbound listener',
  },
  {
    name: 'openclaw',
    file: 'packages/openclaw-aimail/src/index.ts',
    live: 'api.registerHttpRoute({',
    liveLabel: 'the in-gateway HTTP route',
  },
  {
    name: 'pi',
    file: 'packages/pi-aimail/src/index.ts',
    live: "server.listen(port, '127.0.0.1'",
    liveLabel: 'the inbound listener',
  },
]

describe('host startup hooks (route side)', () => {
  for (const h of HOSTS) {
    test(`${h.name}: upserts the route after ${h.liveLabel} is live and reports the outcome`, () => {
      const src = fs.readFileSync(path.join(REPO, h.file), 'utf-8')
      const liveAt = src.indexOf(h.live)
      const hookAt = src.indexOf('ensureBridgeRoutesForSystem(')
      expect(liveAt, `${h.name}: inbound registration not found (${h.live})`).toBeGreaterThan(-1)
      expect(hookAt, `${h.name}: startup route upsert not wired`).toBeGreaterThan(-1)
      expect(hookAt, `${h.name}: the route upsert must come after the inbound endpoint is live`)
        .toBeGreaterThan(liveAt)
      expect(src.includes('formatBridgeRouteLine('), `${h.name}: outcome must be reported, not swallowed`)
        .toBe(true)
      expect(src.includes('isBridgeRouteWarning('), `${h.name}: a failure must be a warning line`)
        .toBe(true)
    })
  }

  test('the helper is imported from the shared core (no per-host reimplementation)', () => {
    for (const h of HOSTS) {
      const src = fs.readFileSync(path.join(REPO, h.file), 'utf-8')
      expect(src.includes("from '@aimail/mail-core'"), `${h.name}: must use the shared core`).toBe(true)
      expect(src.includes('registerBridgeRoute({'), `${h.name}: no private route POST`).toBe(false)
    }
  })
})
