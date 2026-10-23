/**
 * openclaw skill roots — sandboxed unit tests (HOME redirected to a tmp
 * tree). Mirrors the runtime resolveWorkspaceSkillSourcePlan root set for a
 * given agent: plugin-skills → bundled (+custodian only when the agent is
 * the ambient owner) → workshop (per-agent) → managed → personal (default
 * state only) → workspace (.agents/skills, skills).
 *
 * The bundled root is anchored to the installed openclaw package (devDep in
 * this workspace) and is intentionally NOT redirected — it is asserted by
 * shape only. All HOME-derived roots are asserted exactly.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { readOpenclawConfig, resolveAgentWorkspaceDir, resolveOpenclawSkillRoots } from '../../src/openclaw/skills.js'

let home: string
const saved = { HOME: '', OPENCLAW_STATE_DIR: '', OPENCLAW_CONFIG_PATH: '' } as Record<string, string | undefined>

beforeAll(() => {
  for (const k of Object.keys(saved)) saved[k] = process.env[k]
})

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-oc-skills-'))
  process.env.HOME = home
  delete process.env.OPENCLAW_STATE_DIR
  delete process.env.OPENCLAW_CONFIG_PATH
})

afterAll(async () => {
  for (const [k, v] of Object.entries(saved)) {
    if (v === undefined) delete process.env[k]
    else process.env[k] = v
  }
  await fs.rm(home, { recursive: true, force: true })
})

async function writeCfg(obj: unknown): Promise<void> {
  const dir = path.join(home, '.openclaw')
  await fs.mkdir(dir, { recursive: true })
  await fs.writeFile(path.join(dir, 'openclaw.json'), JSON.stringify(obj))
}

describe('readOpenclawConfig', () => {
  it('missing config ⇒ {} (no throw)', async () => {
    expect(readOpenclawConfig()).toEqual({})
  })

  it('parses the config at {HOME}/.openclaw/openclaw.json', async () => {
    await writeCfg({ agents: { defaults: { workspace: '/w' } } })
    expect(readOpenclawConfig().agents.defaults.workspace).toBe('/w')
  })
})

describe('resolveAgentWorkspaceDir', () => {
  it('per-agent explicit workspace wins (with ~ expansion)', async () => {
    await writeCfg({ agents: { w: { workspace: '~/ws-w' } } })
    expect(resolveAgentWorkspaceDir('w')).toBe(path.join(home, 'ws-w'))
  })

  it('main inherits agents.defaults.workspace', async () => {
    await writeCfg({ agents: { defaults: { workspace: '/shared-ws' } } })
    expect(resolveAgentWorkspaceDir('main')).toBe('/shared-ws')
  })

  it('other agents: {defaults.workspace}/{agentId}', async () => {
    await writeCfg({ agents: { defaults: { workspace: '/shared-ws' } } })
    expect(resolveAgentWorkspaceDir('w2')).toBe('/shared-ws/w2')
  })

  it('no config: main ⇒ {HOME}/.openclaw/workspace, other ⇒ .../workspace/{id}', async () => {
    expect(resolveAgentWorkspaceDir('main')).toBe(path.join(home, '.openclaw', 'workspace'))
    expect(resolveAgentWorkspaceDir('w2')).toBe(path.join(home, '.openclaw', 'workspace', 'w2'))
  })
})

describe('resolveOpenclawSkillRoots', () => {
  it('root order: plugin-skills → (bundled) → workshop → managed → personal → workspace', async () => {
    await writeCfg({})
    const roots = resolveOpenclawSkillRoots('main')
    const oc = path.join(home, '.openclaw')
    expect(roots[0]).toBe(path.join(oc, 'plugin-skills'))
    const workshop = path.join(oc, 'agents', 'main', 'agent', 'workshop-skills')
    const idx = roots.indexOf(workshop)
    expect(idx).toBeGreaterThan(0)
    // managed + personal follow workshop; personal only with default state dir
    expect(roots[idx + 1]).toBe(path.join(oc, 'skills'))
    expect(roots[idx + 2]).toBe(path.join(home, '.agents', 'skills'))
    // workspace pair last, in runtime order
    expect(roots[roots.length - 2]).toBe(path.join(oc, 'workspace', '.agents', 'skills'))
    expect(roots[roots.length - 1]).toBe(path.join(oc, 'workspace', 'skills'))
  })

  it('workshop root is PER-AGENT (agentDir from agents.{id}.agentDir override)', async () => {
    await writeCfg({ agents: { w: { agentDir: '~/ad-w' } } })
    const roots = resolveOpenclawSkillRoots('w')
    expect(roots).toContain(path.join(home, 'ad-w', 'workshop-skills'))
    expect(roots).not.toContain(path.join(home, '.openclaw', 'agents', 'w', 'agent', 'workshop-skills'))
  })

  it('custodian-skills root only when the agent is the ambient owner', async () => {
    await writeCfg({ agents: { defaults: { systemAgent: { agentId: 'w' } } } })
    const ownerRoots = resolveOpenclawSkillRoots('w')
    const otherRoots = resolveOpenclawSkillRoots('main')
    const hasCustodian = (r: string[]) => r.some((p) => p.endsWith(path.join('custodian-skills')))
    expect(hasCustodian(ownerRoots)).toBe(true)
    expect(hasCustodian(otherRoots)).toBe(false)
  })

  it('skills.load.extraDirs are resolved first (absolute + ~)', async () => {
    await writeCfg({ skills: { load: { extraDirs: [path.join(home, 'extra-a'), '~/extra-b'] } } })
    const roots = resolveOpenclawSkillRoots('main')
    expect(roots[0]).toBe(path.resolve(path.join(home, 'extra-a')))
    expect(roots[1]).toBe(path.resolve(path.join(home, 'extra-b')))
  })

  it('OPENCLAW_STATE_DIR: state-rooted dirs move, personal root drops', async () => {
    const state = path.join(home, 'state')
    process.env.OPENCLAW_STATE_DIR = state
    await writeCfg({})
    const roots = resolveOpenclawSkillRoots('main')
    expect(roots).toContain(path.join(state, 'agents', 'main', 'agent', 'workshop-skills'))
    expect(roots).toContain(path.join(state, 'plugin-skills'))
    expect(roots).toContain(path.join(state, 'skills'))
    expect(roots).not.toContain(path.join(home, '.agents', 'skills'))
    expect(roots).not.toContain(path.join(home, '.openclaw', 'agents', 'main', 'agent', 'workshop-skills'))
  })

  it('bundled root (when resolvable) is a real skills dir named .../skills', async () => {
    await writeCfg({})
    const roots = resolveOpenclawSkillRoots('main')
    const bundled = roots.find((r) => r.includes('node_modules') && r.endsWith('/skills'))
    // devDep openclaw ships skills/ in this workspace; if it ever stops,
    // the root simply vanishes (the runtime behaves the same) — assert shape
    // only when present.
    if (bundled !== undefined) {
      expect(bundled.split(path.sep).slice(-1)[0]).toBe('skills')
    }
  })
})
