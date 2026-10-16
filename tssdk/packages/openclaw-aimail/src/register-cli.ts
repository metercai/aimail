#!/usr/bin/env node
/**
 * register-cli — CLI-spawnable openclaw↔AIMail op entry（独立可执行入口）。
 *
 * 契约 v1.0 §4.1(2)（owner 2026-10-11 裁定）：node transport **一律**经平台注册器
 * `node_entry`，`openclaw aimail <cmd>`(host_command) 形态废弃。CLI 以
 *
 *   node <openclaw-aimail>/dist/register-cli.js --op <op> --args '<json>'
 *
 * 调用本文件。本文件**只 import `@aimail/mail-core`**，不加载 openclaw 插件运行时
 * ⇒ 宿主生命周期诊断(arm pull / ensureSystem / inbound notify)不可能落到 stdout，
 * 单行 JSON 信封在结构上得到保证（旧 host_command 路径经 register(api) 派发，
 * 曾把 7 行诊断打进 stdout ⇒ CLI 判"可执行门输出异常"）。
 *
 * ABI（与 python 门、dsh/pi 入口同形）：
 *   stdout 恰一行 JSON：成功 `{ok:true,result:{…}}`；失败
 *   `{ok:false,kind:"usage"|"import"|"call",exc:"<Cls>",error:"<msg>"}`；
 *   诊断 → stderr；exit 0 / 2(usage) / 1(其它)。
 *
 * 注册语义在 mail-core 的 op 实现内（定名/派生/注册/改名/落绑定/注销），本文件只做
 * argv 解析与信封输出 —— CLI 不实现平台适配，平台包只提供宿主布局数据。
 */
import {
  runOpEntry,
  cliEmit as emit,
} from '@aimail/mail-core'

const OPS = 'assemble|update|teardown|prompt-test'

/** 单行 JSON 信封 + usage/call 两类错误分类(与 python 门同形)。
 *  op 分支单源 = mail-core runOpEntry(C2 2026-10-17);openclaw 无 legacy 分支,
 *  无 `--op` ⇒ usage 错误。 */
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

// 仅当本文件是被执行的入口时自跑；被 import（单测）时不执行。
if (process.argv[1] && /register-cli\.(js|ts)$/.test(process.argv[1])) {
  void main().then((code) => process.exit(code))
}
