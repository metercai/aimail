/**
 * inbound-notify — a host's best-effort "my inbound is live / down" note to the
 * environment master (the `aimail` CLI).
 *
 * Layering (owner ruling 2026-09-28): the SDK is **bridge-agnostic** — it neither
 * knows nor speaks about the local bridge. The CLI owns the environment (bridge
 * routes included). A host therefore reports only the two facts it really owns:
 *
 *   - its inbound listener is serving  → `--inbound-live`
 *   - it has stopped                   → `--inbound-down`
 *
 * The CLI decides what that means for its own route table. That keeps the timing
 * rule (a route may only take effect once inbound really serves) while removing
 * every route/bridge concept from the SDK.
 *
 * Contract:
 *   - one argv call per address this host serves:
 *       aimail address -a <addr> --inbound-live
 *       aimail address -a <addr> --inbound-down
 *   - binary resolution: `AIMAIL_BIN` → `~/.aimail/bin/aimail` → `aimail` on PATH.
 *     None found ⇒ one debug line, then skip. A machine without the CLI is still
 *     fully self-sufficient: it binds, serves and self-registers (address-level
 *     standalone deployments).
 *   - argv only (never a shell), timeout 3–5s, **never throws**, never blocks the
 *     host's startup, never retries: a non-zero exit or a spawn error is one log
 *     line and the host keeps going.
 *   - the payload carries the address and the state, nothing else — no port, no
 *     protocol, no bridge.
 */
import { spawn } from 'node:child_process'
import { existsSync } from 'node:fs'
import { AIMAIL_HOME } from './config.js'
import { listSystemDirs, readSystemConfig } from './auto-bind.js'
import { listAgentConfigs } from './config.js'

/** Environment shape the resolver reads (injectable in tests). */
export type EnvLike = Record<string, string | undefined>

export type InboundState = 'live' | 'down'

/** The CLI argument that expresses each state (the notification payload). */
export const INBOUND_STATE_FLAG: Readonly<Record<InboundState, string>> = {
  live: '--inbound-live',
  down: '--inbound-down',
}

export interface InboundNotifyOutcome {
  /** notified = the CLI accepted the note; no_cli = nothing to call; failed = one-line error. */
  state: 'notified' | 'no_cli' | 'failed'
  address: string
  /** resolved binary ('' when none was found). */
  bin: string
  /** CLI exit code when it ran. */
  code?: number
  detail?: string
}

/** Runs one argv command and reports how it ended (never throws). */
export type CommandRunner = (
  bin: string,
  args: string[],
  timeoutMs: number,
) => Promise<{ code: number | null; error?: string }>

export interface NotifyOptions {
  /** bindings of this system (default: the single system on this machine). */
  systemId?: string
  /** 3–5s by contract. */
  timeoutMs?: number
  env?: EnvLike
  /** injected runner (tests); default = real child_process.spawn. */
  runner?: CommandRunner
}

export function logLine(prefix: string, msg: string): void {
  console.log(`[${prefix}] ${msg}`)
}

/**
 * Binary resolution order: AIMAIL_BIN → ~/.aimail/bin/aimail → PATH `aimail`.
 * Returns '' when the machine has no CLI at all (a supported deployment).
 */
export function resolveAimailBin(env: EnvLike = process.env as EnvLike): string {
  const fromEnv = String(env['AIMAIL_BIN'] ?? '').trim()
  if (fromEnv) return fromEnv
  const canonical = `${AIMAIL_HOME()}/bin/aimail`
  try {
    if (existsSync(canonical)) return canonical
  } catch {
    /* ignore */
  }
  return 'aimail'
}

