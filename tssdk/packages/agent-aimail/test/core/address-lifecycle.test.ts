/**
 * `aimailAddress(action=...)` lifecycle — the single agent-facing entry
 * (parity: pysdk `aimail_address`). Hermetic: loopback stand-in gateway,
 * scratch HOME/AIMAIL_HOME.
 *
 * Mirrors the G-domain acceptance lines:
 * - bind ① idempotent: second bind = 0 gateway calls, file untouched,
 *   pointer (re)written, a supplied code is NOT consumed;
 * - bind ② restore: archive ⇒ rename back, 0 gateway calls + pointer;
 * - bind ③ cloud: one gateway call, register-isomorphic persist;
 * - pause/resume: local flag only (0 gateway calls); resume re-fires the
 *   in-process hot-add hook;
 * - unbind: archive (key bytes survive, 0600) + pointer cleared only on
 *   email match; second unbind idempotent (already-archived);
 * - no local trace + no code ⇒ not-bound (never guessed);
 * - bad action / missing email ⇒ explicit failure.
 */
import { describe, it, expect, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import * as http from 'node:http'
import * as os from 'node:os'
import * as path from 'node:path'
import { aimailAddressTool } from '../../src/core/tools.js'
import { setProfileDirResolver, BINDING_ARCHIVED_SUFFIX } from '../../src/core/address-code.js'
import { onBindingCreated } from '../../src/core/binding-hooks.js'
import { POINTER_FILE, BINDING_FILE } from '../../src/core/contract.js'

const SID = 'shared_addr_ts123'
const ADDR = 'ts-agent@e2e.local'
const RAW_KEY = 'rk-ts-lifecycle'

let home: string
let prevHome: string | undefined
let prevAimail: string | undefined
let prevGwUrl: string | undefined

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

/** The active binding path under the scratch home. */
function bindingPath(): string {
  return path.join(process.env.AIMAIL_HOME!, 'systems', SID,
    `${ADDR.split('@')[0]}_e2e.local`, BINDING_FILE)
}

async function sha256(p: string): Promise<string> {
  // crypto-free: a stable byte fingerprint is enough for an unchanged-file assertion
  const buf = await fs.readFile(p)
  let h = 0
  for (const b of buf) h = (h * 31 + b) >>> 0
  return `${buf.length}:${h}`
}

beforeEach(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'mail-core-lifecycle-'))
  prevHome = process.env.HOME
  prevAimail = process.env.AIMAIL_HOME
  prevGwUrl = process.env.AIMAIL_GW_URL
  process.env.HOME = home
  process.env.AIMAIL_HOME = path.join(home, 'aimail-home')
  setProfileDirResolver(undefined)
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimail === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimail
  if (prevGwUrl === undefined) delete process.env.AIMAIL_GW_URL
  else process.env.AIMAIL_GW_URL = prevGwUrl
  setProfileDirResolver(undefined)
  await fs.rm(home, { recursive: true, force: true })
})

describe('aimailAddress — input validation', () => {
  it('bad-action is explicit', async () => {
    const r = await aimailAddressTool({ action: 'destroy', email: ADDR })
    expect(r.success).toBe(false)
    expect(r.reason).toBe('bad-action')
  })

  it('missing-email is explicit', async () => {
    const r = await aimailAddressTool({ action: 'bind' })
    expect(r.success).toBe(false)
    expect(r.reason).toBe('missing-email')
  })
})

