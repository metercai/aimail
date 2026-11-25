import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import * as fs from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

import { BINDING_FILE } from '../../src/core/contract.js'
import { aimailAddress } from '../../src/core/address-code.js'
import { setAddressModel } from '../../src/core/host-capabilities.js'
import { setProfileDirResolver } from '../../src/core/index.js'

/**
 * Contract §11.3 / C1 — a single-address host must REFUSE a second address
 * *before* any gateway call, so the one-time activation code is not consumed.
 *
 * The failure mode this guards: without the guard the second activation silently
 * shadows the first (same agent slot), and the user loses the code for a mailbox
 * the host can never serve.
 */

const SID = 'system-s1'
const FIRST = 'first@test.local'
const SECOND = 'second@test.local'

let home: string
let prevHome: string | undefined
let prevAimail: string | undefined

function bindingFile(email: string): string {
  return path.join(process.env.AIMAIL_HOME!, 'systems', SID, email.replace('@', '_'), BINDING_FILE)
}

function seedActive(email: string, extra: Record<string, unknown> = {}) {
  const file = bindingFile(email)
  fs.mkdirSync(path.dirname(file), { recursive: true })
  fs.writeFileSync(
    file,
    JSON.stringify({ email, system_id: SID, gateway_url: 'http://gw.invalid', api_key: 'k-1', ...extra }),
    'utf-8',
  )
}

beforeEach(() => {
  home = fs.mkdtempSync(path.join(os.tmpdir(), 'aimail-single-'))
  prevHome = process.env.HOME
  prevAimail = process.env.AIMAIL_HOME
  process.env.HOME = home
  process.env.AIMAIL_HOME = path.join(home, 'aimail-home')
  setProfileDirResolver(undefined)
})

afterEach(() => {
  if (prevHome === undefined) delete process.env.HOME
  else process.env.HOME = prevHome
  if (prevAimail === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevAimail
  setAddressModel(undefined)
  setProfileDirResolver(undefined)
  fs.rmSync(home, { recursive: true, force: true })
})

describe('single-address host (pi)', () => {
  it('refuses a second address and reports the one already held', async () => {
    seedActive(FIRST)
    setAddressModel('single')
    const res = await aimailAddress({ action: 'bind', email: SECOND, code: 'addr-1111-2222-3333' })
    expect(res.success).toBe(false)
    expect(res.reason).toBe('second-address')
    expect(res.error_code).toBe('SINGLE_ADDRESS_HOST')
    // Contract C18: the refusal says WHICH layer failed, not just "refused".
    expect(res.failed).toBe('wake')
    expect(String(res.error)).toContain(FIRST)
    // The refusal must not have created anything for the second address.
    expect(fs.existsSync(bindingFile(SECOND))).toBe(false)
  })

  it('still allows re-binding the address it already holds (idempotent)', async () => {
    seedActive(FIRST)
    setAddressModel('single')
    const res = await aimailAddress({ action: 'bind', email: FIRST })
    expect(res.success).toBe(true)
    expect(res.reason).toBe('already_bound')
  })

  it('lets a multi-address host take a second address', async () => {
    seedActive(FIRST)
    setAddressModel('multi')
    // No gateway is reachable here, so activation cannot complete — what matters is
    // that the single-address guard did NOT fire.
    const res = await aimailAddress({ action: 'bind', email: SECOND, code: 'addr-9999-8888-7777' })
    expect(res.reason).not.toBe('second-address')
  })
})
