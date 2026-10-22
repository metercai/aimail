/**
 * skill-exposure.ts — shared node-host assembly step: make the host actually EXPOSE the
 * skill/tool layer it is given.
 *
 * Why this is shared: node hosts assemble themselves through the same three-op entry
 * (register-cli.js -> @aimail/mail-core opAssemble). Some hosts expose skills out of the
 * box; the ones that ship the exposure features switched off (deepseek-harness writes
 * `skill-filesystem` / `tool-skill` as `disabled: true` in its profile patch layer) end up
 * with a correct skills directory that never reaches the model's tool/skill list. That is
 * an assembly concern, so it lives here and the platform passes its own patch file + ids as
 * data — no platform-specific code path.
 *
 * Idempotent: rows already enabled are left alone, everything else in the file is preserved.
 * Fails loudly: after writing, the ids must be present AND enabled, else it throws.
 */
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { dirname } from 'node:path'

export interface SkillExposureResult {
  patch_file: string
  ids: string[]
  changed: boolean
}

const ID_RE = /^-\s*id\s*:\s*(.+?)\s*$/
const DISABLED_RE = /^\s*disabled\s*:/
const EMPTY_FLOW_SEQ_RE = /^\s*\[\]\s*$/

function normalize(lines: string[], ids: string[]): string[] {
  const want = new Set(ids)
  const seen = new Set<string>()
  const out: string[] = []

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i]!
    // The host materializes a comment + a bare `[]` (empty flow sequence) template. Appending
    // a top-level `- id:` right after it is invalid YAML and makes the host fail to parse the
    // whole overlay, so that line is dropped.
    if (EMPTY_FLOW_SEQ_RE.test(line)) continue

    const idMatch = ID_RE.exec(line)
    if (idMatch) {
      const id = idMatch[1]!
      out.push(line)
      if (want.has(id)) {
        seen.add(id)
        const next = lines[i + 1]
        if (next !== undefined && DISABLED_RE.test(next)) {
          out.push('  disabled: false')
          i++ // consume the original disabled line
        } else {
          out.push('  disabled: false')
        }
      }
      continue
    }
    out.push(line)
  }

  for (const id of ids) {
    if (seen.has(id)) continue
    out.push(`- id: ${id}`, '  disabled: false')
  }
  return out
}

function assertEnabled(lines: string[], ids: string[]): void {
  for (const id of ids) {
    const idx = lines.findIndex((l) => ID_RE.exec(l)?.[1] === id)
    if (idx < 0) throw new Error(`skill exposure: row '${id}' missing after assembly`)
    const next = lines[idx + 1]
    if (next === undefined || !DISABLED_RE.test(next) || !/false/.test(next)) {
      throw new Error(`skill exposure: row '${id}' is not enabled after assembly`)
    }
  }
}

export function ensureSkillToolExposure(opts: {
  patchFile: string
  ids: string[]
}): SkillExposureResult {
  const { patchFile, ids } = opts
  if (!patchFile) throw new Error('skill exposure: patch file required')
  if (ids.length === 0) throw new Error('skill exposure: no ids given')

  const before = existsSync(patchFile) ? readFileSync(patchFile, 'utf8').split('\n') : []
  if (before.length > 0 && before[before.length - 1] === '') before.pop()
  const after = normalize(before, ids)
  assertEnabled(after, ids)

  const same = before.length === after.length && before.every((l, i) => l === after[i])
  if (same) return { patch_file: patchFile, ids, changed: false }

  mkdirSync(dirname(patchFile), { recursive: true })
  writeFileSync(patchFile, after.join('\n') + '\n')
  return { patch_file: patchFile, ids, changed: true }
}
