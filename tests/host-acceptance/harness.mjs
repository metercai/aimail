#!/usr/bin/env node
/**
 * Host L2 activation harness (Pi / OpenClaw), no CLI.
 *
 * Drives the host's actual tool registration with a fake host context, exactly
 * like the DSH harness: aimail_system → aimail_address → workspace assertion.
 * No real LLM and no HTTP listener.
 *
 * Usage:
 *   node harness.mjs <pi|openclaw> --gateway URL --system-code SYS \
 *     --address-code ADDR --email E --workspace DIR --home DIR \
 *     [--domain D | --system-name N] [--manager M]
 */
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import * as os from 'node:os'

const host = process.argv[2]
const arg = (name, fallback = '') => {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}
const gateway = arg('gateway', 'http://127.0.0.1:18080')
const systemCode = arg('system-code')
const addressCode = arg('address-code')
const email = arg('email')
const workspace = path.resolve(arg('workspace', path.join(process.cwd(), 'workspace')))
const home = arg('home', path.join(os.tmpdir(), `aimail-host-l2-${Date.now()}`))
const domain = arg('domain')
const systemName = arg('system-name')
const manager = arg('manager')
const delivery = process.argv.includes('--delivery')

if (!['pi', 'openclaw'].includes(host)) { console.error('host must be pi|openclaw'); process.exit(2) }
if ((host !== 'pi' && !systemCode) || !email) {
  console.error('missing --system-code/--email'); process.exit(2)
}
await fs.mkdir(path.join(home, 'aimail'), { recursive: true })
await fs.mkdir(workspace, { recursive: true })
process.env.AIMAIL_HOME = path.join(home, 'aimail')
process.env.HOME = home
if (host === 'pi') process.env.AIMAIL_TRANSPORT = 'sse'
else process.env.AIMAIL_TRANSPORT = 'sse'

const dist = process.env.TSSDK_DIST ?? path.resolve(import.meta.dirname, '../../tssdk/packages/agent-aimail/dist')
const core = await import(path.join(dist, 'core/index.js'))
core.setProfileDirResolver(() => host === 'pi' ? path.join(home, '.pi') : path.join(home, '.openclaw'))

async function toolMap() {
  if (host === 'pi') {
    const { buildPiTools } = await import(path.join(dist, 'pi/tools.js'))
    return new Map(buildPiTools().map((t) => [t.name, t]))
  }
  const { createMailTools } = await import(path.join(dist, 'openclaw/tools.js'))
  return new Map(createMailTools({ agentId: 'main' }, () => workspace).map((t) => [t.name, t]))
}

const tools = await toolMap()
const address = tools.get('aimail_address')
if (!address) { console.error('aimail_address missing'); process.exit(1) }
let sysDetail = { success: true }
let addrDetail
if (host === 'pi') {
  const r = await address.execute('addr', {
    action: 'bind', email, ...(addressCode ? { code: addressCode } : {}), gateway_url: gateway,
  }, undefined, { cwd: workspace })
  addrDetail = r.details ?? r
} else {
  const system = tools.get('aimail_system')
  if (!system) { console.error('aimail_system missing'); process.exit(1) }
  const sysArgs = { action: 'bind', code: systemCode, gateway_url: gateway }
  if (domain) sysArgs.domain = domain
  if (systemName) sysArgs.system_name = systemName
  if (manager) sysArgs.manager = manager
  const sys = await system.execute('sys', sysArgs)
  sysDetail = sys.details ?? sys
  const r = await address.execute('addr', {
    action: 'bind', email, ...(addressCode ? { code: addressCode } : {}), gateway_url: gateway,
  })
  addrDetail = r.details ?? r
}
console.log(JSON.stringify({ step: host === 'pi' ? 'address-only' : 'system', result: sysDetail }))
console.log(JSON.stringify({ step: 'address', result: addrDetail }))
if (addrDetail?.success !== true) process.exit(1)
const { loadAgentConfig } = await import(path.join(dist, 'core/index.js'))
const sid = String(addrDetail.system_id ?? sysDetail.system_id ?? '')
const cfg = sid ? await loadAgentConfig(sid, email).catch(() => undefined) : undefined
if (!cfg) { console.error('binding missing'); process.exit(1) }
if (path.resolve(String(cfg.workspace_path ?? '')) !== workspace) {
  console.error(`workspace mismatch: binding=${cfg.workspace_path} want=${workspace}`)
  process.exit(1)
}
if (cfg.webhook_url) { console.error('binding still has webhook_url'); process.exit(1) }
console.log(JSON.stringify({ step: 'assert', ok: true, workspace: cfg.workspace_path, host }))

