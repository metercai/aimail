/**
 * agentmail.json read/write — single source of truth, atomic writes.
 * Layout: ~/.agentmail/systems/{system_id}/{cleaned_addr}/agentmail.json
 * Contract: AGENTMAIL-JSON-REFERENCE.md; clean rule mirrors Python
 * `_clean_agent_dir_name` (non [\w.-] → '_').
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'
import type { AgentConfig } from './types.js'

export const AMAIL_HOME = (): string =>
  process.env.AMAIL_HOME || path.join(os.homedir(), '.agentmail')

export function systemDir(systemId: string): string {
  return path.join(AMAIL_HOME(), 'systems', systemId)
}

/** Clean an address into a directory key (must match Python exactly). */
export function cleanAddr(email: string): string {
  return email.replace(/[^\w.-]/g, '_')
}

export function agentConfigPath(systemId: string, email: string): string {
  return path.join(systemDir(systemId), cleanAddr(email), 'agentmail.json')
}

/** Load one agent config by address (missing → undefined). */
export async function loadAgentConfig(
  systemId: string,
  email: string,
): Promise<AgentConfig | undefined> {
  const p = agentConfigPath(systemId, email)
  try {
    const raw = await fs.readFile(p, 'utf-8')
    const cfg = JSON.parse(raw) as AgentConfig
    cfg._config_path = p
    return cfg
  } catch {
    return undefined
  }
}

/**
 * Resolve config by dsh session id (uuid instance identity).
 * systemId empty → scan all systems/{sid} address dirs.
 */
export async function loadConfigBySessionId(
  systemId: string,
  sessionId: string,
): Promise<AgentConfig | undefined> {
  const dirs = systemId ? [systemDir(systemId)] : await listSystemDirs()
  for (const dir of dirs) {
    let entries
    try {
      entries = await fs.readdir(dir, { withFileTypes: true })
    } catch {
      continue
    }
    for (const ent of entries) {
      if (!ent.isDirectory()) continue
      const p = path.join(dir, ent.name, 'agentmail.json')
      try {
        const cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as AgentConfig
        if (cfg.session_id === sessionId) {
          cfg._config_path = p
          return cfg
        }
      } catch {
        /* skip unreadable */
      }
    }
  }
  return undefined
}

/** Resolve config by email address (systemId empty → scan all systems). */
export async function loadConfigByEmail(
  email: string,
  systemId = '',
): Promise<AgentConfig | undefined> {
  const dirs = systemId ? [systemDir(systemId)] : await listSystemDirs()
  for (const dir of dirs) {
    const p = path.join(dir, cleanAddr(email), 'agentmail.json')
    try {
      const cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as AgentConfig
      if (cfg.email === email) {
        cfg._config_path = p
        return cfg
      }
    } catch {
      /* skip */
    }
  }
  return undefined
}

async function listSystemDirs(): Promise<string[]> {
  try {
    const entries = await fs.readdir(systemDir(''), { withFileTypes: true })
    return entries.filter(e => e.isDirectory()).map(e => path.join(systemDir(''), e.name))
  } catch {
    return []
  }
}

/** Resolve config by platform agent id (main/default/...) — scans all systems. */
export async function loadConfigByAgentId(
  systemId: string,
  agentId: string,
): Promise<AgentConfig | undefined> {
  const dir = systemDir(systemId)
  let entries
  try {
    entries = await fs.readdir(dir, { withFileTypes: true })
  } catch {
    return undefined
  }
  for (const ent of entries) {
    if (!ent.isDirectory()) continue
    const p = path.join(dir, ent.name, 'agentmail.json')
    try {
      const cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as AgentConfig
      if (cfg.agent_id === agentId || (agentId === 'main' && !cfg.agent_id)) {
        cfg._config_path = p
        return cfg
      }
    } catch {
      /* skip */
    }
  }
  return undefined
}

/** Atomic write (tmp + rename), mode 0600 — preserves existing fields by merge. */
export async function saveAgentConfig(
  cfg: AgentConfig,
  systemId: string,
): Promise<string> {
  const p = agentConfigPath(systemId, cfg.email)
  await fs.mkdir(path.dirname(p), { recursive: true, mode: 0o700 })
  const tmp = `${p}.tmp`
  const { _config_path, ...rest } = cfg
  await fs.writeFile(tmp, JSON.stringify(rest, null, 2) + '\n', { mode: 0o600 })
  await fs.rename(tmp, p)
  await fs.chmod(p, 0o600)
  return p
}

/** Update a single field atomically. */
export async function updateAgentConfig(
  systemId: string,
  email: string,
  patch: Partial<AgentConfig>,
): Promise<AgentConfig | undefined> {
  const cfg = (await loadAgentConfig(systemId, email)) ?? ({} as AgentConfig)
  Object.assign(cfg, patch)
  cfg.email = cfg.email || email
  await saveAgentConfig(cfg, systemId)
  return cfg
}
