/**
 * register-cli process argv parsing single source (C1: the three host register-cli.ts copies of
 * arg/flag/emit copies merged here, host side only keeps import).
 *
 * Semantics (verbatim identical to the three host original copies, extracted 2026-10-17):
 * - `arg`: `--name value` takes value; missing/trailing without value ⇒ ''.
 * - `flag`: true if name appears (no value).
 * - `emit`: single line JSON + newline written to stdout (register-cli's ABI surface).
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
