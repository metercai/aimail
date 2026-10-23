/**
 * dsh skill roots — mirrors dsh's own FileSystemSkillProvider (dsh-skill-
 * filesystem) root set, so the skills this profile's configuration can load
 * are exactly the roots the runtime would discover. Shared across profiles
 * on purpose (owner ruling 2026-10-23: report every skill the agent's
 * configuration can use, shared or not); the profile unit is the process
 * (DSH_PROFILE), which does not change the roots the provider scans.
 *
 * Root order here = provider rank order (project > user > bundled); the
 * adapter dedupes by name with first-root-wins (combineSkillNames).
 */
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

/** Mirrors @deepseek-ai/dsh-home-paths resolveDshHome: DSH_HOME (env or
 * configured), else ~/.dsh, `~`-expanded and resolved. */
export function resolveDshHome(): string {
  const env = process.env.DSH_HOME?.trim()
  const selected = env || path.join(os.homedir(), '.dsh')
  return path.resolve(selected.replace(/^~(?=\/|$)/, os.homedir()))
}

/** Mirrors skill-filesystem findProjectRoot: nearest ancestor (self
 * included) containing a .git directory; stops at the filesystem root. */
export function findProjectRoot(cwd: string): string {
  let current = path.resolve(cwd)
  for (;;) {
    try {
      if (fs.statSync(path.join(current, '.git')).isDirectory()) return current
    } catch { /* keep walking */ }
    const parent = path.dirname(current)
    if (parent === current) return path.resolve(cwd)
    current = parent
  }
}

/**
 * The skill roots this dsh process's configuration can load, in provider
 * rank order:
 *   project {projectRoot}/.dsh/skills, {projectRoot}/.agents/skills
 *   user    {DSH_HOME}/skills, {DSH_AGENTS_HOME|~/.agents}/skills
 *   bundled $DSH_BUNDLED_SKILL_DIR (set by the dsh runtime when the
 *   provider is constructed with the app's bundled root).
 */
export function resolveDshSkillRoots(cwd: string = process.cwd()): string[] {
  const roots: string[] = []
  const projectRoot = findProjectRoot(cwd)
  roots.push(path.join(projectRoot, '.dsh', 'skills'))
  roots.push(path.join(projectRoot, '.agents', 'skills'))
  const dshHome = resolveDshHome()
  const agentsHome = process.env.DSH_AGENTS_HOME?.trim()
    ? path.resolve(process.env.DSH_AGENTS_HOME.trim().replace(/^~(?=\/|$)/, os.homedir()))
    : path.join(os.homedir(), '.agents')
  roots.push(path.join(dshHome, 'skills'))
  roots.push(path.join(agentsHome, 'skills'))
  const bundled = process.env.DSH_BUNDLED_SKILL_DIR?.trim()
  if (bundled) roots.push(path.resolve(bundled))
  return roots
}