describe('aimailAddress — bind', () => {
  it('③ cloud activation: one gateway call, binding persists, pointer lands', async () => {
    const gw = await startGateway(ok)
    try {
      const pf = path.join(home, 'pf')
      const r = await aimailAddressTool({
        action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url, profile_home: pf,
      })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.system_id).toBe(SID)
      expect(gw.seen).toHaveLength(1)
      expect(gw.seen[0].path).toBe('/api/v1/activate-address-code')
      expect(gw.seen[0].body).toEqual({ code: 'shared_a-ts', email_address: ADDR })

      const bp = bindingPath()
      const cfg = JSON.parse(await fs.readFile(bp, 'utf8')) as Record<string, unknown>
      expect(cfg.api_key).toBe(RAW_KEY)
      expect((await fs.stat(bp)).mode & 0o777).toBe(0o600)

      const ptr = path.join(pf, POINTER_FILE)
      expect(JSON.parse(await fs.readFile(ptr, 'utf8'))).toEqual({ system_id: SID, email: ADDR })
    } finally {
      await gw.close()
    }
  })

  it('① idempotent: second bind = 0 gateway calls, file untouched, pointer (re)written', async () => {
    const gw = await startGateway(ok)
    try {
      const pf = path.join(home, 'pf')
      const first = await aimailAddressTool({
        action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url, profile_home: pf,
      })
      expect(first.success, JSON.stringify(first)).toBe(true)
      const hash1 = await sha256(bindingPath())

      // simulate a deleted pointer → the bind must self-heal it
      await fs.unlink(path.join(pf, POINTER_FILE))

      const second = await aimailAddressTool({
        action: 'bind', email: ADDR, profile_home: pf,
      })
      expect(second.success).toBe(true)
      expect(second.reason).toBe('already_bound')
      expect(second.gateway_calls).toBe(0)
      // exactly one gateway call for the whole session (the first bind)
      expect(gw.seen).toHaveLength(1)
      expect(await sha256(bindingPath())).toBe(hash1)
      expect(second.pointer_written).toBe(true)
      expect(second.pointer_path).toBe(path.join(pf, POINTER_FILE))
    } finally {
      await gw.close()
    }
  })

  it('① a supplied code with an existing binding is refused as already_bound (unconsumed)', async () => {
    const gw = await startGateway(ok)
    try {
      await aimailAddressTool({ action: 'bind', code: 'shared_a-1', email: ADDR, gateway_url: gw.url })
      const r = await aimailAddressTool({
        action: 'bind', code: 'shared_a-2', email: ADDR, gateway_url: gw.url,
      })
      expect(r.success).toBe(true)
      expect(r.reason).toBe('already_bound')
      expect(gw.seen).toHaveLength(1) // the second code never reached the gateway
    } finally {
      await gw.close()
    }
  })

  it('② restore from the archive: no code, 0 gateway calls, pointer lands', async () => {
    const gw = await startGateway(ok)
    try {
      await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url })
      await aimailAddressTool({ action: 'unbind', email: ADDR })
      expect(gw.seen).toHaveLength(1) // unbind made no gateway call

      const pf = path.join(home, 'pf')
      const r = await aimailAddressTool({ action: 'bind', email: ADDR, profile_home: pf })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.reason).toBe('restored')
      expect(r.gateway_calls).toBe(0)
      expect(gw.seen).toHaveLength(1) // restore made no gateway call either
      expect(r.pointer_written).toBe(true)

      const cfg = JSON.parse(await fs.readFile(bindingPath(), 'utf8')) as Record<string, unknown>
      expect(cfg.api_key).toBe(RAW_KEY)
    } finally {
      await gw.close()
    }
  })

  it('no local trace + no code ⇒ not-bound (never guessed)', async () => {
    const r = await aimailAddressTool({ action: 'bind', email: ADDR })
    expect(r.success).toBe(false)
    expect(r.reason).toBe('not-bound')
  })

  it('no local trace + code but no gateway URL ⇒ gateway-url-required', async () => {
    const r = await aimailAddressTool({ action: 'bind', code: 'shared_a-x', email: ADDR })
    expect(r.success).toBe(false)
    expect(r.reason).toBe('gateway-url-required')
  })

  it('gateway URL falls back to AIMAIL_GW_URL', async () => {
    const gw = await startGateway(ok)
    try {
      process.env.AIMAIL_GW_URL = gw.url
      const r = await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(gw.seen).toHaveLength(1)
    } finally {
      await gw.close()
    }
  })

  it('the profile-dir resolver owns the pointer when profileHome is omitted', async () => {
    const gw = await startGateway(ok)
    try {
      const pf = path.join(home, 'resolved')
      setProfileDirResolver(() => pf)
      const r = await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.pointer_written).toBe(true)
      expect(r.pointer_path).toBe(path.join(pf, POINTER_FILE))
    } finally {
      await gw.close()
    }
  })

  it('an explicit profileHome wins over the resolver', async () => {
    const gw = await startGateway(ok)
    try {
      setProfileDirResolver(() => path.join(home, 'resolver-should-lose'))
      const explicit = path.join(home, 'explicit')
      const r = await aimailAddressTool({
        action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url, profile_home: explicit,
      })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.pointer_path).toBe(path.join(explicit, POINTER_FILE))
    } finally {
      await gw.close()
    }
  })

  it('no resolver + no explicit home ⇒ no pointer side effect (binding still lands)', async () => {
    const gw = await startGateway(ok)
    try {
      const r = await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.pointer_written).toBe(false)
      expect(r.pointer_path).toBe('')
    } finally {
      await gw.close()
    }
  })
})

