/**
 * Stable client identity for one Agent-system instance.
 *
 * The id is derived from the agent home directory plus a machine hardware
 * fingerprint. It is intentionally NOT a Gateway system id: the Gateway sees
 * it only as the SSE aggregation client identity. The derivation is pure given
 * its inputs so tests and restarts are deterministic.
 */
import { createHash } from 'node:crypto'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'

export interface AgentSystemIdInputs {
  /** Stable host/agent home (e.g. ~/.dsh, ~/.openclaw, ~/.pi). */
  agentHome: string
  /** Machine hardware fingerprint; must be stable for the same machine. */
  hardwareFingerprint: string
}

/** Pure derivation, exposed for tests and host-specific fingerprint seams. */
export function deriveAgentSystemId(inputs: AgentSystemIdInputs): string {
  const home = path.resolve(inputs.agentHome.trim() || '/')
  const fp = inputs.hardwareFingerprint.trim()
  if (!fp) throw new Error('agent system id: empty hardware fingerprint')
  const digest = createHash('sha256')
    .update('aimail-agent-system-v1\0')
    .update(home)
    .update('\0')
    .update(fp)
    .digest('hex')
  return `asid_${digest.slice(0, 32)}`
}

async function readMachineId(): Promise<string> {
  for (const p of ['/etc/machine-id', '/var/lib/dbus/machine-id']) {
    try {
      const v = (await fs.readFile(p, 'utf8')).trim()
      if (v) return v
    } catch {
      /* try next */
    }
  }
  return ''
}

/**
 * Best-effort hardware fingerprint. It must not contain secrets; hostname +
 * platform + arch + machine-id is stable across restarts and distinguishes
 * machines. AIMAIL_HARDWARE_FINGERPRINT is a test/enterprise seam.
 */
export async function hardwareFingerprint(): Promise<string> {
  const override = process.env.AIMAIL_HARDWARE_FINGERPRINT?.trim()
  if (override) return override
  const machine = await readMachineId()
  return [
    os.hostname(),
    os.platform(),
    os.arch(),
    machine || 'no-machine-id',
  ].join('|')
}

let cached: string | undefined
let agentHomeResolver: (() => string | undefined) | undefined

/**
 * Host seam: the AgentSystemId is derived from the AGENT home (e.g.
 * ~/.dsh/profiles/web, ~/.pi, ~/.openclaw), not the OS user's home. Without
 * this, DSH + Pi + OpenClaw (and multiple DSH profiles) on one machine share
 * one identity and the Gateway aggregates their mailboxes together.
 */
export function setAgentHomeResolver(fn: (() => string | undefined) | undefined): void {
  agentHomeResolver = fn
  cached = undefined
}

/** Cached process-level AgentSystemId (derivation is deterministic anyway). */
export async function getAgentSystemId(): Promise<string> {
  const override = process.env.AIMAIL_AGENT_SYSTEM_ID?.trim()
  if (override) return override
  if (cached) return cached
  let resolved = ''
  try {
    resolved = String(agentHomeResolver?.() ?? '').trim()
  } catch {
    /* a broken resolver degrades to the OS home, never breaks the host */
  }
  const agentHome = process.env.AIMAIL_AGENT_HOME?.trim() || resolved || os.homedir()
  cached = deriveAgentSystemId({
    agentHome,
    hardwareFingerprint: await hardwareFingerprint(),
  })
  return cached
}

/** Test seam: clear the process cache after changing env/resolver. */
export function resetAgentSystemIdForTests(): void {
  cached = undefined
}
