/**
 * `activate_address_code` tool wrapper — the wire parameter `profile_home`
 * (hermetic: loopback stand-in gateway, scratch HOME/AIMAIL_HOME).
 *
 * The cross-language parity gate (`tool-registry.test.ts`) compares the
 * parameter **key set** of the TS registry against the Python MCP spec
 * verbatim, so `profile_home` is contract, not style. A key that is
 * advertised but not wired is the classic "declared ≠ implemented" defect —
 * this file proves the wrapper actually consumes it:
 *
 * 1. `profile_home` (and its programmatic alias `platformHome`) reach
 *    `activateAddressCodePersist` and really land the pointer at
 *    `<home>/<POINTER_FILE>` (constant from `contract.ts`, never spelled out);
 * 2. `~` is expanded and a relative home is absolutised before landing;
 * 3. the pointer is 0600 in a 0700 dir, with no `.tmp` left behind;
 * 4. an omitted home skips only the pointer — the binding still lands.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'
import { activateAddressCode } from '../src/tools.js'
import { POINTER_FILE, BINDING_FILE } from '../src/contract.js'

const SID = 'shared_addr_ts123'
const ADDR = 'ts-agent@e2e.local'
const RAW_KEY = 'rk-ts-1'

let home: string
let prevHome: string | undefined
let prevAimail: string | undefined
let prevCwd: string

/** Loopback stand-in for POST /api/v1/activate-address-code. */
function startGateway(body: Record<string, unknown> = {}, status = 200) {
  const seen: Array<{ path: string; body: Record<string, unknown> }> = []
  const srv = http.createServer((req, res) => {
    const chunks: Buffer[] = []
    req.on('data', (c: Buffer) => chunks.push(c))
    req.on('end', () => {
      let parsed: Record<string, unknown> = {}
      try {
        parsed = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}') as Record<string, unknown>
      } catch {
        parsed = {}
      }
      seen.push({ path: req.url ?? '', body: parsed })
      const payload = Buffer.from(JSON.stringify(body))
      res.writeHead(status, { 'Content-Type': 'application/json', 'Content-Length': String(payload.length) })
      res.end(payload)
    })
  })
  return new Promise<{ url: string; seen: typeof seen; close: () => Promise<void> }>((resolve) => {
    srv.listen(0, '127.0.0.1', () => {
      const port = (srv.address() as { port: number }).port
      resolve({
        url: `http://127.0.0.1:${port}`,
        seen,
        close: () => new Promise<void>((r) => srv.close(() => r())),
      })
    })
  })
}

const ok = {
  raw_key: RAW_KEY,
  system_id: SID,
  email_address: ADDR,
  expires_at: '2026-12-31T00:00:00Z',
}

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'mail-core-tools-'))
  prevHome = process.env.HOME
  prevAimail = process.env.AIMAIL_HOME
  prevCwd = process.cwd()
  process.env.HOME = home
  process.env.AIMAIL_HOME = path.join(home, 'aimail-home')
})

afterEach(async () => {
  process.chdir(prevCwd)
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimail === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimail
  await fs.rm(home, { recursive: true, force: true })
})

