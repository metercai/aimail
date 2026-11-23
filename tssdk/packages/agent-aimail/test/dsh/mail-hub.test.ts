/**
 * Mail hub delivery: thread meta → existing session / new workspace session;
 * new message gets session_id stamped for reply routing.
 */
import { afterEach, beforeEach, describe, expect, test } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { MailHub } from '../../src/dsh/mail-hub.js'
import { saveAgentConfig, readLocalMeta } from '../../src/core/index.js'

let home = ''
let dsh = ''
let followups: unknown[] = []
let created: Array<{ sessionId: string; cwd?: string }> = []
let liveId = ''

function ctx(): never {
  const values = new Map<string, unknown>()
  values.set('agents', {
    get: (id: string) => (liveId && id === liveId ? { followup: (m: unknown) => followups.push(m) } : undefined),
    resume: async () => { throw new Error('not resumable') },
    create: async (opts: { sessionId: string; meta?: { cwd?: string } }) => {
      created.push({ sessionId: opts.sessionId, ...(opts.meta?.cwd ? { cwd: opts.meta.cwd } : {}) })
      return { agent: { followup: (m: unknown) => followups.push(m) }, dispose: async () => {} }
    },
  })
  values.set('workspaceRegistry', { list: () => [{ id: 'w', path: path.join(dsh, 'default-workspace'), title: 'default-workspace' }], create: async (p: string) => ({ id: 'w2', path: p }) })
  values.set('agentDefaultModel', { currentSelection: () => ({ provider: 'p', model: 'm' }) })
  values.set('mail', { resolveByRecipient: async () => undefined })
  return { get: (k: string) => values.get(k), provide: () => {} } as never
}

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-hub-'))
  dsh = await fs.mkdtemp(path.join(os.tmpdir(), 'dsh-hub-'))
  process.env.AIMAIL_HOME = home
  process.env.DSH_HOME = dsh
  await fs.mkdir(path.join(dsh, 'default-workspace'), { recursive: true })
  await fs.mkdir(path.join(dsh, 'storages'), { recursive: true })
  await fs.writeFile(path.join(dsh, 'storages', 'workspace.json'), JSON.stringify({
    global: { defaultWorkspaceId: 'w' },
    tables: { workspaces: { w: { path: path.join(dsh, 'default-workspace'), title: 'default-workspace' } } },
  }))
  followups = []
  created = []
})

afterEach(async () => {
  delete process.env.AIMAIL_HOME
  delete process.env.DSH_HOME
  await fs.rm(home, { recursive: true, force: true })
  await fs.rm(dsh, { recursive: true, force: true })
})

describe('mail hub delivery', () => {
  test('creates a session in the bound workspace and stamps session_id', async () => {
    const cfg = {
      email: 'u@a.com',
      gateway_url: 'http://gw',
      system_id: 'sys-1',
      domain: 'a.com',
      api_key: 'k',
      workspace_id: 'w',
      workspace_path: path.join(dsh, 'default-workspace'),
      workspace_title: 'default-workspace',
      paused: false,
    }
    await saveAgentConfig(cfg, 'sys-1')
    const hub = new MailHub(ctx())
    const ok = await hub.deliverOne(cfg, {
      message_id: 'm1',
      from: 'sender@example.com',
      to: ['u@a.com'],
      subject: 'Hello',
      body: 'hi there',
      references: [],
      headers: {},
    }, {}, 'u@a.com')
    expect(ok.status).toBe('accepted')
    expect(created).toHaveLength(1)
    expect(created[0]?.cwd).toBe(await fs.realpath(path.join(dsh, 'default-workspace')))
    expect(followups).toHaveLength(1)
    const meta = await readLocalMeta('u@a.com', 'm1')
    expect(meta?.session_id).toBe(created[0]?.sessionId)
    expect(meta?.workspace_path).toBe(await fs.realpath(path.join(dsh, 'default-workspace')))
  })
})

test('a reply whose thread meta points at a live session is delivered to it', async () => {
  const cfg = {
    email: 'u@a.com',
    gateway_url: 'http://gw',
    system_id: 'sys-1',
    domain: 'a.com',
    api_key: 'k',
    workspace_id: 'w',
    workspace_path: path.join(dsh, 'default-workspace'),
    workspace_title: 'default-workspace',
    paused: false,
  }
  await saveAgentConfig(cfg, 'sys-1')
  const hub = new MailHub(ctx())
  const first = await hub.deliverOne(cfg, {
    message_id: 'm1', from: 's@example.com', to: ['u@a.com'], subject: 'Hi', body: 'one', references: [], headers: {},
  }, {}, 'u@a.com')
  expect(first.status).toBe('accepted')
  liveId = created[0]!.sessionId
  const second = await hub.deliverOne(cfg, {
    message_id: 'm2', from: 's@example.com', to: ['u@a.com'], subject: 'Re: Hi', body: 'two', references: ['m1'], headers: { in_reply_to: 'm1' },
  }, {}, 'u@a.com')
  expect(second.status).toBe('accepted')
  expect(created).toHaveLength(1)
  expect(followups).toHaveLength(2)
  const meta = await readLocalMeta('u@a.com', 'm2')
  expect(meta?.session_id).toBe(liveId)
})
