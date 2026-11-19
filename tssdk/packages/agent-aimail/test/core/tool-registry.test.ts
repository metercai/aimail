/**
 * AGENT_TOOL_DEFS contract tests — SSOT parity (2026-11-03).
 *
 * Single source of truth: `pysdk/tool-spec.json` (repo root). The TS SDK
 * projects it in two steps at build time:
 *   1. scripts/gen-tool-spec.mjs codegens `src/generated/tool-spec.ts`
 *   2. tool-registry.ts flattens it into the adapter-facing AGENT_TOOL_DEFS shape
 * This test pins BOTH steps to the JSON file:
 *   - the codegen artifact must equal the spec (names/order/text/params)
 *   - the flattened AGENT_TOOL_DEFS must equal the spec (per-param mapping)
 * Drift anywhere in the chain turns L0 red. The Python side pins its two
 * projections (MCP TOOLS, hermes schemas) against the same file in
 * tests/sdk/test_tool_spec_sot.py — three projections, one text.
 */
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { AGENT_TOOL_DEFS, MAIL_TOOLS, MAIL_BOARD, MAIL_ADMIN } from '../../src/core/tool-registry.js'
import { MAIL_TOOL_NAMES, MAIL_BOARD_NAMES, MAIL_ADMIN_NAMES } from '../../src/core/agent-tools.js'
import type { MailToolDef, MailToolParam } from '../../src/core/tool-registry.js'
import { TOOL_SPEC } from '../../src/generated/tool-spec.js'

/** Repo root: tssdk/packages/agent-aimail/test/core → 5 up. In a standalone
 * npm install there is no sibling pysdk — the spec pin then degrades to
 * codegen-vs-registry only (still catches TS-side drift). */
const SPEC_JSON = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', '..', '..', '..', '..', 'pysdk', 'tool-spec.json',
)

interface SpecParam {
  type: string
  items?: { type: string }
  enum?: string[]
  default?: string | number | boolean
  description?: string
}
interface SpecEntry {
  name: string
  description: string
  parameters: { type: 'object'; properties: Record<string, SpecParam>; required?: string[] }
}

function loadSpec(): SpecEntry[] | undefined {
  if (!existsSync(SPEC_JSON)) return undefined
  return JSON.parse(readFileSync(SPEC_JSON, 'utf-8')) as SpecEntry[]
}

const spec = loadSpec()

const EXPECTED_NAMES = [
  'send_mail',
  'manage_contacts',
  'contact_profile',
  'set_contact_profile',
  'email_summary',
  'set_email_summary',
  'search_mail',
  'board_status',
  'board_task_list',
  'board_task_show',
  'board_heartbeat',
  'board_members',
  'board_roles',
  'aimail_address',
  'role_calibrator',
] as const

describe('AGENT_TOOL_DEFS structure', () => {
  it('registers exactly the 15 bare tool names, in order', () => {
    expect(AGENT_TOOL_DEFS.map(t => t.name)).toEqual([...EXPECTED_NAMES])
  })

  it('every tool has a non-empty description; tools with parameters have ≥1 typed parameter', () => {
    for (const t of AGENT_TOOL_DEFS) {
      expect(t.description.length, `${t.name} description`).toBeGreaterThan(0)
      for (const [k, p] of Object.entries(t.parameters)) {
        expect(p.type, `${t.name}.${k} type`).toMatch(/^(string|array|integer|number|boolean)$/)
        if (p.type === 'array') expect(p.items, `${t.name}.${k} items`).toEqual({ type: 'string' })
      }
    }
  })

  it('every handler is a function bound to the tool', () => {
    for (const t of AGENT_TOOL_DEFS) {
      expect(typeof t.handler, `${t.name} handler`).toBe('function')
    }
  })
})

describe('codegen artifact ↔ spec JSON', () => {
  it('TOOL_SPEC equals the spec file, verbatim, in order', () => {
    if (!spec) {
      // standalone install: still pin the shape
      expect(TOOL_SPEC.map(t => t.name)).toEqual([...EXPECTED_NAMES])
      return
    }
    expect(TOOL_SPEC).toEqual(spec)
  })
})

describe('grouped tool definitions', () => {
  it('MAIL_TOOLS is the 7 mail tools', () => {
    expect(MAIL_TOOLS.map(t => t.name)).toEqual([...MAIL_TOOL_NAMES])
    expect(MAIL_TOOLS).toHaveLength(7)
  })

  it('MAIL_BOARD is the 7 board/agent tools', () => {
    expect(MAIL_BOARD.map(t => t.name)).toEqual([...MAIL_BOARD_NAMES])
    expect(MAIL_BOARD).toHaveLength(7)
  })

  it('MAIL_ADMIN is the address binding tool; system onboarding is DSH-only', () => {
    expect(MAIL_ADMIN.map(t => t.name)).toEqual(['aimail_address'])
    expect(MAIL_ADMIN_NAMES).toEqual(['aimail_address'])
  })

  it('the three groups cover the full spec-backed set', () => {
    const grouped = [...MAIL_TOOLS, ...MAIL_BOARD, ...MAIL_ADMIN].map(t => t.name).sort()
    expect(grouped).toEqual(AGENT_TOOL_DEFS.map(t => t.name).sort())
  })
})

describe('AGENT_TOOL_DEFS ↔ spec JSON (flat projection)', () => {
  if (!spec) {
    it.skip('spec JSON available (aimail repo checkout)', () => {})
    return
  }

  it('has the same 15 tools with identical names and descriptions', () => {
    expect(AGENT_TOOL_DEFS.map(t => t.name)).toEqual(spec.map(t => t.name))
    spec.forEach((st, i) => {
      const tt = AGENT_TOOL_DEFS[i] as MailToolDef
      expect(tt.description, `${st.name} description`).toBe(st.description)
    })
  })

  it('parameter types/enums/defaults/descriptions/required match the spec', () => {
    for (const st of spec) {
      const tt = AGENT_TOOL_DEFS.find(t => t.name === st.name)!
      const props = st.parameters.properties
      const required = new Set(st.parameters.required ?? [])
      // exact key set, no invented params, no dropped params
      expect(Object.keys(tt.parameters).sort()).toEqual(Object.keys(props).sort())
      for (const [k, sp] of Object.entries(props)) {
        const tp: MailToolParam = tt.parameters[k]
        expect(tp.type, `${st.name}.${k} type`).toBe(sp.type)
        if (sp.items) expect(tp.items, `${st.name}.${k} items`).toEqual({ type: sp.items.type })
        if (sp.enum !== undefined) expect(tp.enum, `${st.name}.${k} enum`).toEqual([...sp.enum])
        if (sp.default !== undefined) expect(tp.default, `${st.name}.${k} default`).toBe(sp.default)
        if (sp.description !== undefined) {
          expect(tp.description, `${st.name}.${k} description`).toBe(sp.description)
        }
        expect(!!tp.required, `${st.name}.${k} required`).toBe(required.has(k))
      }
    }
  })
})
