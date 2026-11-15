#!/usr/bin/env node
/** Create a base domain with the bootstrap admin key (signed request). */
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../..')
const DIST = process.env.TSSDK_DIST || path.join(ROOT, 'tssdk/packages/agent-aimail/dist')
const { GatewayClient } = await import(path.join(DIST, 'core/gateway.js'))

const arg = (name, fallback = '') => {
  const i = process.argv.indexOf(`--${name}`)
  return i >= 0 && process.argv[i + 1] ? process.argv[i + 1] : fallback
}
const gateway = arg('gateway', 'http://127.0.0.1:18080').replace(/\/+$/, '')
const key = arg('key')
const identity = arg('identity')
const system = arg('system')
const domain = arg('domain')
if (!key || !identity || !system || !domain) {
  console.error('usage: admin.mjs --gateway URL --key ADMIN --identity SID --system SID --domain D')
  process.exit(2)
}
const c = new GatewayClient(gateway, key, 30_000, identity)
const r = await c.request('POST', `/api/v1/admin/systems/${encodeURIComponent(system)}/domains`, {
  id: `dom-${domain.replace(/[^a-z0-9.-]/gi, '-')}`,
  domain,
  manager_address: arg('manager') || undefined,
})
console.log(JSON.stringify(r, null, 2))
process.exit(r.status === 200 || r.status === 201 ? 0 : 1)
