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
import { systemDir } from '@aimail/mail-core'
import type {
  OpenClawPluginCommandDefinition,
  PluginCommandContext,
  PluginCommandResult,
} from 'openclaw/plugin-sdk/plugin-entry'
import { openclawWebhookUrl, readPointer, writePointer } from './identity.js'
import { emailForAgent } from '@aimail/mail-core'

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
  // canonical aimail_gateway.json only; report the canonical name on failure
  const p = path.join(systemDir(systemId), 'aimail_gateway.json')
  try {
    return JSON.parse(await fs.readFile(p, 'utf-8')) as Record<string, string>
  } catch {
    throw new Error(
      `gateway config not found (aimail_gateway.json) for ${systemId} — activate first`,
    )
  }
}

function cmdText(lines: string[]): PluginCommandResult {
  return { text: lines.join('\n') }
}

const USAGE = `openclaw aimail <register|register-all|deregister|status> [...args]
  register  --email <addr> [--system-id SID] [--webhook-url URL] [--manager ADDR]
             (4-step idempotent chain; writes agentmail.json + pointer)
  register-all [--system-id SID] [--domain D]
             (multi-agent: enumerate ~/.openclaw/agents/* → {agent}@{domain}
              per agent, same chain; pointer stays with the main agent)
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

export async function handleCommand(
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
      const webhookSecret = randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
      // 统一走 mail-core autoBind(归属读 → 4 步注册 → agentmail.json 原子
      // 写 0600 → bridge route)。此前本命令用复制链 + 非原子落盘 + 从不配
      // bridge route → 命令注册的地址收不到信(AUDIT-1 P1-9)。
      const { autoBind } = await import('@aimail/mail-core')
      const agentId = ctx.agentId ?? 'main'
      try {
        const res = await autoBind({
          systemId,
          email,
          webhookUrl: opts['webhook-url'] ?? openclawWebhookUrl(),
          webhookSecret,
          managerAddress: opts.manager ?? gw.manager_address ?? '',
          extraFields: { agent_id: agentId },
        })
        if (res.exists) {
          return cmdText([
            `registered ${email} (system ${systemId})`,
            '  address already bound locally — nothing to do (idempotent)',
          ])
        }
        await writePointer({ system_id: systemId, email })
        return cmdText([
          `✓ registered ${email} (system ${systemId}, agent ${agentId})`,
          `  api_key ok; webhook_url=${opts['webhook-url'] ?? '(local gateway route)'}`,
        ])
      } catch (e) {
        const msg = e instanceof Error ? e.message : String(e)
        if (/already exists/i.test(msg)) {
          return cmdText([
            `registered ${email}: remote address exists but no local api_key binding —`,
            `  run 'openclaw aimail deregister --email ${email} --system-id ${systemId}' first,`,
            `  then register again (or remove the stale binding and re-run)`,
          ])
        }
        throw e
      }
    }

    if (sub === 'register-all') {
      // 多 agent 全量注册:枚举 ~/.openclaw/agents/*,每个 agent 自动
      // 派生地址 {agent名}@{domain} 走 autoBind(与 register 同链)。
      // 无显式 email/agent 参数——agent 集合是平台的,地址派生是 SDK 的。
      const systemId = await resolveSystemId(opts['system-id'] ?? '')
      const gw = await readGatewayConfig(systemId)
      const domain = opts.domain ?? gw.domain ?? ''
      if (!domain) {
        return cmdText(['register-all requires a domain (gateway cfg has none — pass --domain)'])
      }
      const agentsRoot = path.join(process.env.HOME ?? '', '.openclaw', 'agents')
      let agentNames: string[] = []
      try {
        const entries = await fs.readdir(agentsRoot, { withFileTypes: true })
        agentNames = entries.filter((e) => e.isDirectory() && !e.name.startsWith('.')).map((e) => e.name).sort()
      } catch {
        return cmdText([`register-all: no agents dir at ${agentsRoot}`, USAGE])
      }
      if (agentNames.length === 0) return cmdText([`register-all: no agents under ${agentsRoot}`])
      const { autoBind } = await import('@aimail/mail-core')
      const systemName = gw.system_name ?? ''
      const out: string[] = [`register-all: ${agentNames.length} agent(s) → ${domain} (system ${systemId})`]
      let okN = 0
      for (const name of agentNames) {
        // 地址派生与 identity.ts auto-bind 同源:emailForAgent(别名归一/
        // 共享域 {base}.{system_name} 形态/atext 清洗)——两条路径同一 agent 同一地址
        const email = emailForAgent(name, domain, systemName, ['main'])
        const secret = randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
        try {
          const res = await autoBind({
            systemId,
            email,
            webhookUrl: opts['webhook-url'] ?? openclawWebhookUrl(),
            webhookSecret: secret,
            managerAddress: opts.manager ?? gw.manager_address ?? '',
            extraFields: { agent_id: name },
          })
          if (res.exists) {
            out.push(`  ${email}: already bound (idempotent)`)
          } else {
            out.push(`  ✓ ${email}: registered (agent ${name})`)
          }
          okN++
        } catch (e) {
          const msg = e instanceof Error ? e.message : String(e)
          if (/already exists/i.test(msg)) {
            out.push(`  ${email}: remote exists, no local key — deregister first (see register help)`)
          } else {
            out.push(`  ✗ ${email}: ${msg}`)
          }
        }
      }
      out.push(`register-all done: ${okN}/${agentNames.length} ok (pointer unchanged — main agent owns it)`)
      return cmdText(out)
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
