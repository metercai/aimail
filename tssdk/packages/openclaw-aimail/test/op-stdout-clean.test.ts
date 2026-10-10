// 契约 v1.0 §4.1(2) 回归钉子:op 路径(--op/--args)的 stdout 必须**恰一行 JSON**。
//
// 缺陷(B3 后 openclaw L2 实跑,J5-7a/7d 红):
//   openclaw 的 op 路径经**完整插件 register(api)** 派发(不像 dsh/pi 走独立
//   register-cli.ts)。register(api) 里的宿主生命周期诊断(startInboundPull /
//   ensureSystem / inbound notify)用 console.log 打 **stdout** ⇒ op 成功(rc=0)
//   但 stdout 有 7 行 ⇒ CLI Transport 判"可执行门输出异常(rc=0, 7 行)" ⇒ 门红。
//   首行实证:`[aimail-pull] agent@…: not polling (not-agent-scope) — push/system
//   binding unchanged`。
//
// 本测试把"op 路径 boot 插件后 stdout 零诊断行"在本地钉住:
//   ① register(api) 全程(含异步 settle)**不得调用 console.log**(stdout 干净);
//   ② 诊断行允许走 console.error(stderr)—— 契约只约束 stdout。
//   op 结果 JSON 走命令 action 的 process.stdout.write,不在此 register 路径上。
import { describe, it, expect, vi, afterEach } from 'vitest'

describe('openclaw-aimail op-path stdout cleanliness (契约 §4.1(2))', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('register(api) emits ZERO console.log (stdout stays clean for the op path)', async () => {
    const logSpy = vi.spyOn(console, 'log').mockImplementation(() => {})
    const errSpy = vi.spyOn(console, 'error').mockImplementation(() => {})
    const warnSpy = vi.spyOn(console, 'warn').mockImplementation(() => {})

    const mod = (await import('../src/index.ts')) as {
      default?: { register?: (api: unknown) => void }
    }
    const entry = mod.default
    expect(typeof entry?.register).toBe('function')

    const api = {
      registerTool(fn: (ctx: unknown) => unknown[] | unknown) {
        const list = fn({ agentId: 'main' })
        return Array.isArray(list) ? list.length : 1
      },
      registerHttpRoute() {},
      registerCommand() {},
      registerCli() {},
    }
    entry!.register!(api)

    // register 内部 fire-and-forget 了 startInboundPull / ensureSystem /
    // inbound notify(都是 void 异步)。给它们足够 macrotask 轮次把诊断行排空,
    // 再断言 —— 否则异步诊断行会在断言之后才落地,测试假绿。
    for (let i = 0; i < 20; i++) {
      await new Promise((r) => setTimeout(r, 10))
    }

    // 契约:op 路径 stdout 零诊断行 ⇒ register 全程不得 console.log。
    expect(logSpy).not.toHaveBeenCalled()
    // 诊断行允许落 stderr(console.error)—— 契约只约束 stdout,不在此断言次数。
    void errSpy
    void warnSpy
  })
})
