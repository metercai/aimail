// P3-C (2026-10-17): dsh host layout data (preset) self-report unit test — 10-11 three-source adjudication
// "Host self-report/internal → SDK internal". Full chain verification: this test pins hostLayoutArgs preset value;
// runOpEntry's `{...args, ...extra}` merge semantics + opAssemble's preset→binding
// persistence is covered by existing mail-core tests (op-entry.test.ts / sdk-ops.test.ts:118-135).
// import pure module ./host-layout.ts (no main self-execution side effects), no mock.
import { describe, it, expect } from 'vitest'

import { hostLayoutArgs } from '../../src/dsh/host-layout.js'

describe('dsh hostLayoutArgs (P3-C: preset host self-report)', () => {
  it('reports preset "mail" — the dsh host definition-layer default', () => {
    expect(hostLayoutArgs().preset).toBe('mail')
  })

  it('reports the skill exposure layout (patch under DSH_HOME, ids list)', () => {
    const a = hostLayoutArgs()
    expect(a.skill_expose_patch).toContain('cordis.patch.yml')
    expect(a.skill_expose_ids).toBe('skill-filesystem tool-skill')
  })

  it('honors DSH_HOME / DSH_PROFILE env for the patch path', () => {
    process.env.DSH_HOME = '/tmp/dsh-test'
    process.env.DSH_PROFILE = 'web'
    expect(hostLayoutArgs().skill_expose_patch).toBe(
      '/tmp/dsh-test/profiles/web/cordis.patch.yml',
    )
    delete process.env.DSH_HOME
    delete process.env.DSH_PROFILE
  })
})
