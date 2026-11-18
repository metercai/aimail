import { describe, it, expect } from 'vitest'
import { readFileSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, join } from 'node:path'
import { createMailTools } from '../../src/openclaw/tools.js'

describe('openclaw plugin manifest tool contract', () => {
  it('contracts.tools exactly covers the tools the factory registers', () => {
    const root = join(dirname(fileURLToPath(import.meta.url)), '..', '..')
    const manifest = JSON.parse(readFileSync(join(root, 'openclaw.plugin.json'), 'utf-8')) as {
      contracts?: { tools?: string[] }
    }
    const built = createMailTools({ agentId: 'main' } as never, () => process.cwd()).map(t => t.name)
    expect(new Set(manifest.contracts?.tools)).toEqual(new Set(built))
  })
})
