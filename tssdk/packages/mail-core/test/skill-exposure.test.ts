import { existsSync, mkdtempSync, readFileSync, writeFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { describe, expect, it } from 'vitest'
import { ensureSkillToolExposure } from '../src/skill-exposure.js'

const IDS = ['skill-filesystem', 'tool-skill']

function tmpPatch (content: string): string {
  const d = mkdtempSync(join(tmpdir(), 'expose-'))
  const p = join(d, 'cordis.patch.yml')
  writeFileSync(p, content)
  return p
}

describe('ensureSkillToolExposure (shared node-host assembly step)', () => {
  it('drops the host template `[]`, enables existing rows and appends missing ones', () => {
    // Shape the host itself materializes: a comment + a bare empty flow sequence, plus the
    // two rows shipped disabled.
    const p = tmpPatch([
      '# overlay template',
      '[]',
      '- id: unrelated',
      '  disabled: true',
      '- id: skill-filesystem',
      '  disabled: true',
      '',
    ].join('\n'))
    const r = ensureSkillToolExposure({ patchFile: p, ids: IDS })
    expect(r.changed).toBe(true)
    const out = readFileSync(p, 'utf8').split('\n')
    expect(out.some((l) => /^\s*\[\]\s*$/.test(l))).toBe(false)   // template line removed
    expect(out).toContain('- id: skill-filesystem')
    expect(out).toContain('- id: tool-skill')
    for (const id of IDS) {
      const i = out.findIndex((l) => l === `- id: ${id}`)
      expect(out[i + 1]).toBe('  disabled: false')
    }
    // a row we do not own keeps its own value
    const u = out.findIndex((l) => l === '- id: unrelated')
    expect(out[u + 1]).toBe('  disabled: true')
  })

  it('is idempotent', () => {
    const p = tmpPatch('')
    expect(ensureSkillToolExposure({ patchFile: p, ids: IDS }).changed).toBe(true)
    const second = ensureSkillToolExposure({ patchFile: p, ids: IDS })
    expect(second.changed).toBe(false)
  })

  it('creates the file when the host has none yet', () => {
    const d = mkdtempSync(join(tmpdir(), 'expose-'))
    const p = join(d, 'profiles', 'web', 'cordis.patch.yml')
    const r = ensureSkillToolExposure({ patchFile: p, ids: IDS })
    expect(r.changed).toBe(true)
    expect(existsSync(p)).toBe(true)
    const body = readFileSync(p, 'utf8')
    expect(body).toContain('- id: skill-filesystem')
    expect(body).toContain('- id: tool-skill')
  })

  it('refuses an empty id list instead of writing nothing silently', () => {
    expect(() => ensureSkillToolExposure({ patchFile: tmpPatch(''), ids: [] })).toThrow()
  })
})