describe('aimailAddress — pause / resume', () => {
  async function bindViaCode (url: string): Promise<void> {
    const r = await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: url })
    expect(r.success, JSON.stringify(r)).toBe(true)
  }

  it('pause flips the local flag only; idempotent; unbind-then-pause ⇒ not-bound', async () => {
    const gw = await startGateway(ok)
    try {
      await bindViaCode(gw.url)
      const bp = bindingPath()

      const p1 = await aimailAddressTool({ action: 'pause', email: ADDR })
      expect(p1.success).toBe(true)
      expect(p1.reason).toBe('paused')
      expect(gw.seen).toHaveLength(1) // pause made no gateway call

      let cfg = JSON.parse(await fs.readFile(bp, 'utf8')) as Record<string, unknown>
      expect(cfg.paused).toBe(true)

      const p2 = await aimailAddressTool({ action: 'pause', email: ADDR })
      expect(p2.success).toBe(true)
      expect(p2.reason).toBe('already-paused')

      const un = await aimailAddressTool({ action: 'unbind', email: ADDR })
      expect(un.success).toBe(true)
      expect(un.reason).toBe('archived')

      const p3 = await aimailAddressTool({ action: 'pause', email: ADDR })
      expect(p3.success).toBe(false)
      expect(p3.reason).toBe('not-bound')
    } finally {
      await gw.close()
    }
  })

  it('resume clears the flag (0 gateway calls) and re-fires the hot-add hook', async () => {
    const gw = await startGateway(ok)
    try {
      let hotAdds = 0
      const off = onBindingCreated(async () => { hotAdds++ })
      try {
        await bindViaCode(gw.url)
        const afterBind = hotAdds // the bind persist chain already fired once
        expect(afterBind).toBe(1)

        await aimailAddressTool({ action: 'pause', email: ADDR })
        const r = await aimailAddressTool({ action: 'resume', email: ADDR })
        expect(r.success, JSON.stringify(r)).toBe(true)
        expect(r.reason).toBe('resumed')
        expect(hotAdds).toBe(afterBind + 1) // the resume re-fired the hook
        expect(gw.seen).toHaveLength(1) // resume made no gateway call

        const cfg = JSON.parse(await fs.readFile(bindingPath(), 'utf8')) as Record<string, unknown>
        expect(cfg.paused).toBeUndefined()

        const r2 = await aimailAddressTool({ action: 'resume', email: ADDR })
        expect(r2.success).toBe(true)
        expect(r2.reason).toBe('already-active')
      } finally {
        off()
      }
    } finally {
      await gw.close()
    }
  })

  it('pause/resume on a missing binding ⇒ not-bound', async () => {
    const r = await aimailAddressTool({ action: 'resume', email: ADDR })
    expect(r.success).toBe(false)
    expect(r.reason).toBe('not-bound')
  })
})

