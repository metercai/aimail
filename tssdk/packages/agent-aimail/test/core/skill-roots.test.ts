/**
 * skill-roots (shared enumerator) — sandboxed unit tests.
 *
 * listSkillNames mirrors the discovery granularity both TS hosts use at
 * runtime: top-level subdir with SKILL.md (frontmatter `name` ?? dir name),
 * bare .md file (frontmatter `name` ?? file base name), dotted entries
 * ignored, symlinks followed, broken links skipped, missing root ⇒ [].
 * combineSkillNames dedupes by name with first-root-wins, sorted.
 */
import { beforeAll, beforeEach, afterAll, describe, it, expect } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { listSkillNames, combineSkillNames } from '../../src/core/skill-roots.js'

let home: string

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-skill-roots-'))
})

beforeEach(async () => {
  await fs.rm(home, { recursive: true, force: true })
  await fs.mkdir(home, { recursive: true })
})

afterAll(async () => {
  await fs.rm(home, { recursive: true, force: true })
})

async function mkSkill(base: string, name: string, fmName?: string): Promise<void> {
  const dir = path.join(base, name)
  await fs.mkdir(dir, { recursive: true })
  const body = fmName
    ? `---\nname: ${fmName}\ndescription: test\n---\nbody`
    : `---\ndescription: no name field\n---\nbody`
  await fs.writeFile(path.join(dir, 'SKILL.md'), body)
}

describe('listSkillNames', () => {
  it('missing root ⇒ []', async () => {
    expect(listSkillNames(path.join(home, 'nope'))).toEqual([])
  })

  it('empty root ⇒ []', async () => {
    await fs.mkdir(path.join(home, 'empty'))
    expect(listSkillNames(path.join(home, 'empty'))).toEqual([])
  })

  it('subdir with SKILL.md: frontmatter name wins over dir name', async () => {
    await mkSkill(home, 'dir-name', 'frontmatter-name')
    expect(listSkillNames(home)).toEqual(['frontmatter-name'])
  })

  it('subdir with SKILL.md lacking name: falls back to dir name', async () => {
    await mkSkill(home, 'fallback-dir')
    expect(listSkillNames(home)).toEqual(['fallback-dir'])
  })

  it('subdir WITHOUT SKILL.md is not a skill', async () => {
    await fs.mkdir(path.join(home, 'plain-dir'), { recursive: true })
    await fs.writeFile(path.join(home, 'plain-dir', 'notes.txt'), 'x')
    await mkSkill(home, 'real-skill')
    expect(listSkillNames(home)).toEqual(['real-skill'])
  })

  it('bare .md file is a skill (name ?? file base)', async () => {
    await fs.writeFile(path.join(home, 'loose.md'), '---\ndescription: x\n---\nbody')
    expect(listSkillNames(home)).toEqual(['loose'])
    await fs.rm(path.join(home, 'loose.md'))
    await fs.writeFile(path.join(home, 'named.md'), '---\nname: from-fm\ndescription: x\n---\n')
    expect(listSkillNames(home)).toEqual(['from-fm'])
  })

  it('dotted entries are ignored', async () => {
    await fs.mkdir(path.join(home, '.hidden'), { recursive: true })
    await fs.writeFile(path.join(home, '.hidden', 'SKILL.md'), '---\nname: h\ndescription: x\n---\n')
    await fs.writeFile(path.join(home, '.loose.md'), '---\nname: l\ndescription: x\n---\n')
    expect(listSkillNames(home)).toEqual([])
  })

  it('follows symlinks to skill dirs; skips broken links', async () => {
    const real = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-skill-link-'))
    try {
      await fs.mkdir(path.join(home, 'target', 'linked-skill'), { recursive: true })
      await fs.writeFile(path.join(home, 'target', 'linked-skill', 'SKILL.md'),
        '---\nname: linked\ndescription: x\n---\n')
      await fs.symlink(path.join(home, 'target', 'linked-skill'), path.join(home, 'link'))
      await fs.symlink(path.join(home, 'target', 'missing'), path.join(home, 'broken'))
      expect(listSkillNames(path.join(home, 'target'))).toEqual(['linked'])
      expect(listSkillNames(home)).toEqual(['linked'])
    } finally {
      await fs.rm(real, { recursive: true, force: true })
      await fs.rm(path.join(home, 'target'), { recursive: true, force: true })
    }
  })

  it('CRLF frontmatter parses', async () => {
    await fs.mkdir(path.join(home, 'crlf'), { recursive: true })
    await fs.writeFile(path.join(home, 'crlf', 'SKILL.md'), '---\r\nname: crlf-name\r\ndescription: x\r\n---\r\nbody')
    expect(listSkillNames(home)).toEqual(['crlf-name'])
  })

  it('result is deduped and sorted', async () => {
    await mkSkill(home, 'z-skill')
    await mkSkill(home, 'a-skill')
    await mkSkill(home, 'dup-dir', 'a-skill') // same name, different dir
    expect(listSkillNames(home)).toEqual(['a-skill', 'z-skill'])
  })
})

describe('combineSkillNames', () => {
  it('dedupes by name, first root wins, sorted', async () => {
    const r1 = path.join(home, 'r1')
    const r2 = path.join(home, 'r2')
    await fs.mkdir(r1, { recursive: true })
    await fs.mkdir(r2, { recursive: true })
    await mkSkill(r1, 'shared', 'shared')
    await mkSkill(r1, 'only-in-one', 'only-one')
    await mkSkill(r2, 'shared', 'shared')
    await mkSkill(r2, 'only-in-two', 'only-two')
    expect(combineSkillNames([r1, r2])).toEqual(['only-one', 'only-two', 'shared'])
    expect(combineSkillNames([r2, r1])).toEqual(['only-one', 'only-two', 'shared'])
  })

  it('empty/missing roots contribute nothing', async () => {
    const r1 = path.join(home, 'r1')
    await mkSkill(r1, 'solo')
    expect(combineSkillNames([path.join(home, 'missing'), r1])).toEqual(['solo'])
    expect(combineSkillNames([])).toEqual([])
  })
})
