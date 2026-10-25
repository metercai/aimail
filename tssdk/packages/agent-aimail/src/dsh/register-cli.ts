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
import { cliArg as arg, cliFlag as flag } from '../core/index.js'
import { hostLayoutArgs } from './host-layout.js'
import {
  autoBind,
  ensureSkillToolExposure,
  runOpEntry,
  resolveRegisterEmail,
  inboundUrl,
  INBOUND_PORT,
  listSystemDirs,
  loadAgentConfig,
  readSystemConfig,
  registerAddress,
  resolveRegisterWebhook,
  saveBinding,
} from '../core/index.js'

/** dsh host layout data (preset + skill exposure) -- single source in ./host-layout.ts. */
async function main(): Promise<number> {
  const argv = process.argv.slice(2)
  // Contract v1.0 §4.1(2): op entry format (CLI invokes via registry ops.argv) --
  //   node <register-cli.js> --op <assemble|update|teardown|prompt-test> --args '<json>'
  // Single-line JSON envelope {ok,result|error}, same shape as python gate. op branch single source = mail-core
  // runOpEntry (C2 2026-10-17); dsh difference = opAssemble attaches host layout data (preset + skill exposure).
  return runOpEntry(argv, {
    assembleExtra: hostLayoutArgs,
    onNoOp: () => legacyBind(argv),
  })
}

/** legacy binding branch (no `--op`, old registry form; preserved as-is after C2 skeletonization). */
async function legacyBind(argv: string[]): Promise<number> {
  const systemIdArg = arg(argv, '--system-id')
  const nameArg = arg(argv, '--name')
  const emailArg = arg(argv, '--email')
  const manager = arg(argv, '--manager')
  const sessionId = arg(argv, '--session-id')
  const preset = arg(argv, '--preset') || 'mail'
  const localWebhook =
    arg(argv, '--local-webhook') || inboundUrl(INBOUND_PORT)
  const force = flag(argv, '--force')

  // Assembly: enable this host's skill/tool exposure layer. Implementation is shared (mail-core ensureSkillToolExposure),
  // here only provides dsh host layout data. Failure is loud but does not block binding (CLI register_default converts to warn).
  let exposureRc = 0
  try {
    const _exp = hostLayoutArgs()
    const exp = ensureSkillToolExposure({
      patchFile: _exp.skill_expose_patch,
      ids: _exp.skill_expose_ids.split(' '),
    })
    console.error(
      `register-cli: skill exposure ${exp.changed ? 'assembled' : 'already in place'} → ${exp.patch_file}`,
    )
  } catch (e) {
    exposureRc = 1
    console.error(`register-cli: skill exposure FAILED: ${e instanceof Error ? e.message : String(e)}`)
  }

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
      return exposureRc
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
    // secret captured as a single value: cloud registration and local disk write must use the same secret
    // (Previously, the secret generated by the inline expression was only sent to the cloud, while the persisted value was old/empty -> verifySignature always 401)
    const webhookSecret = existing.webhook_secret || randomUUID().replace(/-/g, '') + randomUUID().replace(/-/g, '')
    // Registration parameter tri-state (environment-declared push / explicit pull / local endpoint) is consistent with the install main chain --
    // force path does not bypass webhook_host semantics
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
    // SDK de-bridging (owner decision 2026-09-28): no route step here — the SDK speaks no
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
