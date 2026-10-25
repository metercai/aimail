/**
 * Defect 3 (ruling 2026-10-25): persona capability is a host-adapter hook, not a
 * hardcoded branch in the shared chain.
 *
 * The TS core must mirror the Python `PERSONA_SUPPORTED` + `list_personas()`
 * semantics exactly:
 *   - persona present + host does NOT support persona (default / dsh contract)
 *       ⇒ my_aimail_addr = base address (strip persona prefix, no validation)
 *   - persona present + host supports persona + persona is configured
 *       ⇒ my_aimail_addr = the persona address (kept)
 *   - persona present + host supports persona + persona NOT configured
 *       ⇒ warn + my_aimail_addr = base address
 *
 * Acceptance (QA): for the SAME payload (persona address / display name /
 * multi-recipient), py and ts must produce value-equal
 * my_aimail_addr / direct_message / mentioned / recipients / sender.
 * When a ts host declares persona=false the behavior equals the pre-change
 * (always base) behavior.
 *
 * Cross-language parity: the repo pattern (tool-registry.test.ts) — exec the
 * sibling pysdk and diff field-for-field. Skipped when pysdk / python3 is
 * unavailable.
 */
import { describe, it, expect, beforeAll, afterAll, beforeEach, vi } from 'vitest'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { fileURLToPath } from 'node:url'
import { execFileSync } from 'node:child_process'
import { existsSync } from 'node:fs'
import { processInboundMail, setPersonaSupport, setPersonasProvider } from '../../src/core/preprocess.js'
import { cleanAddr } from '../../src/core/config.js'
import { BINDING_FILE } from '../../src/core/contract.js'
import type { InboundPayload } from '../../src/core/types.js'

const SYSTEM_ID = 'sys-p3'
const EMAIL = 'alice@agent.com'
const DOMAIN = 'agent.com'
const PERSONA_ADDR = 'support.alice@agent.com'
const CTX = { systemId: SYSTEM_ID, email: EMAIL }
const BODY = 'Hi Alice, could you look at this?'

let home: string

/** The five QA-named fields, normalized to a comparable shape. */
function pick5(r: Record<string, unknown>) {
  return {
    my_aimail_addr: r.my_aimail_addr,
    direct_message: r.direct_message,
    mentioned: r.mentioned,
    recipients: r.recipients,
    sender: r.sender,
  }
}

const FIELDS = ['my_aimail_addr', 'direct_message', 'mentioned', 'recipients', 'sender'] as const

/** Repo root = 5 levels above test/core (…/tssdk/packages/<pkg>/test/core). */
function repoRoot(): string {
  const here = path.dirname(fileURLToPath(import.meta.url))
  return path.resolve(here, '..', '..', '..', '..', '..')
}

/** Whether the sibling pysdk is importable for parity. */
function pysdkAvailable(): boolean {
  return existsSync(path.join(repoRoot(), 'pysdk', 'aimail_base.py'))
}

beforeAll(async () => {
  home = await fs.mkdtemp(path.join(os.tmpdir(), 'aimail-p3-'))
  process.env.AIMAIL_HOME = home
  // binding WITHOUT api_key ⇒ the B1 batch-profile step is skipped on both
  // sides (no network in the parity test).
  const dir = path.join(home, 'systems', SYSTEM_ID, cleanAddr(EMAIL))
  await fs.mkdir(dir, { recursive: true, mode: 0o700 })
  await fs.writeFile(
    path.join(dir, BINDING_FILE),
    JSON.stringify(
      { email: EMAIL, gateway_url: 'http://127.0.0.1:9', domain: DOMAIN, system_id: SYSTEM_ID, session_id: 's-1' },
      null,
      2,
    ) + '\n',
    { mode: 0o600 },
  )
  // stub fetch so any incidental network call is inert (B1 is skipped anyway)
  vi.stubGlobal('fetch', vi.fn(async () => new Response('{}', { status: 200 })))
})

beforeEach(() => {
  setPersonaSupport(null)
  setPersonasProvider(null)
})

afterAll(async () => {
  vi.unstubAllGlobals()
  await fs.rm(home, { recursive: true, force: true })
  delete process.env.AIMAIL_HOME
})

function mail(to: string[], toHeader: string): InboundPayload {
  return {
    mail_id: 'm-p3',
    message_id: '<m-p3@x>',
    subject: 'hi',
    body: BODY,
    from: 'peer@example.com',
    to,
    cc: [],
    headers: { from: 'Peer <peer@example.com>', to: toHeader },
  }
}

/** Run the TS chain for one persona-config case. */
async function tsRun(support: boolean, personas: string[] | null, to: string[], toHeader: string) {
  setPersonaSupport(support ? () => true : null)
  setPersonasProvider(personas ? () => personas : null)
  const r = await processInboundMail(mail(to, toHeader), {}, CTX)
  return pick5(r as Record<string, unknown>)
}

/** Run the Python chain for the same case; returns the same 5-field shape. */
function pyRun(
  support: boolean,
  personas: Record<string, unknown> | null,
  to: string[],
  toHeader: string,
): Record<string, unknown> {
  const caseCfg = JSON.stringify({
    support,
    personas,
    to,
    toHeader,
    home,
    email: EMAIL,
    domain: DOMAIN,
    sid: SYSTEM_ID,
    body: BODY,
  })
  // the helper runs from stdin (no .py file lands in tssdk/ — the language
  // boundary gate forbids python files in the TS tree)
  const out = execFileSync('python3', ['-', caseCfg], {
    input: PY_HELPER,
    encoding: 'utf-8',
    timeout: 30_000,
    env: { ...process.env, AIMAIL_HOME: home, AIMAIL_REPO_ROOT: repoRoot() },
  })
  return JSON.parse(out.trim())
}

