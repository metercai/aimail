#!/usr/bin/env node
/**
 * register-cli — CLI-spawnable openclaw↔AIMail op entry (standalone executable entry).
 *
 * Contract v1.0 §4.1(2) (owner ruling 2026-10-11): node transport **always** via platform registrar
 * `node_entry`, `openclaw aimail <cmd>` (host_command) form is deprecated. CLI is
 *
 *   node <openclaw-aimail>/dist/register-cli.js --op <op> --args '<json>'
 *
 * calls this file. This file **only imports `@aimail/mail-core`**, does not load openclaw plugin runtime
 * ⇒ host lifecycle diagnostics (arm pull / ensureSystem / inbound notify) cannot land on stdout,
 * single-line JSON envelope is structurally guaranteed (old host_command path dispatched via register(api),
 * once printed 7 lines of diagnostics to stdout ⇒ CLI judged "executable gate output abnormal").
 *
 * ABI (same shape as python gate, dsh/pi entry):
 *   stdout exactly one line JSON: success `{ok:true,result:{…}}`; failure
 *   `{ok:false,kind:"usage"|"import"|"call",exc:"<Cls>",error:"<msg>"}`；
 *   diagnostics → stderr; exit 0 / 2 (usage) / 1 (other).
 *
 * Registration semantics are in mail-core op implementation (naming/derivation/registration/renaming/binding persistence/deregistration), this file only does
 * argv parsing and envelope output —— The CLI does not implement platform adaptation; platform packages only provide host layout data.
 */
import {
  runOpEntry,
  cliEmit as emit,
} from '../core/index.js'

const OPS = 'assemble|update|teardown|prompt-test'

/** Single-line JSON envelope + usage/call two types of error classification (same shape as python gate).
 *  op branch single source = mail-core runOpEntry(C2 2026-10-17); openclaw has no legacy branch,
 *  no `--op` ⇒ usage error. */
export async function main(argv: string[] = process.argv.slice(2)): Promise<number> {
  return runOpEntry(argv, {
    onNoOp: () => {
      emit({
        ok: false,
        kind: 'usage',
        exc: 'UsageError',
        error: `usage: node register-cli.js --op <${OPS}> --args '<json>'`,
      })
      return 2
    },
  })
}

// Self-run only when this file is the executed entry; do not execute when imported (unit tests).
if (process.argv[1] && /register-cli\.(js|ts)$/.test(process.argv[1])) {
  void main().then((code) => process.exit(code))
}
