/**
 * poll-entry.ts — agent-scope 定时轮询入口(auto-pull wiring, slice 8)。
 *
 * 适配器 install/初始化收尾要接上的**最后一根线**: 地址级激活得到的 agent-scope
 * key 取不到 push(网关给该地址写的是 `webhook_url = NULL`, 见
 * `aimail-advanced src/advanced/api/address.rs` 的 system_domains INSERT),
 * 只能自己定时 pull。此前 `pullList/pullAck/startPolling` 有库有单测、**零调用方**,
 * 文档承诺的"agent 自主接入"在产线上缺这根线 —— 本模块就是它。
 *
 * 语义(与 startPolling 同族, 不重复实现):
 *   - **只对 agent 级激活启用**: 绑定是地址码兑换产物 ⇒ 启用; 系统/bridge 场景
 *     (平台注册的系统, 走 push) ⇒ 不启用。判据是**现成事实**, 不是猜:
 *     地址码只可能在 `shared_addr_*` 宿主系统下被兑换 ——
 *     `aimail-advanced src/advanced/api/address.rs:308`
 *     `if !sid.starts_with("shared_addr_") { return Ok(Err(ActErr::Invalid)); }`
 *     ⇒ 绑定 `system_id` 以该前缀开头 ⇔ 该 key 由地址码兑换而来(agent scope;
 *     见 `src/advanced/api/pull_intercept.rs` 头部: agent scope ⇒ 只服务自己地址)。
 *     regen/产品创建路径**永不**产生该前缀(`products.rs:83` 显式排除)
 *     ⇒ 系统/bridge 绑定不会被误判。
 *   - **失败不 ack / 幂等去重**: 由 `startPolling` 保证(onEmail 抛错或
 *     reject ⇒ 该 delivery 不 ack, 下轮重拉; 去重键 = delivery id)。
 *   - **可关停 / 可配间隔**: 句柄 `stop()`(AbortController) + 进程退出即停
 *     (计时器 unref); 间隔/批量/总开关走 env 或显式 override。
 *   - **不泄漏定时器**: 一个绑定一条循环, 句柄收敛在启动处。
 */
import type { AgentConfig } from './types.js'
import { GatewayClient } from './gateway.js'
import { listAgentConfigs } from './config.js'
import { listSystemDirs } from './auto-bind.js'
import { startPolling, type PollStats, type RequestClient } from './address-code.js'

/**
 * 地址级激活产物的系统 id 前缀 —— 网关侧事实(证据见模块头):
 * 只有 `shared_addr_*` 宿主系统下的激活码可被兑换, 兑换成功即落该 sid。
 */
export const AGENT_SCOPE_SYSTEM_PREFIX = 'shared_addr_'

/** 默认轮询间隔(30s): 邮件不是实时通道, 但要比人的耐心快。 */
export const DEFAULT_PULL_INTERVAL_MS = 30_000

/** 默认单轮批量(网关上限 200, pullList 自行收敛)。 */
export const DEFAULT_PULL_LIMIT = 20

/** 下限 1s: 更密就是打网关, 没有业务理由。 */
const MIN_PULL_INTERVAL_MS = 1_000

/** 总开关环境变量: 0/false/off/no(大小写不敏感)⇒ 关。默认开。 */
const PULL_ENABLE_ENV = 'AIMAIL_PULL'
const PULL_INTERVAL_ENV = 'AIMAIL_PULL_INTERVAL_MS'
const PULL_LIMIT_ENV = 'AIMAIL_PULL_LIMIT'

/** 该绑定是不是地址级激活(agent scope)产物 —— 用现成事实判定, 不猜。 */
export function isAgentScopeBinding(cfg: AgentConfig | undefined | null): boolean {
  if (!cfg) return false
  if (!cfg.system_id || !cfg.api_key) return false
  return cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX)
}

/** 判定结果的可解释原因(日志与单测都按它断言)。 */
export type PullDecisionReason =
  | 'enabled'
  | 'no-binding'
  | 'not-agent-scope'
  | 'disabled-by-config'

export interface AgentPullSettings {
  enabled: boolean
  reason: PullDecisionReason
  intervalMs: number
  limit: number
}

export interface AgentPullOverrides {
  enabled?: boolean
  intervalMs?: number
  limit?: number
}

function envFlagOff(raw: string | undefined): boolean {
  if (raw === undefined) return false
  const v = raw.trim().toLowerCase()
  return v === '0' || v === 'false' || v === 'off' || v === 'no'
}

function clampInt(raw: string | number | undefined, def: number, min: number, max: number): number {
  const n = typeof raw === 'number' ? raw : Number(raw)
  if (!Number.isFinite(n) || n <= 0) return def
  return Math.max(min, Math.min(Math.trunc(n), max))
}

/**
 * Resolve whether/what to poll for one binding.
 *
 * Precedence: explicit override > env > default. `enabled` defaults to
 * true for an agent-scope binding (the only inbound path it has) and is
 * never true without one.
 */
