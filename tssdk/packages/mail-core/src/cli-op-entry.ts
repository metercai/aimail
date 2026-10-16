/**
 * register-cli op 入口骨架(C2: 三宿主 register-cli.ts 的 op 分支逐字同构,
 * 2026-10-17 提取为单源)。
 *
 * 契约 v1.0 §4.1(2): `node <register-cli.js> --op <op> --args '<json>'`。
 * 单行 JSON 信封(与 python 门同形):
 *   成功  {ok:true,result:{…}}                exit 0
 *   usage {ok:false,kind:'usage',exc,error}   exit 2
 *   call  {ok:false,kind:'call',exc,error}    exit 1
 * 诊断 → stderr。
 *
 * 宿主差异收敛为两个 hook:
 * - `assembleExtra`: opAssemble 参数附加的宿主布局数据(dsh = skill exposure;
 *   merge 顺序 `{ ...args, ...extra }` 与原 dsh 逐字一致 = extra 覆盖 args)。
 * - `onNoOp`: 无 `--op` 时的宿主行为(dsh/pi = legacy 绑定分支;
 *   openclaw = usage 错误,无 legacy)。
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
  /** opAssemble 附加参数(宿主布局数据);缺省 = 不附加。 */
  assembleExtra?: () => Record<string, unknown>
  /** 无 `--op` 时调用(宿主 legacy 分支或 usage 错误),返回 exit code。 */
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
        error: `unknown op '${op}'(期望 assemble|update|teardown|prompt-test)`,
      })
      return 2
    }
    emit({ ok: true, result })
    return 0
  } catch (e) {
    // 与 python 门同形:usage ⇒ error=纯消息;call ⇒ error="<Cls>: <msg>"
    // (JS 子类实例的 e.name 恒为 'Error',须用 e.constructor.name —— 对应 python type(e).__name__)
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
