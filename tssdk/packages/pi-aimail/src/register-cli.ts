#!/usr/bin/env node
/**
 * register-cli — CLI-spawnable pi↔AIMail binding entry (TS home of the
 * retired inline pi chain in cli/aimail; platform boundary: CLI spawns this
 * entry via the platform registry, never implements pi registration).
 *
 * Flow (parity with the former CLI pi branch):
 *   1. systemId: --system-id | AIMAIL_SYSTEM_ID | unique local system
 *   2. email:    --name <base> | --email | platform default base
 *                (resolveRegisterEmail — same derivation as the Python
 *                 plan_address_name; CLI passes the planned base via --name)
 *   3. gateway chain: autoBind (registerAddress+activate+saveBinding+route)
 *   4. pointer ~/.pi/.agentmail written (platform home: AIMAIL_SYSTEM_HOME
 *      or ~/.pi) — pi extensions read identity from it.
 *
 * ABI: stdout = one JSON line {ok,email,system_id,exists?,registered?,
 * config_path?,error?,hint?}; logs → stderr; exit 0 = ok.
 *
 * Usage (from the CLI platform registry):
 *   node <pi-aimail>/dist/register-cli.js [--system-id S] [--name B]
 *        [--email E] [--manager M] [--local-webhook URL]
 */
import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  autoBind,
  opAssemble,
  opPromptTest,
  opTeardown,
  opUpdate,
  UsageError,
  resolveRegisterEmail,
  inboundUrl,
  INBOUND_PORTS,
  listSystemDirs,
  readSystemConfig,
} from '@aimail/mail-core'

function arg(argv: string[], name: string): string {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1]! : ''
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  // 契约 v1.0 §4.1(2):op 入口形态(CLI 经注册表 ops.argv 调)——
  //   node <register-cli.js> --op <assemble|update|teardown> --args '<json>'
  const opIdx = argv.indexOf('--op')
  if (opIdx >= 0) {
    const op = argv[opIdx + 1] || ''
    const argsRaw = arg(argv, '--args') || '{}'
    let args: Record<string, unknown>
    try {
      args = JSON.parse(argsRaw) as Record<string, unknown>
      if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('not a JSON object')
    } catch (e) {
      process.stdout.write(
        JSON.stringify({ ok: false, kind: 'usage', exc: 'UsageError', error: `--args is not valid JSON: ${e instanceof Error ? e.message : String(e)}` }) + '\n',
      )
      return 2
    }
    try {
      const result =
        op === 'assemble' ? await opAssemble(args)
        : op === 'update' ? await opUpdate(args)
        : op === 'teardown' ? await opTeardown(args)
        : op === 'prompt-test' ? await opPromptTest(args)
        : null
      if (!result) {
        process.stdout.write(
          JSON.stringify({ ok: false, kind: 'usage', exc: 'UsageError', error: `unknown op '${op}'(期望 assemble|update|teardown|prompt-test)` }) + '\n',
        )
        return 2
      }
      process.stdout.write(JSON.stringify({ ok: true, result }) + '\n')
      return 0
    } catch (e) {
      // 与 python 门同形:usage ⇒ error=纯消息;call ⇒ error="<Cls>: <msg>"(JS 子类实例的
      // e.name 恒为 'Error',须用 e.constructor.name —— 对应 python type(e).__name__)
      if (e instanceof UsageError) {
        process.stderr.write(`register-cli: ${op} failed: ${e.message}\n`)
        process.stdout.write(JSON.stringify({ ok: false, kind: 'usage', exc: 'UsageError', error: e.message }) + '\n')
        return 2
      }
      const exc = e instanceof Error ? e.constructor.name : 'Error'
      const msg = e instanceof Error ? e.message : String(e)
      process.stderr.write(`register-cli: ${op} failed: ${exc}: ${msg}\n`)
      process.stdout.write(JSON.stringify({ ok: false, kind: 'call', exc, error: `${exc}: ${msg}` }) + '\n')
      return 1
    }
  }
  const systemIdArg = arg(argv, '--system-id')
  const nameArg = arg(argv, '--name')
  const emailArg = arg(argv, '--email')
  const manager = arg(argv, '--manager')
  const localWebhook =
    arg(argv, '--local-webhook') || inboundUrl(INBOUND_PORTS.pi)

  const systemId =
    systemIdArg ||
    process.env.AIMAIL_SYSTEM_ID ||
    (await listSystemDirs())[0] ||
    ''
  if (!systemId) {
    console.log(
      JSON.stringify({ ok: false, error: 'no aimail system found', hint: 'run aimail install first' }),
    )
    return 1
  }
  const gw = await readSystemConfig(systemId)
  const email = resolveRegisterEmail({
    name: nameArg,
    email: emailArg,
    domain: gw.domain || '',
    systemName: gw.system_name || '',
    fallbackBase: 'pi',
  })

  try {
    const res = await autoBind({
      systemId,
      email,
      webhookUrl: localWebhook,
      ...(manager ? { managerAddress: manager } : {}),
      extraFields: { agent_id: 'pi' },
    })
    // platform pointer — pi extensions read identity from ~/.pi/.agentmail
    const platformHome =
      (process.env.AIMAIL_SYSTEM_HOME || '').trim() ||
      path.join(os.homedir(), '.pi')
    await fs.mkdir(platformHome, { recursive: true })
    await fs.writeFile(
      path.join(platformHome, '.agentmail'),
      JSON.stringify({ system_id: systemId, email }, null, 2) + '\n',
      { mode: 0o600 },
    )
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
  } catch (e) {
    const msg = e instanceof Error ? e.message : String(e)
    console.log(
      JSON.stringify({ ok: false, error: msg, hint: 'see host plugin state / aimail check' }),
    )
    return 1
  }
}

void main().then((code) => process.exit(code))
