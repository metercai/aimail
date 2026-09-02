import { describe, expect, test, beforeAll, afterAll } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { releaseResources, releaseAllSystems, AIMAIL_HOME } from '../src/index.js'

/** Board layout expected by the preprocess chain after release. */
const EXPECTED_DIRS = ['role_prompt', 'role_prompt_zh', 'role_soul', 'role_soul_zh']
const EXPECTED_COUNT = {
  role_prompt: 6, // incl. role_calibrator
  role_prompt_zh: 6,
  role_soul: 4,
  role_soul_zh: 4,
}

let tmpHome: string
let sdkBoard: string

beforeAll(() => {
  tmpHome = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-rel-'))
  process.env.AIMAIL_HOME = tmpHome
  // fake SDK resources (mirrors the shipped resources/board layout)
  sdkBoard = path.join(tmpHome, 'sdk-resources', 'board')
  const perDir: Record<string, number> = { role_prompt_en: 6, role_prompt_zh: 6, role_soul_en: 4, role_soul_zh: 4 }
  for (const [sub, n] of Object.entries(perDir)) {
    const d = path.join(sdkBoard, sub)
    fs.mkdirSync(d, { recursive: true })
    for (let i = 0; i < n; i++) {
      fs.writeFileSync(path.join(d, i === 0 && sub === 'role_prompt_en' ? 'role_calibrator.md' : `file${i}.md`), `# ${sub} ${i}`)
    }
  }
})

afterAll(() => {
  fs.rmSync(tmpHome, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

describe('releaseResources', () => {
  test('releases all four board dirs into the system config dir', () => {
    const r = releaseResources({ systemId: 'sys-a', boardRoot: sdkBoard })
    expect(r.copied).toBeGreaterThanOrEqual(20)
    expect(fs.existsSync(path.join(AIMAIL_HOME(), 'systems', 'sys-a', 'board'))).toBe(true)
    for (const d of EXPECTED_DIRS) {
      const dir = path.join(AIMAIL_HOME(), 'systems', 'sys-a', 'board', d)
      expect(fs.readdirSync(dir).length).toBe(EXPECTED_COUNT[d as keyof typeof EXPECTED_COUNT])
    }
    // en default dir holds role_calibrator
    expect(fs.existsSync(path.join(AIMAIL_HOME(), 'systems', 'sys-a', 'board', 'role_prompt', 'role_calibrator.md'))).toBe(true)
  })

  test('idempotent: second release copies nothing (keeps user mtimes)', () => {
    releaseResources({ systemId: 'sys-a', boardRoot: sdkBoard })
    const r2 = releaseResources({ systemId: 'sys-a', boardRoot: sdkBoard })
    expect(r2.copied).toBe(0)
  })

  test('never overwrites a user-personalized (newer) file', () => {
    const dst = path.join(AIMAIL_HOME(), 'systems', 'sys-a', 'board', 'role_prompt', 'file0.md')
    const personalized = '# personalized by user\n'
    fs.writeFileSync(dst, personalized)
    // bump mtime into the future
    const t = Date.now() / 1000 + 60
    fs.utimesSync(dst, t, t)
    releaseResources({ systemId: 'sys-a', boardRoot: sdkBoard })
    expect(fs.readFileSync(dst, 'utf8')).toBe(personalized)
  })

  test('releaseAllSystems covers every existing system dir', () => {
    fs.mkdirSync(path.join(AIMAIL_HOME(), 'systems', 'sys-b'), { recursive: true })
    const results = releaseAllSystems(sdkBoard)
    const sids = results.map((r) => path.basename(path.dirname(r.boardDir)))
    expect(sids).toContain('sys-b')
    expect(sids).toContain('sys-a')
  })
})
