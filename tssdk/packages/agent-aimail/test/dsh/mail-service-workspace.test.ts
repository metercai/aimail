// Inbound sessions are disposable: the tools must resolve the mailbox through
// the stable workspace mapping, never through a session_id written to the binding.
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

const ensureAgentBinding = vi.fn()

vi.mock('../../src/core/index.js', async (importOriginal) => {
  const actual = (await importOriginal<typeof import('../../src/core/index.js')>()) as typeof import('../../src/core/index.js')
  return {
    ...actual,
    ensureAgentBinding: (...a: unknown[]) => ensureAgentBinding(...a),
  }
})

vi.mock('../../src/mail/index.js', () => ({
  resolveBySessionId: async () => {
    throw new Error('unbound session')
  },
  resolveByEmail: async () => {
    throw new Error('unbound email')
  },
  resolveByRecipient: async () => undefined,
}))

import { apply, ensureDefaultAgentBinding } from '../../src/dsh/mail-service.js'
import { loadAgentConfig, saveAgentConfig } from '../../src/core/index.js'

let home = ''
let dsh = ''

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-ws-home-'))
  dsh = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-ws-dsh-'))
  process.env.AIMAIL_HOME = home
  process.env.DSH_HOME = dsh
  await fs.mkdir(dsh, { recursive: true })
  await fs.mkdir(path.join(home, 'systems/sys-1'), { recursive: true })
  ensureAgentBinding.mockReset()
  ensureAgentBinding.mockResolvedValue({ ok: false, error: 'not needed' })
})

afterEach(async () => {
  delete process.env.AIMAIL_HOME
  delete process.env.DSH_HOME
  await fs.rm(home, { recursive: true, force: true })
  await fs.rm(dsh, { recursive: true, force: true })
})

describe('dsh mailbox resolution by workspace', () => {
  test('a new session resolves the binding whose workspace matches its cwd', async () => {
    const workspace = path.join(dsh, 'workspace')
    await fs.mkdir(workspace, { recursive: true })
    await saveAgentConfig({
      email: 'agent@l3.local',
      gateway_url: 'http://127.0.0.1:1',
      system_id: 'sys-1',
      domain: 'l3.local',
      api_key: 'k',
      workspace_id: 'w',
      workspace_path: workspace,
      workspace_title: 'workspace',
      paused: false,
    }, 'sys-1')

    let service: { resolveCtx(id: string): Promise<{ systemId: string; email: string }> } | undefined
    const ctx = {
      get: (k: string) => {
        if (k === 'sessions') return { get: () => ({ header: { cwd: workspace } }) }
        return undefined
      },
      provide: (name: string, svc: unknown) => {
        if (name === 'mail') service = svc as typeof service
      },
    }
    apply(ctx as never, {})
    const got = await service!.resolveCtx('new-session-id')
    expect(got.email).toBe('agent@l3.local')
    expect(got.systemId).toBe('sys-1')
  })

  test('startup ensure persists the default workspace onto the CLI-created binding', async () => {
    const workspace = path.join(dsh, 'default-workspace')
    await fs.mkdir(workspace, { recursive: true })
    await saveAgentConfig({
      email: 'agent@l3.local',
      gateway_url: 'http://127.0.0.1:1',
      system_id: 'sys-1',
      domain: 'l3.local',
      api_key: 'k',
      paused: false,
    }, 'sys-1')
    ensureAgentBinding.mockResolvedValue({ ok: true, systemId: 'sys-1', email: 'agent@l3.local' })
    const ctx = {
      get: (k: string) => {
        if (k === 'workspaceRegistry') return { list: () => [{ id: 'w', path: workspace, title: 'default-workspace' }] }
        return undefined
      },
    }
    await ensureDefaultAgentBinding(ctx as never)
    const cfg = await loadAgentConfig('sys-1', 'agent@l3.local')
    expect(cfg?.workspace_id).toBe('w')
    expect(cfg?.workspace_path).toBe(workspace)
  })
})