describe('activate_address_code tool — profile_home', () => {
  it('lands the pointer at <profile_home>/POINTER_FILE (0600, no tmp left)', async () => {
    const gw = await startGateway(ok)
    try {
      const pf = path.join(home, 'deer', '.deer-flow')
      const r = await activateAddressCode({
        code: 'shared_a-ts', address: ADDR, gatewayUrl: gw.url, profile_home: pf,
      })

      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.system_id).toBe(SID)
      expect(r.pointer_written).toBe(true)
      expect(r.pointer_path).toBe(path.join(pf, POINTER_FILE))

      // the wire call is real, and the email is lowercased
      expect(gw.seen).toHaveLength(1)
      expect(gw.seen[0].path).toBe('/api/v1/activate-address-code')
      expect(gw.seen[0].body).toEqual({ code: 'shared_a-ts', email_address: ADDR })

      const ptr = path.join(pf, POINTER_FILE)
      expect(JSON.parse(await fs.readFile(ptr, 'utf8'))).toEqual({ system_id: SID, email: ADDR })
      expect((await fs.stat(ptr)).mode & 0o777).toBe(0o600)
      expect((await fs.stat(pf)).mode & 0o777).toBe(0o700)
      await expect(fs.access(`${ptr}.tmp`)).rejects.toThrow()
    } finally {
      await gw.close()
    }
  })

  it('binding still lands 0600 and carries the agent key', async () => {
    const gw = await startGateway(ok)
    try {
      await activateAddressCode({
        code: 'c', address: ADDR, gatewayUrl: gw.url, profile_home: path.join(home, 'pf'),
      })
      const cfgPath = path.join(process.env.AIMAIL_HOME!, 'systems', SID,
        `${ADDR.split('@')[0]}_e2e.local`, BINDING_FILE)
      const cfg = JSON.parse(await fs.readFile(cfgPath, 'utf8')) as Record<string, unknown>
      expect(cfg.api_key).toBe(RAW_KEY)
      expect(cfg.system_id).toBe(SID)
      expect(cfg.domain).toBe('e2e.local')
      expect(cfg.agent_id).toBe('ts-agent')
      expect((await fs.stat(cfgPath)).mode & 0o777).toBe(0o600)
    } finally {
      await gw.close()
    }
  })

  it('expands a leading ~ and absolutises a relative home', async () => {
    const gw = await startGateway(ok)
    try {
      const r1 = await activateAddressCode({
        code: 'c', address: ADDR, gatewayUrl: gw.url, profile_home: '~/pf',
      })
      expect(r1.pointer_path).toBe(path.join(home, 'pf', POINTER_FILE))
      expect(JSON.parse(await fs.readFile(path.join(home, 'pf', POINTER_FILE), 'utf8')))
        .toEqual({ system_id: SID, email: ADDR })

      process.chdir(home)
      const r2 = await activateAddressCode({
        code: 'c', address: ADDR, gatewayUrl: gw.url, platformHome: 'relhome',
      })
      expect(r2.pointer_path).toBe(path.join(home, 'relhome', POINTER_FILE))
      expect(JSON.parse(await fs.readFile(path.join(home, 'relhome', POINTER_FILE), 'utf8')))
        .toEqual({ system_id: SID, email: ADDR })
    } finally {
      await gw.close()
    }
  })

  it('omitted home skips the pointer but keeps the binding', async () => {
    const gw = await startGateway(ok)
    try {
      const r = await activateAddressCode({ code: 'c', address: ADDR, gatewayUrl: gw.url })
      expect(r.success).toBe(true)
      expect(r.pointer_written).toBe(false)
      expect(r.pointer_path).toBe('')
      await expect(
        fs.access(path.join(process.env.AIMAIL_HOME!, 'systems', SID,
          `${ADDR.split('@')[0]}_e2e.local`, BINDING_FILE)),
      ).resolves.toBeUndefined()
    } finally {
      await gw.close()
    }
  })

  it('a rejected code persists nothing (no binding, no pointer)', async () => {
    const gw = await startGateway({ error: 'code expired' }, 400)
    try {
      const pf = path.join(home, 'pf')
      const r = await activateAddressCode({
        code: 'bad', address: ADDR, gatewayUrl: gw.url, profile_home: pf,
      })
      expect(r.success).toBe(false)
      await expect(fs.access(path.join(pf, POINTER_FILE))).rejects.toThrow()
      await expect(fs.access(path.join(process.env.AIMAIL_HOME!, 'systems', SID))).rejects.toThrow()
    } finally {
      await gw.close()
    }
  })

  it('the local binding enables the agent-scope pull path (sid prefix)', async () => {
    const gw = await startGateway(ok)
    try {
      const r = await activateAddressCode({
        code: 'c', address: ADDR, gatewayUrl: gw.url, profile_home: path.join(home, 'pf'),
      })
      // `shared_addr_` is the prefix `isAgentScopeBinding` keys on (pull entry)
      expect(String(r.system_id).startsWith('shared_addr_')).toBe(true)
    } finally {
      await gw.close()
    }
  })
})
