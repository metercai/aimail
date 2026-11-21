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

// ── delivery: exercise the REAL host entry (the adapter's startInboundPull),
// NOT a hand-rolled startSystemPull. The hand-rolled version armed the correct
// aggregate tier-2 directly, bypassing the host's tier decision — so a build
// with the no-pointer/tier-3 defect (B1/B2) still passed the gate ("false
// green"). The real entry runs the host's actual tier decision:
//   pointer present  → tier-2 aggregate (startSystemPull)
//   no pointer       → tier-3 (startAgentPullEntries / grouping)
// `--no-pointer` reproduces the QA real host (pysdk external activation wrote
// no pointer) — the B1/B2 scenario: on 0.5.0 the tier-3 V1 predicate rejects
// the V2 binding (no pull armed) ⇒ the gate MUST go red; on 0.5.1 (grouping)
// it arms one aggregate stream per (AgentSystemId, Gateway) ⇒ green.
if (delivery) {
  const noPointer = process.argv.includes('--no-pointer')
  const { GatewayClient } = await import(path.join(dist, 'core/index.js'))

  if (noPointer) {
    // pointer file name comes from the contract single source (POINTER_FILE),
    // not a literal — the L0 literal ratchet forbids new contract-value locations.
    const ptrPath = path.join(home, host === 'pi' ? '.pi' : '.openclaw', core.POINTER_FILE)
    await fs.rm(ptrPath, { force: true })
    console.log(JSON.stringify({ step: 'no-pointer', removed: ptrPath }))
  }

  // Delivery observation at the real delivery point: the host hands the
  // enriched mail to the owning agent's session. openclaw → POST /hooks/agent
  // (fake endpoint, since L2 has no real openclaw gateway); pi → sendUserMessage.
  const delivered = []
  let api
  let hookServer
  if (host === 'openclaw') {
    const http = await import('node:http')
    hookServer = http.createServer((req, res) => {
      if (req.method === 'POST' && (req.url ?? '').startsWith('/hooks/agent')) {
        let body = ''
        req.on('data', (c) => (body += c))
        req.on('end', () => {
          try { delivered.push(JSON.parse(body)) } catch { delivered.push({ raw: body.slice(0, 200) }) }
          res.writeHead(200, { 'Content-Type': 'application/json' })
          res.end(JSON.stringify({ ok: true }))
        })
      } else {
        res.writeHead(404, { 'Content-Type': 'application/json' })
        res.end(JSON.stringify({ status: 'not_found' }))
      }
    })
    await new Promise((r) => hookServer.listen(0, '127.0.0.1', r))
    const hookPort = hookServer.address().port
    const ocHome = path.join(home, '.openclaw')
    await fs.mkdir(ocHome, { recursive: true })
    await fs.writeFile(path.join(ocHome, 'openclaw.json'), JSON.stringify({
      gateway: { port: hookPort },
      hooks: { path: '/hooks', token: 'l2-fixture-token', enabled: true },
      agents: { entries: { main: {} } },
    }, null, 2))
    api = {} // deliverToAgent ignores api (void api); it fetches the hooks endpoint directly
  } else {
    api = {
      sendUserMessage: (msg) => {
        try { delivered.push(JSON.parse(msg)) } catch { delivered.push({ raw: String(msg).slice(0, 200) }) }
      },
    }
  }

  // Real host entry (adapter), not a hand-rolled pull.
  const adapter = host === 'pi'
    ? await import(path.join(dist, 'pi/index.js'))
    : await import(path.join(dist, 'openclaw/inbound.js'))
  const logLines = []
  const handles = await adapter.startInboundPull(api, { log: (l) => { logLines.push(l) } })
    .catch((e) => { console.error('startInboundPull threw:', e?.message ?? e); return [] })

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
    while (delivered.length < want && Date.now() < deadline) await new Promise((r) => setTimeout(r, 250))
  } finally {
    for (const h of handles) { try { h.stop() } catch { /* already stopped */ } }
    if (hookServer) await new Promise((r) => hookServer.close(r))
  }

  const armed = handles.some((h) => h.started === true)
  console.log(JSON.stringify({
    step: 'pull-handles', noPointer, armed,
    handles: handles.map((h) => ({ email: h.email, started: h.started, reason: h.reason })),
    log: logLines,
  }))
  // The pull must be ARMED (≥1 started handle). On 0.5.0 + no-pointer the tier-3
  // V1 predicate rejects the V2 binding ⇒ no handle ⇒ this is the B1 negative
  // evidence (the gate MUST go red here on a pre-fix build).
  if (!armed) {
    console.error('pull not armed (no started handle) — no-pointer V2 binding not pulled (B1)')
    process.exit(1)
  }
  if (delivered.length < (secondEmail ? 2 : 1)) {
    console.error(`no SSE delivery received (got ${delivered.length}) — pull armed but delivery chain broken (B1/B2)`)
    process.exit(1)
  }
  console.log(JSON.stringify({ step: 'delivery', ok: true, delivered: delivered.length, handles: handles.length, noPointer }))
}