export function resolveAgentPullSettings(
  cfg: AgentConfig | undefined | null,
  env: NodeJS.ProcessEnv = process.env,
  overrides: AgentPullOverrides = {},
): AgentPullSettings {
  const intervalMs = clampInt(
    overrides.intervalMs ?? env[PULL_INTERVAL_ENV],
    DEFAULT_PULL_INTERVAL_MS,
    MIN_PULL_INTERVAL_MS,
    24 * 3600_000,
  )
  const limit = clampInt(overrides.limit ?? env[PULL_LIMIT_ENV], DEFAULT_PULL_LIMIT, 1, 200)
  if (!cfg) return { enabled: false, reason: 'no-binding', intervalMs, limit }
  if (!isAgentScopeBinding(cfg)) {
    return { enabled: false, reason: 'not-agent-scope', intervalMs, limit }
  }
  const off = overrides.enabled === undefined ? envFlagOff(env[PULL_ENABLE_ENV]) : overrides.enabled === false
  if (off) return { enabled: false, reason: 'disabled-by-config', intervalMs, limit }
  return { enabled: true, reason: 'enabled', intervalMs, limit }
}

/** One pulled delivery (batch body + its per-delivery routing facts). */
export interface PulledMail {
  id: number
  email: string
  headers: Record<string, unknown>
  body: unknown
}

/** The adapter's own inbound chain, entered with a pulled mail. */
export type PullInboundDelivery = (mail: PulledMail) => void | Promise<void>

export interface AgentPullHandle {
  /** The binding this loop serves (undefined when nothing was started). */
  email: string
  started: boolean
  reason: PullDecisionReason
  intervalMs: number
  limit: number
  /** Stop the loop (idempotent; resolves `done`). */
  stop(): void
  /** Settles when the loop exits (stats accumulated so far). */
  done: Promise<PollStats>
  /** Live stats view for observability/tests. */
  stats(): PollStats
}

export interface StartAgentPullEntriesOptions {
  /** System scope: '' ⇒ every system under ~/.aimail/systems. */
  systemId?: string
  /**
   * Delivery into THIS adapter's existing inbound chain. Receives the
   * binding it belongs to (so per-address routing stays authoritative).
   */
  onEmail: (cfg: AgentConfig, mail: PulledMail) => void | Promise<void>
  env?: NodeJS.ProcessEnv
  overrides?: AgentPullOverrides
  /** Reporting line (default console.log) — one per binding + one when off. */
  log?: (line: string) => void
  /** Test seam: build the request client for a binding. */
  clientFor?: (cfg: AgentConfig) => RequestClient
}

/**
 * Start one poll loop per **agent-scope** binding, delivering into the
 * caller's inbound chain. Returns the handles (empty ⇒ nothing enabled,
 * which is the push/systems case) — never throws for the disabled case.
 */
export async function startAgentPullEntries(
  opts: StartAgentPullEntriesOptions,
): Promise<AgentPullHandle[]> {
  const env = opts.env ?? process.env
  const log = opts.log ?? ((line: string) => console.log(line))
  const sids = opts.systemId ? [opts.systemId] : await listSystemDirs()
  const bindings: AgentConfig[] = []
  for (const sid of sids) {
    for (const cfg of await listAgentConfigs(sid)) bindings.push(cfg)
  }
  const handles: AgentPullHandle[] = []
  for (const cfg of bindings) {
    const settings = resolveAgentPullSettings(cfg, env, opts.overrides ?? {})
    if (!settings.enabled) {
      // Only report the NOT-agent-scope decision for a binding that exists;
      // silence for push-side bindings would hide a mis-wiring.
      log(
        `[aimail-pull] ${cfg.email}: not polling (${settings.reason}) — ` +
          'push/system binding unchanged',
      )
      continue
    }
    const client = opts.clientFor
      ? opts.clientFor(cfg)
      : new GatewayClient(cfg.gateway_url, cfg.api_key, 30_000, cfg.email)
    const controller = new AbortController()
    const live: PollStats = { pulled: 0, acked: 0, errors: 0 }
    const done = startPolling(
      client,
      async (mail) => {
        // Count first: startPolling awaits this, a throw ⇒ un-acked ⇒ re-pull.
        live.pulled += 1
        await opts.onEmail(cfg, {
          id: mail.id,
          email: mail.email,
          headers: (mail.headers ?? {}) as Record<string, unknown>,
          body: mail.body,
        })
      },
      {
        intervalMs: settings.intervalMs,
        limit: settings.limit,
        signal: controller.signal,
      },
    )
      .then((stats) => {
        live.pulled = stats.pulled
        live.acked = stats.acked
        live.errors = stats.errors
        return stats
      })
      .catch((e: unknown) => {
        log(`[aimail-pull] ${cfg.email}: loop ended with error: ${String(e)}`)
        return live
      })
    log(
      `[aimail-pull] ${cfg.email}: polling enabled every ${settings.intervalMs}ms ` +
        `(limit ${settings.limit}, agent-scope key)`,
    )
    handles.push({
      email: cfg.email,
      started: true,
      reason: settings.reason,
      intervalMs: settings.intervalMs,
      limit: settings.limit,
      stop: () => controller.abort(),
      done,
      stats: () => ({ ...live }),
    })
  }
  if (handles.length === 0) {
    log(
      '[aimail-pull] no agent-scope binding — polling not started ' +
        '(push path unchanged; address-code activation enables it)',
    )
  }
  return handles
}

/** Stop every handle (host shutdown; idempotent). */
export function stopAgentPullEntries(handles: readonly AgentPullHandle[]): void {
  for (const h of handles) h.stop()
}
