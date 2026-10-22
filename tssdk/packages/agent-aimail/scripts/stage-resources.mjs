#!/usr/bin/env node
/**
 * stage-resources.mjs — copy the canonical resources/ into a package that ships them.
 *
 * Why this exists: an npm tarball can only contain files under the package directory, so
 * each adapter package must carry its own copy of the SDK resources. The copy is therefore
 * part of that package's OWN packaging step (its `prepack`), which names its own
 * destination — there is deliberately no repo-level script that owns every package's layout.
 *
 * Canonical source (single source of truth) = repo-root `resources/` (git-tracked; the only
 * place resources are edited). This script never edits it.
 *
 * Usage:  node scripts/stage-resources.mjs --dest .        # from the package directory
 *         node scripts/stage-resources.mjs --dest <dir> [--canon <dir>]
 */
import { cpSync, existsSync, mkdirSync, rmSync, readdirSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const here = dirname(fileURLToPath(import.meta.url))          // <pkg>/scripts
const args = process.argv.slice(2)
const argOf = (name) => {
  const i = args.indexOf(name)
  return i >= 0 && args[i + 1] ? args[i + 1] : ''
}

// default canonical: <repo>/resources (this script lives at tssdk/packages/<pkg>/scripts)
const canon = resolve(argOf('--canon') || join(here, '..', '..', '..', '..', 'resources'))
const dest = resolve(argOf('--dest') || '')

if (!argOf('--dest')) {
  console.error('stage-resources: --dest required — the directory the resources are written to')
  console.error('                 (packages pass --dest ./resources: this script owns a')
  console.error('                  directory it deletes first, so it must never be a package root)')
  process.exit(2)
}
if (!existsSync(canon)) {
  console.error(`stage-resources: canonical resources missing: ${canon}`)
  process.exit(1)
}
// Safety: the destination is wiped before copying. Writing into a package root would delete
// the package itself, so refuse it outright.
if (existsSync(join(dest, 'package.json')) || resolve(dest) === resolve(dest, '..', '..')) {
  console.error(`stage-resources: refusing to stage into a package root: ${dest}`)
  process.exit(2)
}

rmSync(dest, { recursive: true, force: true })
mkdirSync(dest, { recursive: true })
cpSync(canon, dest, { recursive: true })
console.log(`stage-resources: ${canon} -> ${dest} (${readdirSync(dest).sort().join(' ')})`)