// Shared-stream plan must cover the activated address with a system stream.
const { planStreams } = await import(path.join(dist, 'runtime/index.js'))
const sysCfgPath = path.join(process.env.AIMAIL_HOME, 'systems', sid, 'aimail_gateway.json')
const sysCfg = host === 'pi'
  ? { admin_key: '', gateway_url: cfg.gateway_url }
  : JSON.parse(await fs.readFile(sysCfgPath, 'utf-8'))
const plans = host === 'pi'
  ? planStreams([], [{ email, gatewayUrl: cfg.gateway_url, apiKey: cfg.api_key, system_id: sid }])
  : planStreams(
      [{ systemId: sid, gatewayUrl: sysCfg.gateway_url, apiKey: sysCfg.admin_key }],
      [{ email, gatewayUrl: cfg.gateway_url, apiKey: cfg.api_key, system_id: sid }],
    )
const wantScope = host === 'pi' ? 'address' : 'system'
if (plans.length !== 1 || plans[0].scope !== wantScope || !plans[0].emails.includes(email)) {
  console.error('shared stream plan mismatch: ' + JSON.stringify(plans))
  process.exit(1)
}
console.log(JSON.stringify({ step: 'stream-plan', ok: true, plan: plans[0] }))

if (delivery) {
  const { startSystemPull, startAgentPullEntries, GatewayClient } = await import(path.join(dist, 'core/index.js'))
  const seen = []
  const handles = host === 'pi'
    ? [await startSystemPull({
        systemId: email,
        gatewayUrl: cfg.gateway_url,
        apiKey: cfg.api_key,
        onEmail: async (mail) => {
          seen.push({ id: mail.id, email: mail.email, workspace: cfg.workspace_path })
        },
      })]
    : [await startSystemPull({
        systemId: sid,
        gatewayUrl: JSON.parse(await fs.readFile(path.join(process.env.AIMAIL_HOME, 'systems', sid, 'aimail_gateway.json'), 'utf-8')).gateway_url,
        apiKey: JSON.parse(await fs.readFile(path.join(process.env.AIMAIL_HOME, 'systems', sid, 'aimail_gateway.json'), 'utf-8')).admin_key,
        onEmail: async (mail) => {
          seen.push({ id: mail.id, email: mail.email, workspace: cfg.workspace_path })
        },
      })]
  try {
    const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, email)
    const r = await client.request('POST', '/api/v1/system/welcome', host === 'pi' ? { to: [] } : { to: [email] })
    if (!(r.status >= 200 && r.status < 300)) {
      console.error(`welcome failed: ${r.status} ${JSON.stringify(r)}`)
      process.exit(1)
    }
    const deadline = Date.now() + 20_000
    while (seen.length === 0 && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250))
  } finally {
    for (const h of handles) h.stop()
  }
  if (seen.length === 0) { console.error('no SSE delivery received'); process.exit(1) }
  if (path.resolve(seen[0].workspace) !== workspace) {
    console.error(`delivery workspace mismatch: ${seen[0].workspace} want ${workspace}`)
    process.exit(1)
  }
  console.log(JSON.stringify({ step: 'delivery', ok: true, seen }))
}
