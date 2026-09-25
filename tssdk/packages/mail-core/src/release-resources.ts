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
 *   ~/.aimail/systems/{system_id}/board/
 *   └── role_prompt/        board role prompts (6 templates)
 *
 * Copy policy: only files that are missing or newer in the source are
 * written — user-personalized files in the config dir are never overwritten.
 * The config dir is the runtime source of truth; the SDK copy is a seed.
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { AIMAIL_HOME, systemDir } from './config.js'

/** source subdir (in the SDK's resources/board) -> destination subdir
 *  2026-09-25 user ruling: board keeps only role prompts (source dir renamed to role_prompt, no _en suffix);
 *  the role_prompt_zh / role_soul_en / role_soul_zh dirs are deleted — no runtime consumer. */
const DIR_MAP: ReadonlyArray<[string, string]> = [
  ['role_prompt', 'role_prompt'],
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
 * Assert the package ships complete resources (2026-09-25 single-source change: missing resources no longer skipped silently).
 * Canonical source = repo-root `resources/`; each distribution point is a materialized copy from scripts/materialize-resources.sh
 * or an npm prepack copy. A missing dir = packaging defect, must be loud on the spot — the silent
 * consequence is "gates all green while the host has zero role resources".
 */
export function assertBoardResources (boardRoot: string): void {
  const hint =
    'repo checkout: run scripts/materialize-resources.sh; ' +
    'installed package: reinstall it (prepack copies resources)'
  if (!boardRoot || !fs.existsSync(boardRoot)) {
    throw new Error(`board resources missing: ${boardRoot || '(empty path)'} — ${hint}`)
  }
  const missing = DIR_MAP.map(([src]) => src)
    .filter((src) => !fs.existsSync(path.join(boardRoot, src)))
  if (missing.length > 0) {
    throw new Error(
      `board resources incomplete: ${boardRoot} missing ${missing.join(', ')} — ${hint}`)
  }
}

/**
 * Idempotent board-resource release. Never overwrites newer/edited target
 * files. Returns per-file stats. Throws when the package's own resources are
 * absent/incomplete (packaging defect — see assertBoardResources).
 */
export function releaseResources (opts: ReleaseResourcesOptions): ReleaseResourcesResult {
  const { systemId, boardRoot } = opts
  assertBoardResources(boardRoot)
  const boardDir = path.join(systemDir(systemId), 'board')
  let copied = 0
  let skipped = 0
  for (const [srcName, dstName] of DIR_MAP) {
    const srcDir = path.join(boardRoot, srcName)
    const dstDir = path.join(boardDir, dstName)
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
 * ~/.aimail/systems/ (single-system machines included). New system dirs
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
  // systems to release => assert package resources first (a packaging defect must be loud, it must not be
  // swallowed by the per-system try below); no systems => return early (nothing to release, not an error).
  assertBoardResources(boardRoot)
  const out: ReleaseResourcesResult[] = []
  for (const ent of fs.readdirSync(systemsRoot)) {
    const p = path.join(systemsRoot, ent)
    if (!fs.statSync(p).isDirectory()) continue
    try {
      out.push(releaseResources({ systemId: ent, boardRoot }))
    } catch (e) {
      // an unreadable/unwritable single system dir must not block other systems, but must **never be silent**
      console.error(`[aimail] board resource release failed for system ${ent}: ${String(e)}`)
    }
  }
  return out
}
