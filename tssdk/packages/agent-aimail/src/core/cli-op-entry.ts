/**
 * register-cli op entry skeleton (C2: three host register-cli.ts op branches are verbatim isomorphic,
 * extracted as single source on 2026-10-17).
 *
 * Contract v1.0 §4.1(2): `node <register-cli.js> --op <op> --args '<json>'`.
 * Single-line JSON envelope (same shape as python gate):
 *   Success  {ok:true,result:{…}}                exit 0
 *   usage {ok:false,kind:'usage',exc,error}   exit 2
 *   call  {ok:false,kind:'call',exc,error}    exit 1
 * diagnostics → stderr.
 *
 * Host differences converge to two hooks:
 * - `assembleExtra`: host layout data appended to opAssemble parameters (dsh = skill exposure;
 *   merge order `{ ...args, ...extra }` is verbatim consistent with original dsh = extra overrides args).
 * - `onNoOp`: host behavior when no `--op` (dsh/pi = legacy binding branch;
 *   openclaw = usage error, no legacy).
 */
import {
  opAssemble,
  opPromptTest,
  opTeardown,
  opUpdate,
  UsageError,
} from './sdk-ops.js'
import { arg, emit } from './cli-arg.js'

export interface OpEntryHooks {
  /** opAssemble appended parameters (host layout data); default = not appended. */
  assembleExtra?: () => Record<string, unknown>
  /** Called when no `--op` (host legacy branch or usage error), returns exit code. */
  onNoOp: (argv: string[]) => Promise<number> | number
}

export async function runOpEntry(argv: string[], hooks: OpEntryHooks): Promise<number> {
  const opIdx = argv.indexOf('--op')
  if (opIdx < 0) {
    return hooks.onNoOp(argv)
  }
  const op = argv[opIdx + 1] || ''
  const argsRaw = arg(argv, '--args') || '{}'
  let args: Record<string, unknown>
  try {
    args = JSON.parse(argsRaw) as Record<string, unknown>
    if (!args || typeof args !== 'object' || Array.isArray(args)) throw new Error('not a JSON object')
  } catch (e) {
    emit({
      ok: false,
      kind: 'usage',
      exc: 'UsageError',
      error: `--args is not valid JSON: ${e instanceof Error ? e.message : String(e)}`,
    })
    return 2
  }
  try {
    const result =
      op === 'assemble'
        ? await opAssemble({ ...args, ...(hooks.assembleExtra ? hooks.assembleExtra() : {}) })
        : op === 'update'
          ? await opUpdate(args)
          : op === 'teardown'
            ? await opTeardown(args)
            : op === 'prompt-test'
              ? await opPromptTest(args)
              : null
    if (!result) {
      emit({
        ok: false,
        kind: 'usage',
        exc: 'UsageError',
        error: `unknown op '${op}' (expected assemble|update|teardown|prompt-test)`,
      })
      return 2
    }
    emit({ ok: true, result })
    return 0
  } catch (e) {
    // Same shape as python gate: usage ⇒ error=pure message; call ⇒ error="<Cls>: <msg>"
    // (e.name of JS subclass instances is always 'Error', must use e.constructor.name — corresponds to python type(e).__name__)
    if (e instanceof UsageError) {
      process.stderr.write(`register-cli: ${op} failed: ${e.message}\n`)
      emit({ ok: false, kind: 'usage', exc: 'UsageError', error: e.message })
      return 2
    }
    const exc = e instanceof Error ? e.constructor.name : 'Error'
    const msg = e instanceof Error ? e.message : String(e)
    process.stderr.write(`register-cli: ${op} failed: ${exc}: ${msg}\n`)
    emit({ ok: false, kind: 'call', exc, error: `${exc}: ${msg}` })
    return 1
  }
}
