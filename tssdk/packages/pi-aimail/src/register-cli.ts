#!/usr/bin/env node
/**
 * register-cli — CLI-spawnable pi↔AIMail binding entry (TS home of the
 * retired inline pi chain in cli/aimail; platform boundary: CLI spawns this
 * entry via the platform registry, never implements pi registration).
 *
 * Flow (parity with the former CLI pi branch):
 *   1. systemId: --system-id | AIMAIL_SYSTEM_ID | unique local system
 *   2. email:    --email | emailForAgent('pi', domain, system_name)
 *   3. gateway chain: autoBind (registerAddress+activate+saveBinding+route)
 *   4. pointer ~/.pi/.agentmail written (platform home: AIMAIL_SYSTEM_HOME
 *      or ~/.pi) — pi extensions read identity from it.
 *
 * ABI: stdout = one JSON line {ok,email,system_id,exists?,registered?,
 * config_path?,error?,hint?}; logs → stderr; exit 0 = ok.
 *
 * Usage (from the CLI platform registry):
 *   node <pi-aimail>/dist/register-cli.js [--system-id S] [--email E]
 *        [--manager M] [--local-webhook URL]
 */
import * as fs from 'node:fs/promises'
import * as os from 'node:os'
import * as path from 'node:path'
import {
  autoBind,
  emailForAgent,
  listSystemDirs,
  readSystemConfig,
} from '@aimail/mail-core'

function arg(argv: string[], name: string): string {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1]! : ''
}

async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  const systemIdArg = arg(argv, '--system-id')
  const emailArg = arg(argv, '--email')
  const manager = arg(argv, '--manager')
  const localWebhook =
    arg(argv, '--local-webhook') || 'http://127.0.0.1:9101/aimail/inbound'

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
  const email =
    emailArg || emailForAgent('pi', gw.domain || '', gw.system_name || '')

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
