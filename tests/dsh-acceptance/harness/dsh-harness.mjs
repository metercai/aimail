#!/usr/bin/env node
/**
 * dsh-harness — drive the tssdk dsh adapter without a full DSH UI.
 *
 * It uses the compiled tssdk dist (`pnpm build` first) and a fake Cordis ctx
 * (agents/sessions/workspaceRegistry). This is the same fake-ctx pattern used
 * by the package unit tests; it verifies the adapter contract (config, keys,
 * workspace binding, SSE delivery), not the LLM.
 *
 * Usage:
 *   node dsh-harness.mjs system  --gateway URL --code CODE --domain D [--home DIR]
 *   node dsh-harness.mjs system  --gateway URL --system_key KEY --system_id SID --domain D [--home DIR]
 *   node dsh-harness.mjs address --gateway URL --email E --code CODE --workspace DIR [--home DIR]
 *   node dsh-harness.mjs lifecycle --email E --action pause|resume|unbind [--home DIR]
 *   node dsh-harness.mjs hub     --gateway URL --email E --timeout SEC [--home DIR]
 *
 * Output is one JSON object on stdout; exit != 0 means the assertion failed.
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'
import { fileURLToPath } from 'node:url'

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const DIST = process.env.TSSDK_DIST || path.join(ROOT, 'tssdk/packages/agent-aimail/dist')
const { provisionSystem, runAddressTool, MailHub } = await import(path.join(DIST, 'dsh/index.js'))

function arg(name, fallback = '') {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}
const cmd = process.argv[2]
const home = arg('home', path.join(os.tmpdir(), 'dsh-aimail-acceptance'))
const aimailHome = path.join(home, 'aimail')
const dshHome = path.join(home, 'dsh')
const workspace = arg('workspace', path.join(dshHome, 'workspace'))
const gateway = arg('gateway', 'http://127.0.0.1:18080')

async function ensureHome() {
  await fs.mkdir(aimailHome, { recursive: true })
  await fs.mkdir(path.join(dshHome, 'storages'), { recursive: true })
  await fs.mkdir(workspace, { recursive: true })
  await fs.writeFile(path.join(dshHome, 'storages', 'workspace.json'), JSON.stringify({
    global: { defaultWorkspaceId: 'w-accept' },
    tables: { workspaces: { 'w-accept': { path: workspace, title: path.basename(workspace) } } },
  }), 'utf-8')
  process.env.AIMAIL_HOME = aimailHome
  process.env.DSH_HOME = dshHome
}

function ctx() {
  const values = new Map()
  const followups = []
  values.set('workspaceRegistry', {
    list: () => [{ id: 'w-accept', path: workspace, title: path.basename(workspace) }],
    create: async (p) => ({ id: 'w-accept', path: p, title: path.basename(p) }),
  })
  values.set('sessions', { get: (id) => (id === 'sess-accept' ? { header: { cwd: workspace } } : undefined) })
  values.set('agents', {
    get: () => undefined,
    resume: async () => { throw new Error('no resume in harness') },
    create: async (opts) => ({
      agent: { followup: (m) => followups.push({ sessionId: opts.sessionId, cwd: opts.meta?.cwd, message: m }) },
      dispose: async () => {},
    }),
  })
  values.set('agentDefaultModel', { currentSelection: () => ({ provider: 'test', model: 'test' }) })
  values.set('mail', { resolveByRecipient: async () => undefined })
  return { ctx: { get: (k) => values.get(k), provide: (k, v) => values.set(k, v) }, followups }
}

async function main() {
  await ensureHome()
  if (cmd === 'system') {
    const { ctx: c } = ctx()
    const r = await provisionSystem(c, {
      action: 'bind',
      code: arg('code'),
      gateway_url: gateway,
      ...(arg('domain') ? { domain: arg('domain') } : {}),
      ...(arg('system_name') ? { system_name: arg('system_name') } : {}),
      ...(arg('manager') ? { manager: arg('manager') } : {}),
      ...(arg('system_key') ? { system_key: arg('system_key') } : {}),
      ...(arg('system_id') ? { system_id: arg('system_id') } : {}),
    })
    console.log(JSON.stringify(r, null, 2))
    process.exit(r.success ? 0 : 1)
  }
  if (cmd === 'address') {
    const { ctx: c } = ctx()
    const r = await runAddressTool(c, { agent: { id: 'sess-accept' } }, {
      action: 'bind',
      email: arg('email'),
      ...(arg('code') ? { code: arg('code') } : {}),
      gateway_url: gateway,
    })
    console.log(JSON.stringify(r, null, 2))
    process.exit(r.success ? 0 : 1)
  }
  if (cmd === 'lifecycle') {
    const { ctx: c } = ctx()
    const r = await runAddressTool(c, { agent: { id: 'sess-accept' } }, {
      action: arg('action'),
      email: arg('email'),
    })
    console.log(JSON.stringify(r, null, 2))
    process.exit(r.success || arg('action') === 'unbind' ? 0 : 1)
  }
  if (cmd === 'hub') {
    const { ctx: c, followups } = ctx()
    const hub = new MailHub(c)
    await hub.start()
    const timeout = Number(arg('timeout', '30')) * 1000
    const started = Date.now()
    while (followups.length === 0 && Date.now() - started < timeout) {
      await new Promise((r) => setTimeout(r, 300))
    }
    const status = await hub.status()
    const out = { status, followups: followups.map((f) => ({ sessionId: f.sessionId, cwd: f.cwd, hasMessage: Boolean(f.message) })) }
    console.log(JSON.stringify(out, null, 2))
    hub.dispose()
    process.exit(followups.length > 0 ? 0 : 1)
  }
  console.error('usage: dsh-harness.mjs system|address|hub ...')
  process.exit(2)
}

main().catch((e) => { console.error(e); process.exit(1) })
