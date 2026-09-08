#!/usr/bin/env node
/**
 * register-cli — CLI-spawnable dsh↔AIMail binding entry (TS home of the
 * retired cli/dsh/bind_agent.py; platform boundary: CLI never implements
 * platform registration, it spawns this entry via the platform registry).
 *
 * Flow (parity with bind_agent.py, Python shared chain replaced by the
 * mail-core chain):
 *   1. systemId: --system-id | AIMAIL_SYSTEM_ID | unique local system
 *   2. email:    --email | emailForAgent('agent', domain, system_name)
 *   3. gateway chain: autoBind (registerAddress+activate+saveBinding+route)
 *   4. --force: re-bind with a fresh session_id (cloud webhook refresh +
 *      local extra update) — mirrors bind_agent's every-run semantics.
 *
 * ABI: stdout = one JSON line {ok,email,system_id,exists?,registered?,
 * config_path?,api_key?,error?,hint?}; logs → stderr; exit 0 = ok.
 *
 * Usage (from the CLI platform registry):
 *   node <dsh-aimail>/dist/register-cli.js [--system-id S] [--email E]
 *        [--manager M] [--session-id U] [--preset mail] [--local-webhook URL]
 *        [--force]
 */
import { randomUUID } from 'node:crypto'
import {
  autoBind,
  emailForAgent,
  listSystemDirs,
  loadAgentConfig,
  readSystemConfig,
  registerAddress,
  registerBridgeRoute,
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
  const emailArg = arg(argv, '--email')
  const manager = arg(argv, '--manager')
  const sessionId = arg(argv, '--session-id')
  const preset = arg(argv, '--preset') || 'mail'
  const localWebhook =
    arg(argv, '--local-webhook') || 'http://127.0.0.1:9099/aimail/inbound'
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
  const email =
    emailArg || emailForAgent('agent', gw.domain || '', gw.system_name || '')

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
    // 注册参数三态(push/pull/无 bridge)与 install 主链一致——force 路径不绕过 webhook_host 语义
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
    await registerBridgeRoute({ systemId, email, webhookUrl: localWebhook })
    console.log(JSON.stringify({ ok: true, email, system_id: systemId, registered: true, config_path: configPath }))
    return 0
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.log(JSON.stringify({ ok: false, error: msg, hint: 'see host plugin state / aimail check' }))
    return 1
  }
}

void main().then((code) => process.exit(code))
