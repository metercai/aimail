/**
 * TS-host system activation via the CLI ABI (reverse call).
 *
 * The activation protocol is deliberately NOT re-implemented here — it lives
 * exactly once, in `aimail ensure-system` (L1 only). TS hosts (dsh/pi/openclaw)
 * call back into that command instead of carrying a second protocol
 * implementation (2026-09 architecture ruling, option B).
 *
 * Call-loop breaker: `aimail install` (human path) does L1 + platform wiring
 * (it may spawn `dsh plugin add`); the plugin's readiness check calls back
 * ONLY into `aimail ensure-system`, which never spawns host commands. Two
 * distinct entries, one direction each → no recursion.
 *
 * Contract (CLI side, locked by tests): stdout = exactly one JSON line
 * {success, system_id, gateway_url, domain, system_name, path} or
 * {success:false, error, hint}; exit 0/1.
 */
import { execFile } from 'node:child_process'
import { promises as fs } from 'node:fs'
import * as os from 'node:os'
import * as path from 'node:path'
import { gatewayConfigPath } from './config.js'
import { listSystemDirs } from './auto-bind.js'

export interface EnsureSystemOptions {
  /** platform/agent home root passed as -H (lets the CLI reuse-detect). */
  systemHome?: string
  /** aimail binary override (default: AIMAIL_CLI env, else 'aimail' on PATH). */
  cliPath?: string
  /** test seam: replaces the real execFile. */
  exec?: (cmd: string, args: string[]) => Promise<{ code: number; stdout: string }>
  timeoutMs?: number
}

export interface EnsureSystemResult {
  ok: boolean
  /** sole/owning system id when determinable ('' = systems exist, scope open). */
  systemId?: string
  /** true when the CLI performed a fresh activation (vs reuse). */
  activated?: boolean
  gatewayUrl?: string
  domain?: string
  systemName?: string
  error?: string
  hint?: string
}

function defaultExec(
  timeoutMs: number,
): (cmd: string, args: string[]) => Promise<{ code: number; stdout: string }> {
  return (cmd, args) =>
    new Promise((resolve, reject) => {
      execFile(cmd, args, { timeout: timeoutMs }, (err, stdout) => {
        if (err) {
          const nodeErr = err as NodeJS.ErrnoException
          if (nodeErr.code === 'ENOENT') {
            reject(new Error(`aimail CLI not found ('${cmd}') — run bootstrap first`))
            return
          }
          resolve({ code: typeof err.code === 'number' ? err.code : 1, stdout })
          return
        }
        resolve({ code: 0, stdout })
      })
    })
}

/**
 * Local ownership probe (pure file reads — NOT the activation protocol, which
 * stays in the CLI): system_home → owning sid, UNIQUE match only (ambiguous
 * → '' so the CLI's authoritative reuse/activate decision is consulted).
 */
export async function detectSystemForHome(systemHome: string): Promise<string> {
  if (!systemHome) return ''
  const target = path.resolve(systemHome.replace(/^~\//, os.homedir() + '/')).replace(/\/+$/, '')
  let found = ''
  for (const sid of await listSystemDirs()) {
    let cfg: Record<string, unknown>
    try {
      const p = await gatewayConfigPath(sid)
      cfg = JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, unknown>
    } catch {
      continue
    }
    const sh = String(cfg.system_home ?? '')
    if (sh) {
      const norm = path.resolve(sh.replace(/^~\//, os.homedir() + '/')).replace(/\/+$/, '')
      if (norm === target) {
        if (found) return '' // second owner → ambiguous, don't guess
        found = sid
      }
    }
  }
  return found
}

/**
 * Ensure a system exists for this machine: local systems present → ok (no
 * call-out); none → reverse-call `aimail ensure-system -H <home>` and parse
 * its JSON contract. Never throws (CLI absence → actionable error).
 */
export async function ensureSystem(
  opts: EnsureSystemOptions = {},
): Promise<EnsureSystemResult> {
  // 1) Ownership short-circuit: when a systemHome is given, only an OWNING
  // system (cfg.system_home matches) short-circuits — on multi-platform
  // machines another platform's system must NOT block this one's activation.
  // Without systemHome: any local system short-circuits (sole-system machine).
  let owned = ''
  if (opts.systemHome) owned = await detectSystemForHome(opts.systemHome)
  if (owned) return { ok: true, systemId: owned, activated: false }
  if (!opts.systemHome) {
    const sids = await listSystemDirs()
    if (sids.length > 0) {
      const scope = process.env.AIMAIL_SYSTEM_ID?.trim() ?? ''
      const systemId =
        scope && sids.includes(scope) ? scope : sids.length === 1 ? (sids[0] as string) : ''
      return { ok: true, systemId, activated: false }
    }
  }

  // 2) Reverse-call the CLI L1 ABI (single activation implementation).
  const cmd = opts.cliPath ?? process.env.AIMAIL_CLI ?? 'aimail'
  const args = ['ensure-system']
  if (opts.systemHome) args.push('-H', opts.systemHome)
  const exec = opts.exec ?? defaultExec(opts.timeoutMs ?? 60_000)
  let r: { code: number; stdout: string }
  try {
    r = await exec(cmd, args)
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    return { ok: false, error: msg, hint: 'run the aimail bootstrap first, then retry' }
  }
  let parsed: Record<string, unknown>
  try {
    parsed = JSON.parse(r.stdout.trim()) as Record<string, unknown>
  } catch {
    return {
      ok: false,
      error: `aimail ensure-system returned unparsable output (exit ${r.code})`,
      hint: 'run `aimail install --home <root>` manually to see the error',
    }
  }
  if (parsed.success !== true || r.code !== 0) {
    return {
      ok: false,
      error: String(parsed.error ?? `ensure-system failed (exit ${r.code})`),
      ...(parsed.hint !== undefined ? { hint: String(parsed.hint) } : {}),
    }
  }
  return {
    ok: true,
    systemId: String(parsed.system_id ?? ''),
    activated: parsed.path === 'activation',
    gatewayUrl: String(parsed.gateway_url ?? ''),
    domain: String(parsed.domain ?? ''),
    systemName: String(parsed.system_name ?? ''),
  }
}