describe('aimailAddress — unbind', () => {
  it('archives (key bytes survive, 0600), clears the matching pointer, idempotent', async () => {
    const gw = await startGateway(ok)
    try {
      const pf = path.join(home, 'pf')
      await aimailAddressTool({
        action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url, profile_home: pf,
      })
      expect(gw.seen).toHaveLength(1)

      const r = await aimailAddressTool({ action: 'unbind', email: ADDR, profile_home: pf })
      expect(r.success, JSON.stringify(r)).toBe(true)
      expect(r.reason).toBe('archived')
      expect(gw.seen).toHaveLength(1) // unbind made no gateway call
      expect(r.pointer_cleared).toBe(true)

      // active file gone, archive present with the key intact
      await expect(fs.access(bindingPath())).rejects.toThrow()
      const arc = bindingPath() + BINDING_ARCHIVED_SUFFIX
      const cfg = JSON.parse(await fs.readFile(arc, 'utf8')) as Record<string, unknown>
      expect(cfg.api_key).toBe(RAW_KEY)
      expect((await fs.stat(arc)).mode & 0o777).toBe(0o600)
      await expect(fs.access(path.join(pf, POINTER_FILE))).rejects.toThrow()

      // second unbind: no active binding ⇒ not-bound + the archive hint;
      // the archive is never destroyed (parity: py aimail_address unbind)
      const r2 = await aimailAddressTool({ action: 'unbind', email: ADDR, profile_home: pf })
      expect(r2.success).toBe(false)
      expect(r2.reason).toBe('not-bound')
      expect(String(r2.error)).toContain('an archived binding exists')
      await expect(fs.access(arc)).resolves.toBeUndefined()
    } finally {
      await gw.close()
    }
  })

  it('never clears a pointer that belongs to another mailbox', async () => {
    const gw = await startGateway(ok)
    try {
      const pf = path.join(home, 'pf')
      await aimailAddressTool({
        action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url, profile_home: pf,
      })
      // overwrite the pointer with a FOREIGN mailbox (the profile now "is" someone else)
      const foreign = { system_id: 'shared_addr_other', email: 'other@e2e.local' }
      await fs.writeFile(path.join(pf, POINTER_FILE), JSON.stringify(foreign, null, 2) + '\n', { mode: 0o600 })

      const r = await aimailAddressTool({ action: 'unbind', email: ADDR, profile_home: pf })
      expect(r.success).toBe(true)
      expect(r.reason).toBe('archived')
      expect(r.pointer_cleared).toBe(false)
      expect(JSON.parse(await fs.readFile(path.join(pf, POINTER_FILE), 'utf8'))).toEqual(foreign)
    } finally {
      await gw.close()
    }
  })

  it('R2 gate: the helper refuses to archive a file whose own email differs (exact match)', async () => {
    const gw = await startGateway(ok)
    try {
      await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url })
      // plant a foreign binding's email field inside the SAME binding dir
      // (discovery would already refuse it at the tool level; the R2 gate is
      // the helper-level defence that the rename never trusts the path alone)
      const bp = bindingPath()
      const foreign = {
        email: 'impostor@e2e.local', gateway_url: 'http://x', domain: 'e2e.local',
        system_id: SID, api_key: 'rk-foreign', agent_id: 'impostor',
      }
      await fs.writeFile(bp, JSON.stringify(foreign, null, 2) + '\n', { mode: 0o600 })
      const { archiveBinding } = await import('../../src/core/address-code.js')
      const r = await archiveBinding(bp, ADDR)
      expect(r.success).toBe(false)
      expect(r.reason).toBe('email-mismatch')
      // the foreign binding is untouched
      expect((JSON.parse(await fs.readFile(bp, 'utf8')) as { api_key: string }).api_key).toBe('rk-foreign')
    } finally {
      await gw.close()
    }
  })

  it('a foreign email in the binding dir is refused as not-bound at the tool level (discovery filter)', async () => {
    const gw = await startGateway(ok)
    try {
      await aimailAddressTool({ action: 'bind', code: 'shared_a-ts', email: ADDR, gateway_url: gw.url })
      const bp = bindingPath()
      const foreign = {
        email: 'impostor@e2e.local', gateway_url: 'http://x', domain: 'e2e.local',
        system_id: SID, api_key: 'rk-foreign', agent_id: 'impostor',
      }
      await fs.writeFile(bp, JSON.stringify(foreign, null, 2) + '\n', { mode: 0o600 })
      const r = await aimailAddressTool({ action: 'unbind', email: ADDR })
      expect(r.success).toBe(false)
      expect(r.reason).toBe('not-bound')
      // the foreign binding is untouched
      expect((JSON.parse(await fs.readFile(bp, 'utf8')) as { api_key: string }).api_key).toBe('rk-foreign')
    } finally {
      await gw.close()
    }
  })
})
