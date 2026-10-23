/**
 * openclaw skill roots — mirrors openclaw's own runtime resolution
 * (`resolveWorkspaceSkillSourcePlan` in the installed dist), so the skills
 * this agent's configuration can load are exactly the roots the runtime
 * would discover. The agent id selects the per-agent roots (workshop dir,
 * custodian gate); shared roots (managed/bundled/plugin/personal/extra/
 * workspace) are part of the list on purpose — owner ruling 2026-10-23:
 * report every skill the agent's configuration can use, shared or not.
 *
 * Root order here = runtime discovery order; the adapter dedupes by name
 * with first-root-wins (combineSkillNames).
 */
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { createRequire } from 'node:module'

const ocHome = (): string => path.join(os.homedir(), '.openclaw')

export function readOpenclawConfig(): Record<string, any> {
  try {
    return JSON.parse(fs.readFileSync(path.join(ocHome(), 'openclaw.json'), 'utf-8'))
  } catch {
    return {}
  }
}

const expandHome = (p: string): string => p.replace(/^~(?=\/|$)/, os.homedir())

/** openclaw state dir: OPENCLAW_STATE_DIR override, else ~/.openclaw
 * (falling back to the legacy ~/.clawdbot when it is the existing one —
 * mirrors resolveStateDir's legacy preference). */
function resolveStateDir(): string {
  const env = process.env.OPENCLAW_STATE_DIR?.trim()
  if (env) return expandHome(env)
  const legacy = path.join(os.homedir(), '.clawdbot')
  try {
    if (fs.existsSync(legacy) && fs.statSync(legacy).isDirectory()) return legacy
  } catch { /* fall through */ }
  return ocHome()
}

/** openclaw config dir (managed skills live at {CONFIG_DIR}/skills):
 * OPENCLAW_STATE_DIR ?? dirname(OPENCLAW_CONFIG_PATH) ?? ~/.openclaw. */
function resolveConfigDir(): string {
  const state = process.env.OPENCLAW_STATE_DIR?.trim()
  if (state) return expandHome(state)
  const cfgPath = process.env.OPENCLAW_CONFIG_PATH?.trim()
  if (cfgPath) return path.dirname(expandHome(cfgPath))
  return ocHome()
}

/** Mirrors resolveAgentWorkspaceDir (agent-scope-config dist). */
export function resolveAgentWorkspaceDir(agentId: string): string {
  const cfg = readOpenclawConfig()
  const agents = cfg.agents ?? {}
  const entry = agents[agentId]
  const explicit = entry?.workspace?.trim?.()
  if (explicit) return expandHome(String(explicit))
  const defaults = agents.defaults ?? {}
  const fallback = defaults.workspace?.trim?.()
  if (agentId === 'main') {
    if (fallback) return expandHome(String(fallback))
    return path.join(ocHome(), 'workspace')
  }
  if (fallback) return path.join(expandHome(String(fallback)), agentId)
  return path.join(ocHome(), 'workspace', agentId)
}

/** Mirrors resolveEffectiveAgentDir: agents.{id}.agentDir override, else
 * {stateDir}/agents/{id}/agent. */
function resolveAgentDir(agentId: string): string {
  const cfg = readOpenclawConfig()
  const configured = cfg.agents?.[agentId]?.agentDir?.trim?.()
  if (configured) return expandHome(String(configured))
  return path.join(resolveStateDir(), 'agents', agentId, 'agent')
}

/** Looks like a skills dir: a .md file or a subdir with SKILL.md
 * (mirrors bundled-dir looksLikeSkillsDir). */
function looksLikeSkillsDir(dir: string): boolean {
  let entries: fs.Dirent[]
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true })
  } catch {
    return false
  }
  return entries.some((e) => {
    if (e.name.startsWith('.')) return false
    if (e.isFile()) return e.name.endsWith('.md')
    return e.isDirectory() && fs.existsSync(path.join(dir, e.name, 'SKILL.md'))
  })
}

/** Bundled skills dir: OPENCLAW_BUNDLED_SKILLS_DIR ?? sibling of the node
 * binary ?? {openclaw package root}/skills (resolved from this package's
 * location, the plugin loads inside openclaw's install tree). */
function resolveBundledSkillsDir(): string | undefined {
  const env = process.env.OPENCLAW_BUNDLED_SKILLS_DIR?.trim()
  if (env) return expandHome(env)
  try {
    const sibling = path.join(path.dirname(process.execPath), 'skills')
    if (looksLikeSkillsDir(sibling)) return sibling
  } catch { /* fall through */ }
  try {
    const req = createRequire(import.meta.url)
    // Anchor to the openclaw package via a declared exports subpath
    // (package.json is not exportable), then walk up to the first ancestor
    // holding a real skills dir — mirroring the runtime bundled-dir upward
    // probe. Works for both the dev install (devDep openclaw) and the
    // production load (plugin inside openclaw's install tree).
    const entry = req.resolve('openclaw/plugin-sdk/plugin-entry')
    let dir = path.dirname(entry)
    for (let i = 0; i < 6; i += 1) {
      const candidate = path.join(dir, 'skills')
      if (looksLikeSkillsDir(candidate)) return candidate
      const parent = path.dirname(dir)
      if (parent === dir) break
      dir = parent
    }
  } catch { /* openclaw not resolvable from here */ }
  return undefined
}

/** Ambient owner agent (mirrors tryResolveAmbientOwnerAgentId without a
 * requested id): agents.defaults.systemAgent.agentId, else the sole
 * configured agent id. */
function ambientOwnerAgentId(): string | undefined {
  const cfg = readOpenclawConfig()
  const sysAgent = cfg.agents?.defaults?.systemAgent?.agentId
  if (typeof sysAgent === 'string' && sysAgent.trim()) return sysAgent.trim()
  const ids = Object.keys(cfg.agents ?? {}).filter((k) => k !== 'defaults')
  return ids.length === 1 ? ids[0] : undefined
}

/**
 * The skill roots this agent's configuration can load, in runtime order:
 * extraDirs → plugin-skills → bundled (+custodian when this agent is the
 * ambient owner) → workshop (per-agent) → managed → personal (default
 * state only) → workspace (.agents/skills, skills).
 */
export function resolveOpenclawSkillRoots(agentId: string): string[] {
  const roots: string[] = []
  const cfg = readOpenclawConfig()
  const extra = cfg.skills?.load?.extraDirs
  if (Array.isArray(extra)) {
    for (const d of extra) {
      if (typeof d === 'string' && d.trim()) roots.push(path.resolve(expandHome(d.trim())))
    }
  }
  roots.push(path.join(resolveConfigDir(), 'plugin-skills'))
  const bundled = resolveBundledSkillsDir()
  if (bundled) {
    roots.push(bundled)
    if (ambientOwnerAgentId() === agentId) {
      roots.push(path.join(path.dirname(bundled), 'custodian-skills'))
    }
  }
  // Per-agent root: workshop-skills under the owning agent dir.
  roots.push(path.join(resolveAgentDir(agentId), 'workshop-skills'))
  roots.push(path.join(resolveConfigDir(), 'skills'))
  // Personal root only with the default state dir (mirrors isDefaultStateDir).
  if (!process.env.OPENCLAW_STATE_DIR?.trim()) {
    roots.push(path.join(os.homedir(), '.agents', 'skills'))
  }
  const ws = resolveAgentWorkspaceDir(agentId)
  roots.push(path.join(ws, '.agents', 'skills'), path.join(ws, 'skills'))
  return roots
}
