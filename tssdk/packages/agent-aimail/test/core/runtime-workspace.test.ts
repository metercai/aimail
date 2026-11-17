import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { pinWorkspaceToBinding } from '../../src/runtime/index.js'
import { loadAgentConfig, saveAgentConfig, AIMAIL_HOME } from '../../src/core/index.js'

let home: string
let prev: string | undefined

beforeEach(() => {
  prev = process.env.AIMAIL_HOME
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-runtime-ws-'))
  process.env.AIMAIL_HOME = home
})
afterEach(() => {
  if (prev === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prev
  fs.rmSync(home, { recursive: true, force: true })
})

describe('runtime workspace pinning', () => {
  it('writes workspace_id/path onto the activated binding', async () => {
    const sid = 'sys-ws'
    await saveAgentConfig({
      email: 'alice@test.local', gateway_url: 'http://127.0.0.1:1', system_id: sid,
      domain: 'test.local', api_key: 'k'.repeat(32), manager_address: 'm@test.local',
    } as never, sid)
    expect(await pinWorkspaceToBinding('alice@test.local', {
      id: 'w-alice', path: '/work/alice', title: 'alice',
    })).toBe(true)
    const cfg = await loadAgentConfig(sid, 'alice@test.local')
    expect(cfg?.workspace_id).toBe('w-alice')
    expect(cfg?.workspace_path).toBe('/work/alice')
    // sanity: the binding actually landed under the isolated home
    expect(fs.existsSync(path.join(home, 'systems', sid))).toBe(true)
  })
})
