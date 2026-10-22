/**
 * agent-level runtime — toolset filter, skill install, one-call runtime.
 *
 * Pins the three things an unadapted host must not hand-roll:
 * 1. the agent-level toolset (board_* excluded — no membership/token exists on
 *    an address-code mailbox; measured 401),
 * 2. the skill file install (idempotent, shipped with the package),
 * 3. startAgentLevel: skill (optional) + toolset + pull loop, delivering into
 *    the SDK's own inbound processing by default — no port, no endpoint.
 */
import { describe, it, expect, beforeAll, beforeEach, afterEach } from 'vitest'
import { promises as fs } from 'node:fs'
import { execFileSync } from 'node:child_process'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  AGENT_TOOL_NAMES,
  agentTools,
  installAgentSkill,
  startAgentLevel,
} from '../../src/core/agent-level.js'
import { MAIL_TOOLS } from '../../src/core/tool-registry.js'
import { AGENT_SKILL_NAME, BINDING_FILE } from '../../src/core/contract.js'

const PKG = path.resolve(__dirname, '..', '..')
const RESOURCE_SKILL = path.join(PKG, 'resources', 'skills', 'SKILL.md')

let tmp: string
let prevHome: string | undefined

beforeAll(async () => {
  // resources/ is a gitignored build product: stage it like prepack does.
  try {
    await fs.access(RESOURCE_SKILL)
  } catch {
    execFileSync('node', ['scripts/stage-resources.mjs', '--dest', './resources'], { cwd: PKG })
  }
})

beforeEach(async () => {
  tmp = await fs.mkdtemp(path.join(os.tmpdir(), 'agent-level-'))
  prevHome = process.env.AIMAIL_HOME
  process.env.AIMAIL_HOME = path.join(tmp, 'aimail')
})

afterEach(async () => {
  if (prevHome === undefined) delete process.env.AIMAIL_HOME
  else process.env.AIMAIL_HOME = prevHome
  await fs.rm(tmp, { recursive: true, force: true })
})

describe('agentTools', () => {
  it('exposes the agent-level tool set, board tools included (member scope)', () => {
    const names = agentTools().map((t) => t.name).sort()
    expect(names).toEqual([...AGENT_TOOL_NAMES].sort())
    expect(names.filter((n) => n.startsWith('board_'))).toEqual([
      'board_heartbeat', 'board_members', 'board_roles', 'board_status',
      'board_task_list', 'board_task_show',
    ])
    // every entry is executable (handler travels with the definition)
    for (const tool of agentTools()) expect(typeof tool.handler).toBe('function')
    // the agent-level set is the whole registry
    expect(agentTools().length).toBe(MAIL_TOOLS.length)
  })
})

describe('installAgentSkill', () => {
  it('copies the shipped skill into <skills>/<skill name>/SKILL.md (idempotent)', async () => {
    const skills = path.join(tmp, 'skills')
    const first = await installAgentSkill(skills)
    expect(first.installed).toBe(true)
    expect(first.path).toBe(path.join(skills, AGENT_SKILL_NAME, 'SKILL.md'))
    expect(await fs.readFile(first.path, 'utf-8')).toBe(
      await fs.readFile(RESOURCE_SKILL, 'utf-8'),
    )
    const second = await installAgentSkill(skills)
    expect(second.installed).toBe(false)
  })

  it('honours a custom subdir', async () => {
    const r = await installAgentSkill(path.join(tmp, 'skills'), { subdir: 'mailer' })
    expect(r.path).toBe(path.join(tmp, 'skills', 'mailer', 'SKILL.md'))
  })
})

describe('startAgentLevel', () => {
  it('installs the skill, returns the toolset and starts one loop per binding', async () => {
    const sid = 'shared_addr_e2e'
    const email = 'agent.demo@e2e.local'
    const dir = path.join(process.env.AIMAIL_HOME as string, 'systems', sid, email.replace('@', '_'))
    await fs.mkdir(dir, { recursive: true })
    await fs.writeFile(
      path.join(dir, BINDING_FILE),
      JSON.stringify({ system_id: sid, email, gateway_url: 'http://127.0.0.1:1', api_key: 'k' }),
    )

    const fake = {
      start_polling: async (
        _onEmail: unknown,
        opts: { interval?: number; limit?: number },
      ) => ({ rounds: 0, pulled: 0, acked: 0, errors: 0, interval: opts.interval, limit: opts.limit }),
    }
    const rt = await startAgentLevel({
      skillsDir: path.join(tmp, 'skills'),
      env: { AIMAIL_PULL_INTERVAL_MS: '1000' },
      log: () => {},
      clientFor: () => fake as never,
    })
    expect(rt.skill?.installed).toBe(true)
    expect(rt.tools.map((t) => t.name)).toEqual(agentTools().map((t) => t.name))
    expect(rt.handles).toHaveLength(1)
    expect(rt.handles[0].email).toBe(email)
    for (const h of rt.handles) h.stop()
  })
})
