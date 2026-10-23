/**
 * Shared skill-root enumeration for the host adapters' skillsProvider.
 *
 * Mirrors the discovery granularity both TS hosts use at runtime:
 *  - dsh  FileSystemSkillProvider.discoverRoot (skill-filesystem): a skill is a
 *    TOP-LEVEL entry of a root — a directory containing SKILL.md, or a bare
 *    .md file — and its name comes from the SKILL.md frontmatter.
 *  - openclaw local loader / bundled-dir: same shape (subdir with SKILL.md,
 *    or a .md file); frontmatter `name:` preferred over the entry basename.
 *
 * The list a host reports is "the skills AVAILABLE to that agent's
 * configuration" (owner ruling 2026-10-23): it is NOT filtered by whether a
 * root is per-agent or shared — the adapter decides WHICH roots belong to the
 * agent (see ../openclaw/skills.ts, ../dsh/skills.ts); this helper only turns
 * one root directory into names.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'

/**
 * Frontmatter `name:` of a SKILL.md / skill .md file, or undefined when the
 * file is missing, has no frontmatter block, or the block has no name.
 * Only the single-line scalar form is read (the form every shipped SKILL.md
 * uses); multi-line/blocked values fall back to the caller's basename.
 */
function frontmatterName(p: string): string | undefined {
  let raw: string
  try {
    raw = fs.readFileSync(p, 'utf-8')
  } catch {
    return undefined
  }
  const m = raw.match(/^\uFEFF?---\r?\n([\s\S]*?)\r?\n---[ \t]*(?:\r?\n|$)/)
  if (!m) return undefined
  const line = m[1].split(/\r?\n/).find((l) => l.startsWith('name:'))
  if (!line) return undefined
  const v = line.slice('name:'.length).trim().replace(/^["']|["']$/g, '')
  return v || undefined
}

/**
 * Enumerate the skill names available under one root directory.
 *
 * A root contains: subdirectories with a SKILL.md (name = frontmatter `name`
 * ?? directory name) and bare .md files (name = frontmatter `name` ?? file
 * name without extension). Dotted entries are not skills. Missing root ⇒ [].
 * Result is deduped and sorted for a stable, comparable list.
 */
export function listSkillNames(dir: string): string[] {
  let entries: fs.Dirent[]
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return []
  }
  const names: string[] = []
  for (const e of entries) {
    if (e.name.startsWith('.')) continue
    const full = path.join(dir, e.name)
    // statSync follows symlinks — plugin/materialized skill dirs are
    // symlinked in the wild (e.g. openclaw plugin-skills); broken links skip.
    let st: fs.Stats
    try {
      st = fs.statSync(full)
    } catch {
      continue
    }
    if (st.isDirectory()) {
      const skillFile = path.join(full, 'SKILL.md')
      if (!fs.existsSync(skillFile)) continue
      names.push(frontmatterName(skillFile) ?? e.name)
    } else if (st.isFile() && e.name.endsWith('.md')) {
      names.push(frontmatterName(full) ?? e.name.replace(/\.md$/, ''))
    }
  }
  return [...new Set(names)].sort()
}

/**
 * Combine per-root name lists into the agent's available-skill list:
 * dedupe by name (first root wins — adapters pass roots in host priority
 * order, mirroring the host's rank system), sorted.
 */
export function combineSkillNames(roots: string[]): string[] {
  const seen = new Set<string>()
  const out: string[] = []
  for (const r of roots) {
    for (const n of listSkillNames(r)) {
      if (!seen.has(n)) {
        seen.add(n)
        out.push(n)
      }
    }
  }
  return out.sort()
}
