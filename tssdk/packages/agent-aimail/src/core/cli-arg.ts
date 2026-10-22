/**
 * register-cli 进程 argv 解析单源(C1: 三宿主 register-cli.ts 各自的
 * arg/flag/emit 副本合并于此, 宿主侧只留 import)。
 *
 * 语义(与三宿主原副本逐字一致, 2026-10-17 提取):
 * - `arg`: `--name value` 取 value; 缺失/尾随无值 ⇒ ''。
 * - `flag`: 名字出现即 true(无值)。
 * - `emit`: 单行 JSON + 换行写到 stdout(register-cli 的 ABI 面)。
 */

export function arg(argv: string[], name: string): string {
  const i = argv.indexOf(name)
  return i >= 0 && i + 1 < argv.length ? argv[i + 1]! : ''
}

export function flag(argv: string[], name: string): boolean {
  return argv.includes(name)
}

export function emit(obj: Record<string, unknown>): void {
  process.stdout.write(JSON.stringify(obj) + '\n')
}
