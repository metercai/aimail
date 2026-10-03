#!/usr/bin/env node
/**
 * register-cli — CLI-spawnable dsh↔AIMail binding entry (TS home of the
 * retired cli/dsh/bind_agent.py; platform boundary: CLI never implements
 * platform registration, it spawns this entry via the platform registry).
 *
 * Flow (parity with bind_agent.py, Python shared chain replaced by the
 * mail-core chain):
 *   1. systemId: --system-id | AIMAIL_SYSTEM_ID | unique local system
 *   2. email:    --name <base> | --email | platform default base
 *                (resolveRegisterEmail — same derivation as the Python
 *                 plan_address_name; CLI passes the planned base via --name)
 *   3. gateway chain: autoBind (registerAddress+activate+saveBinding+route)
 *   4. --force: re-bind with a fresh session_id (cloud webhook refresh +
 *      local extra update) — mirrors bind_agent's every-run semantics.
 *
 * ABI: stdout = one JSON line {ok,email,system_id,exists?,registered?,
 * config_path?,error?,hint?}; logs → stderr; exit 0 = ok.
 *
 * Usage (from the CLI platform registry):
 *   node <dsh-aimail>/dist/register-cli.js [--system-id S] [--name B]
 *        [--email E] [--manager M] [--session-id U] [--preset mail]
 *        [--local-webhook URL] [--force]
 */
import { randomUUID } from 'node:crypto'
import {
  autoBind,
  resolveRegisterEmail,
  inboundUrl,
  INBOUND_PORTS,
  listSystemDirs,
  loadAgentConfig,
  readSystemConfig,
  registerAddress,
  resolveRegisterWebhook,
  saveBinding,
} from '@aimail/mail-core'

function arg(argv: string[], name: string): string {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1]! : ''
}
function flag(argv: string[], name: string): boolean {
  return argv.includes(name)
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  const systemIdArg = arg(argv, '--system-id')
  const nameArg = arg(argv, '--name')
  const emailArg = arg(argv, '--email')
  const manager = arg(argv, '--manager')
  const sessionId = arg(argv, '--session-id')
  const preset = arg(argv, '--preset') || 'mail'
  const localWebhook =
    arg(argv, '--local-webhook') || inboundUrl(INBOUND_PORTS.dsh)
  const force = flag(argv, '--force')

  const systemId =
    systemIdArg ||
    process.env.AIMAIL_SYSTEM_ID ||
    (await listSystemDirs())[0] ||
    ''
  if (!systemId) {
    console.log(JSON.stringify({ ok: false, error: 'no aimail system found', hint: 'run aimail install first' }))
    return 1
  }
  const gw = await readSystemConfig(systemId)
  const email = resolveRegisterEmail({
    name: nameArg,
    email: emailArg,
    domain: gw.domain || '',
    systemName: gw.system_name || '',
    fallbackBase: 'agent',
  })

  try {
    if (!force) {
      const res = await autoBind({
        systemId,
        email,
        webhookUrl: localWebhook,
        ...(manager ? { managerAddress: manager } : {}),
        extraFields: {
          session_id: sessionId || randomUUID().replace(/-/g, ''),
          preset,
        },
      })
      console.log(
        JSON.stringify({
          ok: true,
          email,
          system_id: systemId,
          ...(res.exists ? { exists: true } : { registered: true }),
          ...(res.config_path ? { config_path: res.config_path } : {}),
        }),
      )
      return 0
    }
    // force re-bind: refresh cloud webhook config, keep/refresh local key,
    // update session extra. Remote address exists → registerAddress refreshes
    // webhook and returns {exists}; local key is carried over.
    const existing = await loadAgentConfig(systemId, email)
    if (!existing || !existing.api_key) {
      // No local binding — plain autoBind path.
      const res = await autoBind({
        systemId,
        email,
        webhookUrl: localWebhook,
        ...(manager ? { managerAddress: manager } : {}),
        extraFields: { session_id: sessionId || randomUUID().replace(/-/g, ''), preset },
      })
      console.log(JSON.stringify({ ok: true, email, system_id: systemId, registered: true, ...(res.config_path ? { config_path: res.config_path } : {}) }))
      return 0
    }
    // secret 捕获为一个值:云端注册与本地落盘必须同一 secret
    // (此前内联表达式生成的 secret 只上送云端,落盘的是旧/空值 → verifySignature 恒 401)
    const webhookSecret = existing.webhook_secret || randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
    // 注册参数三态(环境声明的 push / 显式 pull / 本机端点)与 install 主链一致——
    // force 路径不绕过 webhook_host 语义
    const regUrl = resolveRegisterWebhook(gw, localWebhook)
    const reg = await registerAddress({
      systemId,
      email,
      webhookUrl: regUrl,
      webhookSecret,
      ...(manager ? { managerAddress: manager } : {}),
    })
    void reg // exists:true expected (refresh); new-key path handled above
    const configPath = await saveBinding({
      systemId,
      email,
      apiKey: existing.api_key,
      webhookUrl: localWebhook,
      webhookSecret,
      ...(manager ? { managerAddress: manager } : {}),
      extra: { session_id: sessionId || randomUUID().replace(/-/g, ''), preset },
      gateway: gw,
    })
    // SDK 去桥化(owner 裁决 2026-09-28): no route step here — the SDK speaks no
    // bridge, and the environment master (the `aimail` CLI) pushes the route once
    // this host's inbound is actually serving (`aimail address --inbound-live`).
    console.log(JSON.stringify({ ok: true, email, system_id: systemId, registered: true, config_path: configPath }))
    return 0
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.log(JSON.stringify({ ok: false, error: msg, hint: 'see host plugin state / aimail check' }))
    return 1
  }
}

void main().then((code) => process.exit(code))