describe('defect 3: persona capability = adapter hook (py↔ts parity)', () => {
  const pyOk = pysdkAvailable()
  if (!pyOk) {
    it.skip('pysdk available for parity', () => {})
  }

  const PERSONA_TO = [PERSONA_ADDR]
  const PERSONA_HDR = `Support Alice <${PERSONA_ADDR}>`

  it('persona present, host persona=false (default) ⇒ base address, == Python PERSONA_SUPPORTED=False', async () => {
    const ts = await tsRun(false, null, PERSONA_TO, PERSONA_HDR)
    // current (pre-change) behavior for a persona=false host: always base
    expect(ts.my_aimail_addr).toBe(EMAIL)
    if (pyOk) {
      const py = pyRun(false, null, PERSONA_TO, PERSONA_HDR)
      for (const f of FIELDS) expect(ts[f], `${f} py↔ts`).toEqual(py[f])
    }
  })

  it('persona present, host persona=true + persona configured ⇒ persona address KEPT, == Python', async () => {
    const ts = await tsRun(true, ['support'], PERSONA_TO, PERSONA_HDR)
    expect(ts.my_aimail_addr).toBe(PERSONA_ADDR)
    if (pyOk) {
      const py = pyRun(true, { support: {} }, PERSONA_TO, PERSONA_HDR)
      for (const f of FIELDS) expect(ts[f], `${f} py↔ts`).toEqual(py[f])
    }
  })

  it('persona present, host persona=true + persona NOT configured ⇒ warn + base address, == Python', async () => {
    const ts = await tsRun(true, ['other'], PERSONA_TO, PERSONA_HDR)
    expect(ts.my_aimail_addr).toBe(EMAIL)
    if (pyOk) {
      const py = pyRun(true, { other: {} }, PERSONA_TO, PERSONA_HDR)
      for (const f of FIELDS) expect(ts[f], `${f} py↔ts`).toEqual(py[f])
    }
  })

  it('no persona (bare agent address) ⇒ my_aimail_addr = the address, == Python', async () => {
    const ts = await tsRun(false, null, [EMAIL], `Alice <${EMAIL}>`)
    expect(ts.my_aimail_addr).toBe(EMAIL)
    if (pyOk) {
      const py = pyRun(false, null, [EMAIL], `Alice <${EMAIL}>`)
      for (const f of FIELDS) expect(ts[f], `${f} py↔ts`).toEqual(py[f])
    }
  })

  it('multi-recipient (persona + external) ⇒ direct_message=false, == Python', async () => {
    const to = [PERSONA_ADDR, 'other@else.com']
    const hdr = `Support Alice <${PERSONA_ADDR}>, Other <other@else.com>`
    const ts = await tsRun(true, ['support'], to, hdr)
    expect(ts.direct_message).toBe(false)
    expect(ts.my_aimail_addr).toBe(PERSONA_ADDR)
    if (pyOk) {
      const py = pyRun(true, { support: {} }, to, hdr)
      for (const f of FIELDS) expect(ts[f], `${f} py↔ts`).toEqual(py[f])
    }
  })
})

/** Python parity helper: builds the same config the TS harness writes, runs
 *  aimail_base.preprocess_mail_payload, and prints the 5 QA fields as JSON.
 *  Invoked: python3 _p3_helper.py '<case json>'. */
const PY_HELPER = `
import sys, os, json, pathlib
case = json.loads(sys.argv[1])
home = case['home']
EMAIL, DOMAIN, SID = case['email'], case['domain'], case['sid']
# pysdk root is passed in via AIMAIL_REPO_ROOT (the test knows the layout)
sys.path.insert(0, os.path.join(os.environ['AIMAIL_REPO_ROOT'], 'pysdk'))
import aimail_base as ab
ab._CONFIG_LOADER = lambda: {'email': EMAIL, 'system_id': SID, 'system_name': '',
                             'gateway_url': 'http://127.0.0.1:9', 'domain': DOMAIN}
pdir = pathlib.Path(home) / 'profile'
pdir.mkdir(parents=True, exist_ok=True)
(pdir / '.agentmail').write_text(json.dumps({'system_id': SID, 'email': EMAIL}))
ab._PROFILE_DIR_RESOLVER = lambda: str(pdir)
ab.PERSONA_SUPPORTED = bool(case.get('support'))
ps = case.get('personas')
ab._PERSONAS_PROVIDER = (lambda: ps) if ps is not None else None
ab._TOOLSET_PROVIDER = ab._SOUL_PROVIDER = ab._SKILLS_PROVIDER = None
payload = {'mail_id': 'm-p3', 'message_id': '<m-p3@x>', 'subject': 'hi', 'body': case['body'],
           'from': 'peer@example.com', 'to': case['to'], 'cc': [],
           'headers': {'from': 'Peer <peer@example.com>', 'to': case['toHeader']}}
r = ab.preprocess_mail_payload(payload, {}) or {}
print(json.dumps({k: r.get(k) for k in ('my_aimail_addr', 'direct_message', 'mentioned', 'recipients', 'sender')}))
`
