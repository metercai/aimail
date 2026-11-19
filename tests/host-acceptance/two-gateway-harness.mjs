#!/usr/bin/env node
/** Two-Gateway AgentSystemId L2. */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'

const arg = (n, d = '') => {
  const i = process.argv.indexOf(`--${n}`)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d
}
const aUrl = arg('a')
const bUrl = arg('b')
const aCode = arg('a-code')
const bCode = arg('b-code')
const home = path.resolve(arg('home'))
const workspace = path.resolve(arg('workspace'))
if (!aUrl || !bUrl || !aCode || !bCode) { console.error('missing args'); process.exit(2) }
await fs.mkdir(path.join(home, 'aimail'), { recursive: true })
await fs.mkdir(workspace, { recursive: true })
process.env.HOME = home
process.env.AIMAIL_HOME = path.join(home, 'aimail')
process.env.AIMAIL_AGENT_HOME = home
process.env.AIMAIL_AGENT_SYSTEM_ID = 'asid_l2_two_gateways'
process.env.AIMAIL_TRANSPORT = 'sse'

const dist = process.env.TSSDK_DIST ?? path.resolve(import.meta.dirname, '../../tssdk/packages/agent-aimail/dist')
const core = await import(path.join(dist, 'core/index.js'))
core.setProfileDirResolver(() => home)
const aEmail = 'alice@test.local'
const bEmail = 'bob@test.local'
for (const [email, code, gw] of [[aEmail, aCode, aUrl], [bEmail, bCode, bUrl]]) {
  const r = await core.aimailAddress({ action: 'bind', email, code, gatewayUrl: gw })
  if (r.success !== true) { console.error('bind failed', email, r); process.exit(1) }
}
const agentSystemId = await core.getAgentSystemId()
const seen = []
const handles = []
const findCfg = async (email) => {
  for (const sid of await core.listSystemDirs()) {
    const c = await core.loadAgentConfig(sid, email).catch(() => undefined)
    if (c?.api_key) return c
  }
  return undefined
}
for (const [email, gw] of [[aEmail, aUrl], [bEmail, bUrl]]) {
  const binding = await findCfg(email)
  if (!binding) { console.error('binding missing', email); process.exit(1) }
  handles.push(await core.startSystemPull({
    systemId: email,
    agentSystemId,
    gatewayUrl: gw,
    apiKey: binding.api_key,
    onEmail: async (mail) => { seen.push({ id: mail.id, email: mail.email, body: mail.body }) },
  }))
}
const send = async (email, gw) => {
  const cfg = await findCfg(email)
  const c = new core.GatewayClient(gw, cfg.api_key, 30_000, email)
  const r = await c.request('POST', '/api/v1/system/welcome', { to: [] })
  if (!(r.status >= 200 && r.status < 300)) console.error('welcome failed', email, r)
}
await send(aEmail, aUrl)
await send(bEmail, bUrl)
const deadline = Date.now() + 20_000
while (seen.length < 2 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250))
for (const h of handles) h.stop()
console.log(JSON.stringify({ agentSystemId, seen }, null, 2))
if (seen.length < 2 || !seen.some((s) => s.email === aEmail) || !seen.some((s) => s.email === bEmail)) process.exit(1)
