// pi-aimail 回归守卫（审计 2026-09-21 补齐：本包此前零测试）。
//
// 覆盖两类曾经真出问题/无人守卫的事项：
//   1. 注册面 = MAIL_TOOLS 全集（适配器只绑定身份，不得漏/多注册）
//   2. **pi manifest 必须在位**：pi 宿主 `readPiManifest()` 读 package.json 的
//      `pi` 键，缺失时只按约定目录(extensions/skills/prompts/themes)自动发现；
//      两者皆无 ⇒ `pi install npm:pi-aimail` 装完**零资源**(静默不注册任何工具)。
//      实测宿主 readPiManifest(本包旧 package.json) 返回 null。
import { describe, it, expect } from 'vitest'
import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { MAIL_TOOLS } from '@aimail/mail-core'
import { buildPiTools } from '../src/tools.ts'

const pkgDir = join(dirname(fileURLToPath(import.meta.url)), '..')

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
    expect(pkg.pi?.extensions?.length, 'pi.extensions 缺失 ⇒ pi install 后零扩展').toBeTruthy()
    for (const rel of pkg.pi!.extensions!) {
      expect(existsSync(join(pkgDir, rel)), `${rel} 不存在(先 pnpm build)`).toBe(true)
    }
  })
})
