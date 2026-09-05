/**
 * openclaw-aimail commands — `openclaw aimail register|deregister|status`.
 *
 * register: 4-step idempotent chain ported from Python register_agent_email
 *   (register_email(generate_code) → exists? update webhook → manager
 *   whitelist → activate_address) using existing gateway admin APIs only.
 * deregister: 3-step idempotent chain (api-key → domain → whitelist).
 * status: pointer + binding report.
 *
 * The chains are pure functions over a GatewayClient-like surface so they
 * are unit-testable with a MockClient (P2 acceptance).
 */
import { randomUUID } from 'node:crypto'
import { promises as fs } from 'node:fs'
import * as path from 'node:path'
import type { GatewayResponse } from '@aimail/mail-core'
import { agentConfigPath, systemDir } from '@aimail/mail-core'
import type {
  OpenClawPluginCommandDefinition,
  PluginCommandContext,
  PluginCommandResult,
} from 'openclaw/plugin-sdk/plugin-entry'
import { readPointer } from './identity.js'

/** Minimal admin client surface the chains depend on (MockClient-friendly). */
export interface AdminClient {
  request(
    method: string,
    path: string,
    body?: Record<string, unknown>,
    headers?: Record<string, string>,
    rawBody?: Uint8Array,
  ): Promise<GatewayResponse>
}

export interface RegisterOptions {
  systemId: string
  email: string
  webhookUrl: string
  webhookSecret: string
  managerAddress?: string
}

export interface RegisterResult {
  api_key?: string | undefined
  activation_code?: string
  exists?: boolean
}
/**
 * 4-step idempotent registration chain (port of Python register_agent_email).
 * Returns {api_key} when activation completed; {} when pending/existing.
 */
export async function registerAgentEmail(
  client: AdminClient,
  opts: RegisterOptions,
): Promise<RegisterResult> {
  const result = await client.request(
    'POST',
    `/api/v1/admin/systems/${opts.systemId}/addresses?generate_code=true`,
    {
      id: `addr-${opts.email.replace('@', '-at-')}-${Math.floor(Date.now() / 1000)}`,
      email: opts.email,
      webhook_url: opts.webhookUrl,
      webhook_secret: opts.webhookSecret,
      manager_address: opts.managerAddress ?? '',
    },
  )
  const activationCode = String(result.activation_code ?? '')
  const status = String(result.status ?? '')
  if (status && !['created', '200', '201'].includes(status)) {
    const msg =
      String(result.error ?? '') + String(result.detail ?? '')
    if (/already exists|exists/i.test(msg)) {
      // Idempotent: update webhook config for the existing address
      const domains = await client.request(
        'GET',
        `/api/v1/admin/systems/${opts.systemId}/domains`,
      )
      const entries = Array.isArray(domains.data)
        ? domains.data
        : (domains.entries as unknown[] | undefined) ?? []
      for (const d of entries) {
        const row = d as Record<string, unknown>
        if (row.domain === opts.email) {
          await client.request(
            'PUT',
            `/api/v1/admin/system-domains/${String(row.id)}`,
            {
              webhook_url: opts.webhookUrl,
              webhook_secret: opts.webhookSecret,
            },
          )
          break
        }
      }
      return { exists: true }
    }
    throw new Error(`register failed: ${JSON.stringify(result)}`)
  }

  if (!activationCode) return {}
  const act = await client.request(
    'POST',
    '/api/v1/activate-address',
    { code: activationCode, email_address: opts.email },
  )
  const apiKey = act.raw_key as string | undefined
  return { api_key: apiKey, activation_code: activationCode }
}

export interface DeregisterResult {
  api_key: string
  domain: string
  whitelist: string
}

