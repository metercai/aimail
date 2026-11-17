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

if (!['pi', 'openclaw'].includes(host)) { console.error('host must be pi|openclaw'); process.exit(2) }
if (!systemCode || !addressCode || !email) {
  console.error('missing --system-code/--address-code/--email'); process.exit(2)
}
await fs.mkdir(path.join(home, 'aimail'), { recursive: true })
await fs.mkdir(workspace, { recursive: true })
process.env.AIMAIL_HOME = path.join(home, 'aimail')
process.env.HOME = home
if (host === 'pi') process.env.AIMAIL_TRANSPORT = 'sse'
else process.env.AIMAIL_TRANSPORT = 'sse'

const dist = process.env.TSSDK_DIST ?? path.resolve(import.meta.dirname, '../../tssdk/packages/agent-aimail/dist')

async function toolMap() {
  if (host === 'pi') {
    const { buildPiTools } = await import(path.join(dist, 'pi/tools.js'))
    return new Map(buildPiTools().map((t) => [t.name, t]))
  }
  const { createMailTools } = await import(path.join(dist, 'openclaw/tools.js'))
  return new Map(createMailTools({ agentId: 'main' }, () => workspace).map((t) => [t.name, t]))
}

const tools = await toolMap()
const system = tools.get('aimail_system')
const address = tools.get('aimail_address')
if (!system || !address) { console.error('activation tools missing'); process.exit(1) }

const sysArgs = { action: 'bind', code: systemCode, gateway_url: gateway }
if (domain) sysArgs.domain = domain
if (systemName) sysArgs.system_name = systemName
if (manager) sysArgs.manager = manager
const sys = host === 'pi'
  ? await system.execute('sys', sysArgs, undefined, { cwd: workspace })
  : await system.execute('sys', sysArgs)
const sysDetail = sys.details ?? sys
console.log(JSON.stringify({ step: 'system', result: sysDetail }))
if (sysDetail?.success !== true) process.exit(1)

const addr = host === 'pi'
  ? await address.execute('addr', { action: 'bind', email, code: addressCode, gateway_url: gateway }, undefined, { cwd: workspace })
  : await address.execute('addr', { action: 'bind', email, code: addressCode, gateway_url: gateway })
const addrDetail = addr.details ?? addr
console.log(JSON.stringify({ step: 'address', result: addrDetail }))
if (addrDetail?.success !== true) process.exit(1)

// Binding is the contract: workspace must be the activation workspace.
const { loadAgentConfig } = await import(path.join(dist, 'core/index.js'))
const sid = String(sysDetail.system_id ?? '')
const cfg = sid ? await loadAgentConfig(sid, email).catch(() => undefined) : undefined
if (!cfg) { console.error('binding missing'); process.exit(1) }
if (path.resolve(String(cfg.workspace_path ?? '')) !== workspace) {
  console.error(`workspace mismatch: binding=${cfg.workspace_path} want=${workspace}`)
  process.exit(1)
}
if (cfg.webhook_url) { console.error('binding still has webhook_url'); process.exit(1) }
console.log(JSON.stringify({ step: 'assert', ok: true, workspace: cfg.workspace_path, host }))
