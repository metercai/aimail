/**
 * releaseResources — release SDK-shipped board resources into the local
 * config directory.
 *
 * Every AIMail SDK ships its own copy of the board resources (role prompts
 * and role souls, en + zh) — a machine may run only one SDK (e.g. dsh) with
 * no Python SDK installed. This module is the shared release mechanism:
 * each adapter package passes its own bundled `resources/board` root and the
 * resolved system id; the resources land in the standard location the
 * preprocess chain reads from:
 *
 *   ~/.agentmail/systems/{system_id}/board/
 *   ├── role_prompt/        en prompt  (role_prompt_en -> role_prompt)
 *   ├── role_prompt_zh/     zh prompt
 *   ├── role_soul/          en souls   (role_soul_en -> role_soul)
 *   └── role_soul_zh/       zh souls
 *
 * Copy policy: only files that are missing or newer in the source are
 * written — user-personalized files in the config dir are never overwritten.
 * The config dir is the runtime source of truth; the SDK copy is a seed.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { AIMAIL_HOME, systemDir } from './config.js'

/** source subdir (in the SDK's resources/board) -> destination subdir */
const DIR_MAP: ReadonlyArray<[string, string]> = [
  ['role_prompt_en', 'role_prompt'],
  ['role_prompt_zh', 'role_prompt_zh'],
  ['role_soul_en', 'role_soul'],
  ['role_soul_zh', 'role_soul_zh'],
]

export interface ReleaseResourcesResult {
  copied: number
  skipped: number
  boardDir: string
}

export interface ReleaseResourcesOptions {
  /** system id to release into (from pointer / binding / env). */
  systemId: string
  /**
   * Absolute path to the package's bundled `resources/board` directory
   * (contains role_prompt_en/ … role_soul_zh/). Adapters resolve this from
   * their own package at runtime, e.g. path.join(__dirname, '..', 'resources',
   * 'board').
   */
  boardRoot: string
}

/**
 * Idempotent board-resource release. Never overwrites newer/edited target
 * files. Returns per-file stats.
 */
export function releaseResources (opts: ReleaseResourcesOptions): ReleaseResourcesResult {
  const { systemId, boardRoot } = opts
  const boardDir = path.join(systemDir(systemId), 'board')
  let copied = 0
  let skipped = 0
  for (const [srcName, dstName] of DIR_MAP) {
    const srcDir = path.join(boardRoot, srcName)
    const dstDir = path.join(boardDir, dstName)
    if (!fs.existsSync(srcDir)) continue
    fs.mkdirSync(dstDir, { recursive: true })
    for (const f of fs.readdirSync(srcDir)) {
      if (!f.endsWith('.md')) continue
      const src = path.join(srcDir, f)
      const dst = path.join(dstDir, f)
      if (fs.existsSync(dst)) {
        const srcM = fs.statSync(src).mtimeMs
        const dstM = fs.statSync(dst).mtimeMs
        if (dstM >= srcM) {
          skipped += 1
          continue
        }
      }
      fs.copyFileSync(src, dst)
      copied += 1
    }
  }
  return { copied, skipped, boardDir }
}

/**
 * Release into every system directory already present under
 * ~/.agentmail/systems/ (single-system machines included). New system dirs
 * created later are covered by the next explicit release or by adapters
 * calling this again on startup/registration.
 */
export function hasAnySystem (): boolean {
  const systemsRoot = path.join(AIMAIL_HOME(), 'systems')
  if (!fs.existsSync(systemsRoot)) return false
  return fs.readdirSync(systemsRoot).length > 0
}

export function releaseAllSystems (boardRoot: string): ReleaseResourcesResult[] {
  const systemsRoot = path.join(AIMAIL_HOME(), 'systems')
  if (!fs.existsSync(systemsRoot)) return []
  const out: ReleaseResourcesResult[] = []
  for (const ent of fs.readdirSync(systemsRoot)) {
    const p = path.join(systemsRoot, ent)
    if (!fs.statSync(p).isDirectory()) continue
    try {
      out.push(releaseResources({ systemId: ent, boardRoot }))
    } catch {
      // ignore unreadable system dirs
    }
  }
  return out
}