/** 3-step idempotent deregistration chain (api-key → domain → whitelist). */
export async function deregisterAgentEmail(
  client: AdminClient,
  opts: { systemId: string; email: string; domainAddr: string },
): Promise<DeregisterResult> {
  const out: DeregisterResult = { api_key: '', domain: '', whitelist: '' }

  // 1. API key by email → delete
  try {
    const k = await client.request(
      'GET',
      `/api/v1/admin/api-keys?email=${encodeURIComponent(opts.email)}`,
    )
    const entries = Array.isArray(k.data)
      ? k.data
      : (k.entries as unknown[] | undefined) ?? []
    const key = entries[0] as Record<string, unknown> | undefined
    if (key && key.id) {
      const r = await client.request(
        'DELETE',
        `/api/v1/admin/api-keys/${String(key.id)}`,
      )
      out.api_key = String(r.status ?? '')
    } else {
      out.api_key = 'not_found'
    }
  } catch (e) {
    out.api_key = `err:${e instanceof Error ? e.message : String(e)}`
  }

  // 2. Domain entry by name → delete
  try {
    const domains = await client.request(
      'GET',
      `/api/v1/admin/systems/${opts.systemId}/domains`,
    )
    const entries = Array.isArray(domains.data)
      ? domains.data
      : (domains.entries as unknown[] | undefined) ?? []
    let addrId = ''
    for (const d of entries) {
      const row = d as Record<string, unknown>
      if (row.domain === opts.email) {
        addrId = String(row.id ?? '')
        break
      }
    }
    if (addrId) {
      const r = await client.request(
        'DELETE',
        `/api/v1/admin/system-domains/${addrId}`,
      )
      out.domain = String(r.status ?? '')
    } else {
      out.domain = 'not_found'
    }
  } catch (e) {
    out.domain = `err:${e instanceof Error ? e.message : String(e)}`
  }

  // 3. Whitelist by composite key
  try {
    const q = new URLSearchParams({
      domain_addr: opts.domainAddr,
      value: opts.email,
    })
    const r = await client.request(
      'DELETE',
      `/api/v1/whitelists?${q.toString()}`,
    )
    out.whitelist = String(r.status ?? '')
  } catch (e) {
    out.whitelist = `err:${e instanceof Error ? e.message : String(e)}`
  }

  return out
}

/** Resolve system_id from --system-id / pointer (throws when unknown). */
async function resolveSystemId(explicit: string): Promise<string> {
  if (explicit) return explicit
  const ptr = await readPointer()
  if (ptr.system_id) return ptr.system_id
  throw new Error(
    'no system_id — pass --system-id or activate the aimail pointer (~/.openclaw/.agentmail)',
  )
}

async function readGatewayConfig(
  systemId: string,
): Promise<Record<string, string>> {
  // canonical aimail_gateway.json with legacy-name auto-migration (mail-core
  // gatewayConfigPath parity); report the canonical name on failure
  const p = path.join(systemDir(systemId), 'aimail_gateway.json')
  const legacy = path.join(systemDir(systemId), 'agentmail_gateway.json')
  try {
    await fs.access(p)
  } catch {
    try {
      await fs.access(legacy)
      await fs.rename(legacy, p)
    } catch {
      /* neither — error below names the canonical file */
    }
  }
  try {
    return JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, string>
  } catch {
    throw new Error(
      `gateway config not found (aimail_gateway.json) for ${systemId} — activate first`,
    )
  }
}

function saveAgentConfig(
  agentId: string,
  cfg: Record<string, unknown>,
  systemId: string,
): Promise<void> {
  const email = String(cfg.email ?? '')
  if (!email) throw new Error('saveAgentConfig requires email')
  const p = agentConfigPath(systemId, email)
  return fs.mkdir(path.dirname(p), { recursive: true }).then(() =>
    fs.writeFile(
      p,
      JSON.stringify({ ...cfg, agent_id: agentId }, null, 2) + '\n',
      { mode: 0o600 },
    ),
  )
}

function cmdText(lines: string[]): PluginCommandResult {
  return { text: lines.join('\n') }
}

const USAGE = `openclaw aimail <register|deregister|status> [...args]
  register  --email <addr> [--system-id SID] [--webhook-url URL] [--manager ADDR]
             (4-step idempotent chain; writes agentmail.json + pointer)
  deregister --email <addr> [--system-id SID] [--domain-addr ADDR]
             (3-step idempotent chain; removes api-key/domain/whitelist)
  status    [--system-id SID]   (pointer + binding report)`

function parseArgs(args: string): Record<string, string> {
  const out: Record<string, string> = {}
  const tokens = args.trim().split(/\s+/)
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i]
    if (t.startsWith('--')) {
      const key = t.slice(2)
      const next = tokens[i + 1]
      if (next && !next.startsWith('--')) {
        out[key] = next
        i++
      } else {
        out[key] = 'true'
      }
    }
  }
  return out
}

