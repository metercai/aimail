// dsh-aimail regression guard (filled in during the 2026-09-21 audit: this package previously had zero tests).
//
// drive apply() with a minimal-stub cordis ctx, asserting the registered tool set == the full MAIL_TOOLS set
// (the adapter only does "translate schema + bind identity"; it must not miss-register or over-register).
//
// Note: The host package @deepseek-ai/dsh-tools transitively depends on the uninstalled @deepseek-ai/dsh-scope,
// cannot be truly loaded in the workspace ⇒ use vi.mock to isolate (only replace defineTool one runtime symbol,
// all other imports are type-only, not affecting the logic under test).
import { describe, it, expect, vi } from 'vitest'

vi.mock('@deepseek-ai/dsh-tools', () => ({
  defineTool: (spec: unknown) => spec,
}))

import { MAIL_TOOLS } from '../../src/core/index.js'
import { apply } from '../../src/dsh/tools.js'

describe('dsh-aimail tool registration', () => {
  it('apply() registers every MAIL_TOOLS entry exactly once', () => {
    const registered: string[] = []
    const ctx = {
      get: (key: string) =>
        key === 'mail'
          ? { resolveCtx: async () => ({}) }        // MailService stub
          : undefined,                              // agentDefaultModel missing → go to default branch
      tools: { register: (tool: { name: string }) => registered.push(tool.name) },
    }
    apply(ctx as never)
    expect(registered.length).toBe(MAIL_TOOLS.length)
    expect(registered.length).toBe(15)
    expect([...registered].sort()).toEqual(MAIL_TOOLS.map(t => t.name).sort())
  })

  it('apply() fails loudly when the mail service is not mounted', () => {
    const ctx = { get: () => undefined, tools: { register: () => {} } }
    expect(() => apply(ctx as never)).toThrow(/mail service/)
  })
})
