#!/usr/bin/env node
/**
 * Host L2 activation harness (Pi / OpenClaw), no CLI.
 *
 * Drives the host's actual tool registration with a fake host context, exactly
 * like the DSH harness: aimail_system → aimail_address → workspace assertion.
 * No real LLM and no HTTP listener.
 *
 * Usage:
 *   node harness.mjs <pi|openclaw> --gateway URL \
 *     --address-code ADDR --email E --workspace DIR --home DIR [--domain D]
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
const secondEmail = arg('second-email')
const secondCode = arg('second-code')
const workspace = path.resolve(arg('workspace', path.join(process.cwd(), 'workspace')))
const home = arg('home', path.join(os.tmpdir(), `aimail-host-l2-${Date.now()}`))
const domain = arg('domain')
const systemName = arg('system-name')
const manager = arg('manager')
const delivery = process.argv.includes('--delivery')

if (!['pi', 'openclaw'].includes(host)) { console.error('host must be pi|openclaw'); process.exit(2) }
if (!email || !addressCode) {
  console.error('missing --address-code/--email'); process.exit(2)
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
const sysDetail = { success: true }
const addrExec = host === 'pi'
  ? await address.execute('addr', {
      action: 'bind', email, code: addressCode, gateway_url: gateway,
    }, undefined, { cwd: workspace })
  : await address.execute('addr', {
      action: 'bind', email, code: addressCode, gateway_url: gateway,
    })
const addrDetail = addrExec.details ?? addrExec
console.log(JSON.stringify({ step: 'address-only', result: addrDetail }))
console.log(JSON.stringify({ step: 'address', result: addrDetail }))
if (addrDetail?.success !== true) process.exit(1)
const { loadAgentConfig } = await import(path.join(dist, 'core/index.js'))
const sid = String(addrDetail.system_id ?? '')
const cfg = sid ? await loadAgentConfig(sid, email).catch(() => undefined) : undefined
if (!cfg) { console.error('binding missing'); process.exit(1) }
if (path.resolve(String(cfg.workspace_path ?? '')) !== workspace) {
  console.error(`workspace mismatch: binding=${cfg.workspace_path} want=${workspace}`)
  process.exit(1)
}
if (cfg.webhook_url) { console.error('binding still has webhook_url'); process.exit(1) }
console.log(JSON.stringify({ step: 'assert', ok: true, workspace: cfg.workspace_path, host }))

let cfg2
if (secondEmail) {
  if (!secondCode) { console.error('second-code required with second-email'); process.exit(2) }
  const r2 = host === 'pi'
    ? await address.execute('addr2', { action: 'bind', email: secondEmail, code: secondCode, gateway_url: gateway }, undefined, { cwd: workspace })
    : await address.execute('addr2', { action: 'bind', email: secondEmail, code: secondCode, gateway_url: gateway })
  const d2 = r2.details ?? r2
  if (d2?.success !== true) { console.error('second bind failed: ' + JSON.stringify(d2)); process.exit(1) }
  cfg2 = await loadAgentConfig(String(d2.system_id ?? ''), secondEmail).catch(() => undefined)
  if (!cfg2) { console.error('second binding missing'); process.exit(1) }
}

// Shared-stream plan must cover the activated address with a system stream.
const { planStreams } = await import(path.join(dist, 'runtime/index.js'))
const planAddresses = [{ email, gatewayUrl: cfg.gateway_url, apiKey: cfg.api_key, system_id: sid }]
if (secondEmail) planAddresses.push({ email: secondEmail, gatewayUrl: cfg.gateway_url, apiKey: cfg.api_key, system_id: sid })
const plans = planStreams([], planAddresses)
if (!secondEmail && (plans.length !== 1 || plans[0].scope !== 'address' || !plans[0].emails.includes(email))) {
  console.error('shared stream plan mismatch: ' + JSON.stringify(plans))
  process.exit(1)
}
console.log(JSON.stringify({ step: 'stream-plan', ok: true, plans: plans.length }))

if (delivery) {
  const { startSystemPull, startAgentPullEntries, GatewayClient } = await import(path.join(dist, 'core/index.js'))
  const seen = []
  const agentSystemId = await core.getAgentSystemId()
  const handles = [await startSystemPull({
        systemId: email,
        agentSystemId,
        gatewayUrl: cfg.gateway_url,
        apiKey: cfg.api_key,
        onEmail: async (mail) => {
          seen.push({ id: mail.id, email: mail.email, workspace: cfg.workspace_path })
        },
      })]
  try {
    const client = new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, email)
    const r = await client.request('POST', '/api/v1/system/welcome', { to: [] })
    if (!(r.status >= 200 && r.status < 300)) {
      console.error(`welcome failed: ${r.status} ${JSON.stringify(r)}`)
      process.exit(1)
    }
    if (cfg2) {
      const c2 = new GatewayClient(cfg2.gateway_url, cfg2.api_key, 30_000, secondEmail)
      const r2 = await c2.request('POST', '/api/v1/system/welcome', { to: [] })
      if (!(r2.status >= 200 && r2.status < 300)) {
        console.error(`second welcome failed: ${r2.status} ${JSON.stringify(r2)}`)
        process.exit(1)
      }
    }
    const want = secondEmail ? 2 : 1
    const deadline = Date.now() + 20_000
    while (seen.length < want && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250))
  } finally {
    for (const h of handles) h.stop()
  }
  if (seen.length < (secondEmail ? 2 : 1)) { console.error('no SSE delivery received'); process.exit(1) }
  if (path.resolve(seen[0].workspace) !== workspace) {
    console.error(`delivery workspace mismatch: ${seen[0].workspace} want ${workspace}`)
    process.exit(1)
  }
  console.log(JSON.stringify({ step: 'delivery', ok: true, seen }))
}