async function handleCommand(
  ctx: PluginCommandContext,
): Promise<PluginCommandResult> {
  const args = ctx.args ?? ''
  const sub = (args.trim().split(/\s+/)[0] ?? '').toLowerCase()
  const opts = parseArgs(args.replace(/^\S+/, ''))
  try {
    if (sub === 'register') {
      const email = opts.email ?? ''
      if (!email) return cmdText(['register requires --email <addr>', '', USAGE])
      const systemId = await resolveSystemId(opts['system-id'] ?? '')
      const gw = await readGatewayConfig(systemId)
      // GatewayClient from mail-core (admin key), fall back to raw fetch
      // when the config lacks an admin key (agent-scope registration).
      const { GatewayClient } = await import('@aimail/mail-core')
      const apiKey = gw.admin_key ?? ''
      const admin = new GatewayClient(gw.gateway_url ?? '', apiKey, 30_000, systemId)
      const webhookSecret = randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
      const reg = await registerAgentEmail(admin, {
        systemId,
        email,
        webhookUrl: opts['webhook-url'] ?? '',
        webhookSecret,
        managerAddress: opts.manager ?? gw.manager_address ?? '',
      })
      if (reg.api_key) {
        await saveAgentConfig(
          ctx.agentId ?? 'main',
          {
            email,
            gateway_url: gw.gateway_url ?? '',
            domain: gw.domain ?? '',
            system_id: systemId,
            system_name: gw.system_name ?? '',
            manager_address: opts.manager ?? gw.manager_address ?? '',
            api_key: reg.api_key,
            webhook_url: opts['webhook-url'] ?? '',
            webhook_secret: webhookSecret,
          },
          systemId,
        )
        // pointer refresh: write ~/.openclaw/.agentmail
        const ptrPath = path.join(
          process.env.HOME ?? process.env.USERPROFILE ?? '',
          '.openclaw',
          '.agentmail',
        )
        await fs.mkdir(path.dirname(ptrPath), { recursive: true })
        await fs.writeFile(
          ptrPath,
          JSON.stringify({ system_id: systemId, email }, null, 2) + '\n',
          { mode: 0o600 },
        )
        return cmdText([
          `✓ registered ${email} (system ${systemId}, agent ${ctx.agentId ?? 'main'})`,
          `  api_key ok; webhook_url=${opts['webhook-url'] ?? '(pull)'}`,
        ])
      }
      return cmdText([
        `registered ${email} (system ${systemId})`,
        reg.exists ? '  address existed — webhook config updated' : '  activation pending (no api_key)',
      ])
    }

    if (sub === 'deregister') {
      const email = opts.email ?? ''
      if (!email) return cmdText(['deregister requires --email <addr>', '', USAGE])
      const systemId = await resolveSystemId(opts['system-id'] ?? '')
      const gw = await readGatewayConfig(systemId)
      const { GatewayClient } = await import('@aimail/mail-core')
      const admin = new GatewayClient(gw.gateway_url ?? '', gw.admin_key ?? '', 30_000, systemId)
      const out = await deregisterAgentEmail(admin, {
        systemId,
        email,
        domainAddr: opts['domain-addr'] ?? gw.domain ?? '',
      })
      return cmdText([
        `deregistered ${email} (system ${systemId})`,
        `  api_key: ${out.api_key}`,
        `  domain:  ${out.domain}`,
        `  whitelist: ${out.whitelist}`,
      ])
    }

    if (sub === 'status') {
      const systemId = await resolveSystemId(opts['system-id'] ?? '')
      const ptr = await readPointer()
      const lines = [
        `system_id: ${systemId}`,
        `pointer email: ${ptr.email ?? '(none)'}`,
      ]
      try {
        const gw = await readGatewayConfig(systemId)
        lines.push(`gateway_url: ${gw.gateway_url ?? '(unknown)'}`)
        lines.push(`domain: ${gw.domain ?? '(unknown)'}`)
      } catch (e) {
        lines.push(`gateway config: ${e instanceof Error ? e.message : String(e)}`)
      }
      return cmdText(lines)
    }

    return cmdText([USAGE])
  } catch (e) {
    return cmdText([
      `error: ${e instanceof Error ? e.message : String(e)}`,
      '',
      USAGE,
    ])
  }
}

/** The three aimail commands (name "aimail" + subcommands). */
export function createAimailCommands(): OpenClawPluginCommandDefinition[] {
  return [
    {
      name: 'aimail',
      description: 'AIMail registration and status: register|deregister|status',
      acceptsArgs: true,
      handler: handleCommand,
    },
  ]
}
