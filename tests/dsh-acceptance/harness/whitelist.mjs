#!/usr/bin/env node
/** Add a from-whitelist entry so an external SMTP sender can reach a mailbox. */
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const DIST = process.env.TSSDK_DIST || path.join(ROOT, 'tssdk/packages/agent-aimail/dist')
const { GatewayClient } = await import(path.join(DIST, 'core/gateway.js'))
const arg = (n, d = '') => { const i = process.argv.indexOf(`--${n}`); return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : d }
const c = new GatewayClient(arg('gateway').replace(/\/+$/, ''), arg('key'), 30_000, arg('identity'))
const r = await c.request('POST', '/api/v1/whitelists', {
  system_id: arg('system'), domain_addr: arg('address'), direction: 'from', value: arg('sender'), description: 'acceptance',
})
console.log(JSON.stringify(r, null, 2))
process.exit(r.status === 200 || r.status === 201 ? 0 : 1)