/** Default runner: argv spawn, no shell, hard timeout, output discarded. */
export const spawnRunner: CommandRunner = (bin, args, timeoutMs) =>
  new Promise((resolve) => {
    let settled = false
    const done = (v: { code: number | null; error?: string }) => {
      if (settled) return
      settled = true
      resolve(v)
    }
    try {
      const child = spawn(bin, args, { stdio: 'ignore', shell: false })
      const timer = setTimeout(() => {
        try {
          child.kill('SIGKILL')
        } catch {
          /* ignore */
        }
        done({ code: null, error: `timed out after ${timeoutMs}ms` })
      }, timeoutMs)
      child.once('error', (e) => {
        clearTimeout(timer)
        done({ code: null, error: e.message })
      })
      child.once('exit', (code) => {
        clearTimeout(timer)
        done({ code })
      })
    } catch (e) {
      done({ code: null, error: e instanceof Error ? e.message : String(e) })
    }
  })

/**
 * Notify the CLI about ONE address's inbound state. Never throws, never blocks:
 * every path resolves to an outcome the caller may log.
 */
export async function notifyInboundState(
  address: string,
  state: InboundState,
  opts: NotifyOptions = {},
): Promise<InboundNotifyOutcome> {
  const addr = String(address ?? '').trim()
  if (!addr) return { state: 'failed', address: '', bin: '', detail: 'no address' }
  const bin = resolveAimailBin(opts.env ?? process.env)
  if (!bin) {
    return { state: 'no_cli', address: addr, bin: '', detail: 'no aimail CLI on this machine' }
  }
  const run = opts.runner ?? spawnRunner
  const args = ['address', '-a', addr, INBOUND_STATE_FLAG[state]]
  try {
    const res = await run(bin, args, opts.timeoutMs ?? 4000)
    if (res.error) return { state: 'failed', address: addr, bin, detail: res.error }
    if (res.code !== 0) {
      return { state: 'failed', address: addr, bin, code: res.code ?? -1, detail: `exit ${res.code}` }
    }
    return { state: 'notified', address: addr, bin, code: 0 }
  } catch (e) {
    // A notification is best-effort by contract: it can never reach the host.
    return { state: 'failed', address: addr, bin, detail: e instanceof Error ? e.message : String(e) }
  }
}

/**
 * Notify the CLI for EVERY address this host serves (the addresses of the
 * machine's system, all of which this host's listener answers for).
 *
 * Returns [] when there is nothing to report (no system / no binding) — and
 * `[{state:'no_cli'}]`-shaped outcomes when the CLI is absent, so the caller can
 * print one debug line instead of pretending it happened. Never throws.
 */
export async function notifyInboundForSystem(
  state: InboundState,
  opts: NotifyOptions = {},
): Promise<InboundNotifyOutcome[]> {
  let sids: string[] = []
  try {
    sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
  } catch {
    return []
  }
  if (sids.length !== 1) return []
  const sid = sids[0] as string
  try {
    await readSystemConfig(sid)
  } catch {
    return [] // no system config ⇒ nothing this SDK process is serving for
  }
  let bindings: Array<{ email?: string }> = []
  try {
    bindings = await listAgentConfigs(sid)
  } catch {
    return []
  }
  const addrs = bindings.map(b => String(b.email ?? '').trim()).filter(a => a !== '')
  if (addrs.length === 0) return []

  const out: InboundNotifyOutcome[] = []
  for (const a of addrs) {
    out.push(await notifyInboundState(a, state, opts))
  }
  return out
}

/** One-line English status for an outcome (every host prints the same text). */
export function formatInboundNotifyLine(o: InboundNotifyOutcome, state: InboundState): string {
  const what = state === 'live' ? 'inbound live' : 'inbound down'
  if (o.state === 'notified') return `${what} reported for ${o.address} (${o.bin})`
  if (o.state === 'no_cli') {
    return `${what} not reported for ${o.address}: no aimail CLI (${o.detail ?? 'not found'})`
  }
  return `${what} not reported for ${o.address}: ${o.detail ?? 'unknown'}`
}

/** True when the outcome deserves a warning line rather than an info line. */
export function isInboundNotifyWarning(o: InboundNotifyOutcome): boolean {
  return o.state === 'failed'
}
