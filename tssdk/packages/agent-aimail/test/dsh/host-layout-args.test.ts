// P3-C(2026-10-17): dsh 宿主布局数据(preset)自报单测 —— 10-11 三来源裁决
// 「宿主自报/内部 → SDK 内部」。全链验证: 本测试钉 hostLayoutArgs 的 preset 值;
// runOpEntry 的 `{...args, ...extra}` merge 语义 + opAssemble 的 preset→binding
// 落盘由 mail-core 既有测试覆盖(op-entry.test.ts / sdk-ops.test.ts:118-135)。
// import 纯模块 ./host-layout.ts(无 main 自执行副作用), 不 mock。
import { describe, it, expect } from 'vitest'

import { hostLayoutArgs } from '../../src/dsh/host-layout.js'

describe('dsh hostLayoutArgs (P3-C: preset 宿主自报)', () => {
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
