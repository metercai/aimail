/**
 * dsh skill roots — sandboxed unit tests (HOME + DSH_* env redirected to a
 * tmp tree). Mirrors dsh's FileSystemSkillProvider root set + rank order:
 * project ({projectRoot}/.dsh/skills, .agents/skills) → user
 * ({DSH_HOME}/skills, {DSH_AGENTS_HOME|~/.agents}/skills) → bundled
 * ($DSH_BUNDLED_SKILL_DIR).
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { findProjectRoot, resolveDshHome, resolveDshSkillRoots } from '../../src/dsh/skills.js'

let home: string
const saved = { DSH_HOME: '', DSH_AGENTS_HOME: '', DSH_BUNDLED_SKILL_DIR: '' } as Record<string, string | undefined>

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-dsh-skills-'))
  for (const k of Object.keys(saved)) saved[k] = process.env[k]
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
  process.env.DSH_HOME = path.join(home, '.dsh')
  process.env.DSH_AGENTS_HOME = path.join(home, '.agents')
  delete process.env.DSH_BUNDLED_SKILL_DIR
})

afterAll(async () => {
  for (const [k, v] of Object.entries(saved)) {
    if (v === undefined) delete process.env[k]
    else process.env[k] = v
  }
  await fs.rm(home, { recursive: true, force: true })
})

async function mkSkill(base: string, name: string): Promise<void> {
  const dir = path.join(base, name)
  await fs.mkdir(dir, { recursive: true })
  await fs.writeFile(path.join(dir, 'SKILL.md'), `---\nname: ${name}\ndescription: t\n---\n`)
}

describe('findProjectRoot', () => {
  it('returns the nearest ancestor containing .git (cwd inside)', async () => {
    const proj = path.join(home, 'proj')
    await fs.mkdir(path.join(proj, '.git'), { recursive: true })
    await fs.mkdir(path.join(proj, 'deep', 'nest'), { recursive: true })
    expect(findProjectRoot(path.join(proj, 'deep', 'nest'))).toBe(path.resolve(proj))
  })

  it('no .git anywhere ⇒ the resolved cwd itself', async () => {
    const dir = path.join(home, 'plain', 'sub')
    await fs.mkdir(dir, { recursive: true })
    expect(findProjectRoot(dir)).toBe(path.resolve(dir))
  })
})

describe('resolveDshHome', () => {
  it('DSH_HOME env wins (~ expanded, resolved)', async () => {
    process.env.DSH_HOME = '~/.dsh'
    expect(resolveDshHome()).toBe(path.resolve(path.join(os.homedir(), '.dsh')))
    process.env.DSH_HOME = path.join(home, 'custom')
    expect(resolveDshHome()).toBe(path.resolve(path.join(home, 'custom')))
  })
})

describe('resolveDshSkillRoots', () => {
  it('project roots derive from the .git ancestor of cwd', async () => {
    const proj = path.join(home, 'proj')
    await fs.mkdir(path.join(proj, '.git'), { recursive: true })
    const roots = resolveDshSkillRoots(path.join(proj, 'x', 'y'))
    expect(roots[0]).toBe(path.join(proj, '.dsh', 'skills'))
    expect(roots[1]).toBe(path.join(proj, '.agents', 'skills'))
  })

  it('user roots: {DSH_HOME}/skills + {DSH_AGENTS_HOME}/skills, in rank order', async () => {
    const roots = resolveDshSkillRoots(home)
    expect(roots.slice(2)).toEqual([
      path.join(home, '.dsh', 'skills'),
      path.join(home, '.agents', 'skills'),
    ])
  })

  it('bundled root only when DSH_BUNDLED_SKILL_DIR set; trailing order preserved', async () => {
    expect(resolveDshSkillRoots(home)).toHaveLength(4)
    process.env.DSH_BUNDLED_SKILL_DIR = path.join(home, 'bundled')
    const roots = resolveDshSkillRoots(home)
    expect(roots).toHaveLength(5)
    expect(roots[4]).toBe(path.resolve(path.join(home, 'bundled')))
  })

  it('populated roots enumerate to the available-skill list', async () => {
    // (imported lazily to keep this file focused on the resolver)
    const { combineSkillNames } = await import('../../src/core/skill-roots.js')
    await fs.mkdir(path.join(home, '.dsh', 'skills'), { recursive: true })
    await fs.mkdir(path.join(home, '.agents', 'skills'), { recursive: true })
    await mkSkill(path.join(home, '.dsh', 'skills'), 'user-dsh-skill')
    await mkSkill(path.join(home, '.agents', 'skills'), 'user-agents-skill')
    await mkSkill(path.join(home, '.dsh', 'skills'), 'user-agents-skill') // dup across roots
    expect(combineSkillNames(resolveDshSkillRoots(home))).toEqual([
      'user-agents-skill', 'user-dsh-skill',
    ])
  })
})
