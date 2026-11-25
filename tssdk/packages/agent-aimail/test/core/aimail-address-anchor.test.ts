/**
 * The anchor must survive the TOOL wrapper (L2 defect 3, 2026-11-25).
 *
 * `aimailAddressTool` is the agent-facing `aimail_address` entry; host adapters inject the anchor on
 * it. pi and openclaw inject the camelCase `workspacePath`, while the wrapper only forwarded the
 * snake_case `workspace_path` — so the anchor was silently dropped, the activation carried no
 * `anchor_id`, and the gateway had nothing to enforce for that address. pi's L2 "green" was really
 * "no anchor row ⇒ the enforcement branch never ran".
 */
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { aimailAddressTool } from '../../src/core/tools.js'
import { anchorId } from '../../src/core/anchor-id.js'
import { BINDING_FILE } from '../../src/core/contract.js'
import { setProfileDirResolver } from '../../src/core/index.js'

const EMAIL = 'pi@test.local'
const ANCHOR = '/home/u/.pi/agent'

let home = ''
let prevHome: string | undefined
let prevAimail: string | undefined

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-anchor-tool-'))
  prevHome = process.env.HOME
  prevAimail = process.env.AIMAIL_HOME
  process.env.HOME = home
  process.env.AIMAIL_HOME = path.join(home, 'aimail-home')
  setProfileDirResolver(() => path.join(home, '.pi'))
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimail === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimail
  setProfileDirResolver(undefined)
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
})

async function captureActivation(): Promise<Record<string, unknown>> {
  const bodies: Array<Record<string, unknown>> = []
  const fetchImpl = vi.fn(async (url: string, init: { body?: Uint8Array }) => {
    bodies.push(JSON.parse(new TextDecoder().decode(init.body)) as Record<string, unknown>)
    const payload = {
      raw_key: 'k-new',
      system_id: 'shared_addr_sys',
      email_address: EMAIL,
      expires_at: '2099-01-01T00:00:00Z',
      manager_address: 'manager@test.local',
    }
    return { status: 200, text: async () => JSON.stringify(payload), headers: { get: () => 'application/json' } }
  })
  vi.stubGlobal('fetch', fetchImpl)

  const res = await aimailAddressTool({
    action: 'bind',
    code: 'addr-1111-2222-3333',
    email: EMAIL,
    gatewayUrl: 'https://gw.example',
    // The adapter-injected alias (pi passes `piAnchor()` here).
    workspacePath: ANCHOR,
  })
  expect(res.success).toBe(true)
  expect(fetchImpl).toHaveBeenCalled()
  return bodies[0]!
}

describe('aimail_address keeps the injected anchor', () => {
  it('sends anchor_id from the camelCase workspacePath alias', async () => {
    const body = await captureActivation()
    expect(body.email_address).toBe(EMAIL)
    expect(body.anchor_id).toBe(anchorId(EMAIL, ANCHOR))
  })

  it('persists the same anchor in the binding (pull side hashes the same value)', async () => {
    await captureActivation()
    const file = path.join(
      process.env.AIMAIL_HOME!,
      'systems',
      'shared_addr_sys',
      EMAIL.replace('@', '_'),
      BINDING_FILE,
    )
    const cfg = JSON.parse(await fs.readFile(file, 'utf-8')) as Record<string, unknown>
    expect(cfg.workspace_path).toBe(ANCHOR)
    // The header the pull sends must reproduce the hash the gateway stored.
    expect(anchorId(String(cfg.email), String(cfg.workspace_path))).toBe(anchorId(EMAIL, ANCHOR))
  })
})
