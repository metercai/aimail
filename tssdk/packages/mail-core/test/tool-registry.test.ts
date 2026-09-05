/**
 * MAIL_TOOLS contract tests:
 *   - structural: 13 tools, exact names/order, non-empty semantic text
 *   - parity (best-effort): names + descriptions + parameter text must match
 *     the Python TOOLS registry in aimail/tools/amail_mcp_server.py
 *     (the upstream contract reference). Skipped when the sibling repo or
 *     python3 is unavailable (e.g. CI without the checkout).
 */
import { describe, it, expect } from 'vitest'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { MAIL_TOOLS } from '../src/tool-registry.js'
import type { MailToolDef } from '../src/tool-registry.js'

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
  'set_public_whoami',
] as const

/** Sibling-repo layout: dsh-aimail/ and aimail/ share a parent dir.
 * Python registry moved to pysdk/ in the 2026-09 rename (legacy
 * aimail/tools/ no longer exists) — parity stays live against pysdk. */
const PY_REGISTRY = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '..', '..', '..', '..', 'aimail', 'pysdk', 'amail_mcp_server.py',
)

interface PyParam { type?: string; enum?: string[]; description?: string }
interface PyTool { name: string; description: string; inputSchema: { properties: Record<string, PyParam>; required?: string[] } }

function loadPythonTools(): PyTool[] | undefined {
  if (!existsSync(PY_REGISTRY)) return undefined
  // Exec ONLY the SCHEMA_STR + TOOLS assignments (literal expressions —
  // no calls), so importing the server module side effects never run.
  const script = `
import ast, json
src = open(${JSON.stringify(PY_REGISTRY)}).read()
ns = {}
for node in ast.parse(src).body:
    if isinstance(node, ast.Assign):
        for t in node.targets:
            if isinstance(t, ast.Name) and t.id in ('SCHEMA_STR', 'TOOLS'):
                ns[t.id] = eval(compile(ast.Expression(node.value), '<x>', 'eval'), {'__builtins__': {}}, ns)
print(json.dumps(ns['TOOLS']))
`
  try {
    const out = execFileSync('python3', ['-c', script], { encoding: 'utf-8', timeout: 10_000 })
    return JSON.parse(out.trim())
  } catch {
    return undefined
  }
}

describe('MAIL_TOOLS structure', () => {
  it('registers exactly the 13 bare tool names, in order', () => {
    expect(MAIL_TOOLS.map(t => t.name)).toEqual([...EXPECTED_NAMES])
  })

  it('every tool has non-empty description and ≥1 parameter', () => {
    for (const t of MAIL_TOOLS) {
      expect(t.description.length, `${t.name} description`).toBeGreaterThan(0)
      expect(Object.keys(t.parameters).length, `${t.name} parameters`).toBeGreaterThanOrEqual(1)
      for (const [k, p] of Object.entries(t.parameters)) {
        expect(p.type, `${t.name}.${k} type`).toMatch(/^(string|array)$/)
        if (p.type === 'array') expect(p.items, `${t.name}.${k} items`).toEqual({ type: 'string' })
      }
    }
  })

  it('every handler is a function bound to the tool', () => {
    for (const t of MAIL_TOOLS) {
      expect(typeof t.handler, `${t.name} handler`).toBe('function')
    }
  })
})

describe('MAIL_TOOLS ↔ Python registry parity', () => {
  const py = loadPythonTools()
  if (!py) {
    it.skip('python registry available (aimail repo + python3)', () => {})
    return
  }

  it('has the same 13 tools with identical names and descriptions', () => {
    expect(py.map(t => t.name)).toEqual([...EXPECTED_NAMES])
    py.forEach((pt, i) => {
      const tt = MAIL_TOOLS[i]
      expect(tt.name).toBe(pt.name)
      expect(tt.description, `${pt.name} description`).toBe(pt.description)
    })
  })

  it('parameter types/enums/descriptions match; required flags match', () => {
    for (const pt of py) {
      const tt = MAIL_TOOLS.find(t => t.name === pt.name)!
      const pyProps = pt.inputSchema.properties
      const tsParams = tt.parameters
      expect(Object.keys(tsParams).sort()).toEqual(Object.keys(pyProps).sort())
      for (const [k, pp] of Object.entries(pyProps)) {
        const tp = tsParams[k]
        expect(tp?.type, `${pt.name}.${k} type`).toBe(pp.type)
        if (pp.enum) expect(tp?.enum, `${pt.name}.${k} enum`).toEqual(pp.enum)
        expect(tp?.description, `${pt.name}.${k} description`).toBe(pp.description)
        expect(!!tp?.required, `${pt.name}.${k} required`)
          .toBe((pt.inputSchema.required ?? []).includes(k))
      }
    }
  })
})
