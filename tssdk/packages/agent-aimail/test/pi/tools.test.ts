// pi-aimail regression guard (completed in audit 2026-09-21: this package previously had zero tests).
//
// Covers two types of items that previously really had problems/no one guarded:
//   1. Registration surface = full set of MAIL_TOOLS (adapter only binds identity, must not miss/over-register)
//   2. **the pi manifest must be in place**: the pi host `readPiManifest()` reads package.json's
//      `pi` key; when missing it auto-discovers only by the agreed directories (extensions/skills/prompts/themes);
//      both none ⇒ `pi install npm:pi-aimail` installs **zero resources** (silently registers no tools).
//      Verified that host readPiManifest (old package.json of this package) returns null.
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { MAIL_TOOLS } from '../../src/core/index.js'
import { buildPiTools } from '../../src/pi/tools.js'

const pkgDir = join(dirname(fileURLToPath(import.meta.url)), '..', '..')

describe('pi-aimail tool registration', () => {
  it('buildPiTools() covers every MAIL_TOOLS entry exactly once', () => {
    const built = buildPiTools()
    expect(built.length).toBe(MAIL_TOOLS.length)
    expect(built.map(t => t.name).sort()).toEqual(MAIL_TOOLS.map(t => t.name).sort())
    expect(built.length).toBe(15)
  })

  it('package.json declares a pi manifest whose extensions resolve', () => {
    const pkg = JSON.parse(readFileSync(join(pkgDir, 'package.json'), 'utf-8')) as {
      pi?: { extensions?: string[] }
    }
    expect(pkg.pi?.extensions?.length, 'pi.extensions missing ⇒ zero extensions after pi install').toBeTruthy()
    for (const rel of pkg.pi!.extensions!) {
      expect(existsSync(join(pkgDir, rel)), `${rel} does not exist (run pnpm build first)`).toBe(true)
    }
  })
})
