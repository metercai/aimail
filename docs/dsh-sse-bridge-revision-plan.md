# DSH 侧 AIMail SSE 桥接修订方案

> **已被 `docs/dsh-mail-architecture-v2.md` 取代**（2026-11-14）：v2 按“地址绑定 workspace、dsh 只保留 SSE、一个 Hub 路由”重新设计；本文保留为第一轮诊断/执行细节的历史记录，其中“绑定到 session”“push+SSE 双传输”的结论不再采用。
>
> 状态：**待评审**（评审通过后再动代码）
> 范围：`tssdk/packages/agent-aimail`（dsh 适配层为主，core 只做必要的生命周期/传输修正）
> 目标版本：`agent-aimail 0.2.13`（minor）
> 对应文档：`DSH_SSE桥接插件.md`（两个核心点：非阻塞 SSE + 收到消息后唤起 Session）
> 日期：2026-11-14

---

## 0. 结论（先看这一段）

当前 dsh 适配层**不是**按「Host 半插件 + 后台 SSE + 唤起 session」的模型实现的，而是：

1. 一个 `node:http` 推送监听器（只对 system/bridge 绑定有意义）；
2. 一个在 `apply()` 之后用 `void ...then(...)` **游离启动**的拉取循环（底层确实是 SSE）；
3. 收到邮件后优先 `followup` 一个已绑定的 `session_id`，但 **bind 流程从不写 `session_id`**，于是几乎总是走「新建一次性 session」分支。

这带来三个直接后果，也正是本次要修的：

- **绑定不可用/假成功**：`aimail_address(bind)` 在 dsh 里被前置的 `mail.resolveCtx()` 挡住，未绑定会话根本到不了 handler；地址已绑定时又直接返回 `already_bound`，**不会把当前会话关联进去**，用户等邮件等不到。
- **邮件唤不醒当前对话**：agent-scope 地址没有 webhook（网关存 `webhook_url=NULL`），唯一入站通道就是 SSE 拉流；邮件到了以后，因为 binding 没有 `session_id`，插件会 `agents.create()` 一个**全新的一次性会话**（cwd 还是 dsh profile 目录），而不是唤醒用户正在看的那个对话框。
- **生命周期不可靠**：SSE/fetch/read 没有接 `AbortController`，插件重载/卸载时旧连接最长要等 30s 才断；新实例再连会撞上网关的单槽位 `409 occupied` 并退避。这期间邮件不动，界面看起来就是「挂起、长时间等待」。

> **证据说明**：本机没有复现到「无限挂起」，所以下面把「已验证」和「高概率、待复现」分开写。
> 已验证的是：bind 到不了 handler、`already_bound` 不写 session、入站新建一次性 session、SSE 不可中断。
> 高概率的是：重载/HMR 的 409 退避窗口、UI 侧把「邮件没投到当前对话框」感知成挂起。
> 另外本机 00:10 有一个只有 session 头、没有任何 user message 的会话（`7faa9a67…`），**疑似**就是被挂起的对话框；不作定论。

修订后的目标形态：

```
┌──────────────────────── dsh Host 进程（一个 profile） ────────────────────────┐
│  mail-service (ctx.mail, 只读本地 binding)                                    │
│  tools (15 个工具；aimail_address 不再前置 resolve)                            │
│  inbound (HTTP push，仅 system/bridge 绑定)                                   │
│  session-bridge (新增)                                                        │
│    ├─ ctx.effect(): 启动/托管后台 SSE 循环（fire-and-forget，可 abort）        │
│    ├─ SSE 事件 → 预处理 → 去重 → 串行队列                                      │
│    └─ agents.get(session_id) ?? agents.resume(...) → agent.followup(msg)      │
│         ↑ 只投递即返回；turn/end 旁路监听，不阻塞                               │
└───────────────────────────────────────────────────────────────────────────────┘
```

**SSE 连接属于 Host/插件，属于这个 profile 的后台资源；它不属于任何一次对话、任何一次 tool call。**
对话侧只做两件事：`bind` 时把 `session_id` 写进 binding；入站时接收一次 `followup`。

---

## 1. 项目结构速览（本次阅读结论）

- `cli/`：Rust CLI（`aimail install / address / agent / check`），负责环境安装、系统注册、诊断。
- `pysdk/`：Python SDK + MCP server，是 hermes / deer-flow 等宿主的能力面。
- `bridge/`：aimail-bridge 的发布产物（独立 Rust 进程），负责公网 webhook ↔ 本地/网关的转发；`docs/api-deps.md` 里注明它有 SSE 拉流 + 长轮询回退。
- `tssdk/packages/agent-aimail/`：单个 npm 包，同时服务 openclaw / dsh / pi：
  - `src/core/`：host 无关核心（网关客户端、15 个工具、入站预处理、HMAC、auto-bind、地址生命周期、SSE/poll 拉流）；
  - `src/openclaw/`、`src/dsh/`、`src/pi/`：各宿主适配层；
  - `resources/`：board 角色提示词 + agentmail skill，打包时 stage 进来。 <!-- contract-allowed: design/acceptance doc must quote the contract surface verbatim -->
- `contract/`：跨语言契约（路径、端口、字段）。
- `docs/agent-adapter-playbook*.md`：新增宿主适配的规范；dsh 的 3 个 bundle 子路径（`mail-service` / `tools` / `inbound`）就是在 `cordis.patch.yml` 里自挂载的。

dsh 的加载方式：`dsh plugin --profile <p> add agent-aimail` 把包装进 profile，bundle patch 追加 3 个 cordis 行；DSH 启动时按行 `apply(ctx, config)`。异步 `apply()` 会被等待；`apply()` 返回 Promise 之外的**游离异步任务一旦产生未捕获 rejection，DSH 会致命退出**（0.2.2-rc.1 app-boot 契约）。这两点决定了 SSE 必须挂在 `ctx.effect` 里、并且所有异步都要自己兜住错误。

---

## 2. 现状链路（dsh）

### 2.1 启动

`src/dsh/inbound.ts:316` `apply()`：

1. 同步做 SOUL / skills provider 设置、skill 文件复制、`createServer()`；
2. `void ensureDefaultAgentBinding().then(async () => { ... })`（`inbound.ts:391`）：
   - `await listenWithRetry(server, host, port)` 才真正监听 9099；
   - 通知环境（`notifyInboundForSystem('live')`）；
   - `void startInboundPull(ctx, ...)` 启动拉流（`inbound.ts:428`）。
3. 返回 dispose（`inbound.ts:450`）：`notify down` + `pullHandles.stop()` + `server.close()`。

### 2.2 入站投递

`src/dsh/inbound.ts:124` `deliverInbound()`：

- `processInboundMail()` → `createUserMessage()`；
- `agents.get(cfg.session_id)`：活着就 `followup`；
- 否则 `agents.resume({ resumeSessionId })`：恢复并 `followup`；
- 再否则 `agents.create({ sessionId: randomUUID(), meta: { cwd: process.cwd() }, agentOptions })`，投递后 `whenIdle()` 再把 binding 的 `session_id` 清掉、`handle.dispose()`（`inbound.ts:181-199`）。

### 2.3 工具解析

`src/dsh/tools.ts:101-123`：每个工具执行前都先 `mail.resolveCtx(exec.agent.id)`；
`src/dsh/mail-service.ts:172-188`：`resolveCtx` 未命中时调用 `tryAutoBindSession()`（`mail-service.ts:78-108`），它会：
- 用 `agent-<session8>@<domain>` 派生一个**新地址**；
- 走 `autoBind`（读 system config、register、webhook 同步等，全部网络调用）；
- 成功后重新 `resolveBySessionId`；失败则把最初那个 "unbound" 错误抛回。

`aimail_address` 也是这套循环里的工具，所以**未绑定会话里它根本到不了 handler**（先被 resolve 拦住）。

### 2.4 地址生命周期

`src/core/address-code.ts:1004` `aimailAddress()`：
- 已绑定且带 key → 直接 `already_bound` 返回（`address-code.ts:1025`），不写任何东西；
- 有归档 → 恢复（`1036`）；
- 新地址 → `activateAddressCodePersist()`（`216`）→ 写 binding → `fireBindingCreated(cfg)`（`261`）。
- `session_id` 只在**投递侧**临时写入，bind 侧从不写。

### 2.5 拉流

`src/dsh/inbound.ts:267` `startInboundPull()` → `core/poll-entry.ts:271` `startAgentPullEntries()` → `startAgentPullEntry()`（`186`）→ `address-code.ts:493` `startPolling()`：
- 默认 `auto`：先试 SSE `GET /api/v1/admin/pending/stream`，不可用降级长轮询；
- SSE 读循环在 `core/gateway.ts:221` `streamRound()`，解析器是 `SseParser`（与 bridge/py 三端对齐）；
- 收到 `mail` 批次 → 逐条 `onEmail` → 成功才 ack。

---

## 3. 问题定位（根因，按影响排序）

> 证据强度：A/B/C/E 为**代码+本机数据已验证**；D 为**代码确定、409 窗口未实测**（高概率）。

### 根因 A：bind 链路是「先解析、后绑定」的死结

`tools.ts` 对**所有**工具统一 `resolve(exec)`。未绑定会话里：

```
aimail_address(bind)
  → tools.ts resolve()
  → mail.resolveCtx(sessionId)
  → resolveBySessionId() 失败
  → tryAutoBindSession()（有系统 key 时是网络调用：register/webhook sync，单次 30s 超时）
  → autoBind 失败（agent-scope 机器通常没有 admin_key）
  → 抛出最初的 unbound 错误
  → handler 没执行
```

所以「在对话框里直接绑定」的结果是：**handler 没执行**。本机（agent-scope、没有 admin_key）会快速失败；如果机器上恰好配了系统 key，则可能先被自动绑到一个**派生出来的 `agent-<session8>@…` 地址**——不是用户给的那个地址，而且中途要走多次网关调用。

> 证据强度：**已验证**（代码路径 + 本机 `aimail_gateway.json` 无 admin_key）。
> 说明：这一条解释的是「bind 无效」，不是「长时间等待」；后者主要由 B、D 解释。

证据：本机 `~/.aimail/systems/shared_addr_default/aimail_gateway.json` 只有 `scope: agent`，没有 admin_key；`agentmail.json` 里也没有 `session_id`。 <!-- contract-allowed: design/acceptance doc must quote the contract surface verbatim -->

### 根因 B：`already_bound` 是假成功，binding 与 session 没有关联

即使地址已经由 CLI 绑定（本机就是 `meterv@aimail.token.tm`），在对话框里再 bind 只会得到 `already_bound`，`address-code.ts:1025` 直接 return：
- 不写 `session_id`；
- 不写 profile pointer；
- 不触发任何热挂载。

于是入站时 `cfg.session_id` 永远为空，`deliverInbound` 只能走「新建一次性 session」分支。**用户在当前对话框等，邮件却出现在另一个新会话里。**

### 根因 C：一次性 session 的语义与「唤起当前会话」相反

`inbound.ts:181-199`：每封未绑定邮件 `agents.create()` 一个新 session，`cwd = process.cwd()`（桌面端实测是 `/Users/metercai/.dsh/profiles/desktop`），turn 结束后立刻清掉 `session_id` 并 `dispose()`。结果是：
- 每来一封邮件就多一个对话框；
- 这些对话框不在用户 workspace 里；
- 用户正在看的对话框永远不会被「唤醒」。

本机实测（`~/.dsh/sessions/--Users-metercai-.dsh-profiles-desktop--/62617ec1-…`）：
- 00:11 收到 `happy new year！`，自动建了新 session，cwd 是 profile 目录，模型正常回信并 `turn/end completed`；
- 同一时间前 2 分钟（00:10）存在一个只有 session 头、**没有任何 user message** 的会话 `7faa9a67…`（疑似就是被挂起的那个对话框）。

这说明收信链路本身是通的，**坏的是「投递到哪个会话」**。

### 根因 D：SSE 生命周期没有托管，重载/卸载时断不干净

- `apply()` 没有 `ctx.effect`；SSE/pull 是在 `void` 链里起的，dispose 只 stop 已经拿到的 handle（`inbound.ts:450-470`），如果 dispose 发生在 `startInboundPull` 完成之前，循环会漏掉。
- `gateway.ts:221` `streamRound()` 的 `fetch` **没有传 `signal`**，`reader.read()` 也没有 abort 监听；`signal` 只在每轮循环开头检查。卸载时最多要等一次读超时（`min(30s, 90s)`）才会退出。
- 结果是：插件重载/改配置后，旧连接还占着网关的**单槽位**，新实例连上去收到 `409 occupied`，按退避重试（1/2/4/…/30s）。这段时间没有邮件投递，看起来就是「挂起」。
- `withReadTimeout()`（`gateway.ts:91`）本身也没有清理 abort 监听；长轮询 `pullList` 的 50s hold 同样不可中断。

### 根因 E：`ctx.effect` / `session/event` 都没有用起来

- DSH 0.2.2 的 `ctx.effect(setup)` 语义是：setup **同步执行**、返回 disposer；插件卸载/HMR 时自动调用。SSE、HTTP listener、拉流 handle 都应该挂在这里。
- `ctx.on('session/event', (session, event) => …)`（`event.type === 'turn/end'`、`event.data.reason.kind`）是旁路观察回合结束的正确位置；当前实现完全没有，排障只能靠日志猜。
- 游离异步任务的 rejection 在 DSH 是**致命**的。当前代码大多加了 `.catch`，但 `void ensureDefaultAgentBinding().then(...)` 里还有 `await listenWithRetry` 等，后续任何一处漏 catch 都会把整个 app 带下去。

---

## 4. 与《DSH SSE 桥接插件》的逐条对照

| 文档要求 | 当前 dsh 实现 | 结论 |
|---|---|---|
| `apply` 保持同步，连接 `void` 启动 | `apply` 同步，但连接在 `void ...then` 里，且被 `ensureDefaultAgentBinding` 门控 | 部分符合；启动顺序不可控 |
| `ctx.effect` 托管连接 + dispose `abort()` | 未使用 `ctx.effect`；dispose 只关 server/stop 已拿到的 handle | **不符合** |
| `AbortController` 传给 `fetch`/`read` | `streamRound` 的 fetch/read 都没接 signal | **不符合** |
| buffer 累积 + 空行切分 + 多行 data | `SseParser` 已实现（三端对齐） | 符合 |
| 指数退避、封顶、可中断 | `startPolling` 有退避；sleep 可中断；但流读取不可中断 | 部分符合 |
| `createUserMessage` + `source.kind='user'` | 已使用 | 符合 |
| `agents.get` → `resume` → `followup` | 有，但只在 `session_id` 存在时；bind 从不写 | **不符合（链路断在 bind）** |
| `followup` 投递即返回，不等待 | 是 | 符合 |
| `session/event` 旁路 `turn/end` | 未实现 | **不符合** |
| 消息 id 去重 + TTL | 拉流层有 delivery id 去重（`seen` Set） | 基本符合；缺跨重连/重启语义说明 |
| 串行队列，避免并发 resume/followup | 无；`deliverBatches` 串行 await，但多封/多 loop 仍可能并发 | 部分符合 |
| 多行/跨 chunk/心跳/重连 | `SseParser` + `streamRound` 已覆盖 | 符合 |

结论：**传输层（SSE 解析/退避/ack）已经做得不错，真正的缺口在「生命周期托管」和「绑定↔会话关联 + 唤醒」两段。**

---

## 5. 目标架构

```
                 ┌──────────────────────────────┐
                 │ aimail-gateway               │
                 │ GET /admin/pending/stream    │  SSE（单槽位/每 key）
                 └──────────────┬───────────────┘
                                │  signed GET（X-Api-Identity/Signature）
                 ┌──────────────▼───────────────┐
                 │ dsh: mail-session-bridge      │
                 │  ctx.effect → runSseLoop()    │
                 │   ├─ SseParser（复用 core）    │
                 │   ├─ 去重（delivery id + TTL） │
                 │   ├─ 串行队列                  │
                 │   ├─ processInboundMail()     │
                 │   └─ wakeSession()            │
                 └──────┬───────────────┬───────┘
                        │               │
          agents.get(id)│               │agents.resume({resumeSessionId})
                        ▼               ▼
                 ┌──────────────────────────────┐
                 │ 用户正在看的那个 dsh session   │
                 │ agent.followup(message)       │  ← 只入队，立即返回
                 │ （turn 结束后再处理下一封）     │
                 └──────────────────────────────┘
```

关键约束：

1. **连接层**只负责「收 + 解析 + 去重 + 串行 + ack」，不 await 任何 session 的回合。
2. **唤醒层**只负责「找到 session → followup」，找不到就 resume，再找不到才考虑新建（且要显式记录）。
3. **绑定层**负责「地址 ↔ 当前 session」的持久化；bind 成功后立即可用，不重启。
4. 任何异步都不允许逃逸出 `ctx.effect` 的 catch；卸载时 200ms 内断开。

---

## 6. 修订设计

### 6.1 新增 dsh 入口：`agent-aimail/session-bridge`

- 新文件 `src/dsh/session-bridge.ts`；
- `package.json` 增加 `./session-bridge` export；
- `cordis.patch.yml` 在 `insert:` 下增加一行：

```yaml
- id: mail-session-bridge
  name: 'agent-aimail/session-bridge'
  config:
    # 可选：mode: auto|stream|poll, cwd, dedupeTtlMs, maxRetryDelayMs
```

- `inbound` 保留 HTTP push（system/bridge 绑定用），不再承担 agent-scope 的拉流；把 `startInboundPull` 从 `inbound.ts` 移走（core 函数保留给 pi/openclaw）。

> 为什么要拆：push 与 pull 的失败语义不同（503 重试 vs 不 ack 重拉），生命周期也不同；拆开后 `inbound` 只在需要时监听端口，`session-bridge` 只做 SSE，排障面小。

### 6.2 `apply()`：同步 + `ctx.effect` + 全部 catch

```ts
export const name = 'mail-session-bridge'
export const inject = ['mail', 'agents']

export function apply(ctx: Context, config: BridgeConfig = {}) {
  const ctrl = new AbortController()
  const bridge = new SessionBridge(ctx, config, ctrl.signal)

  ctx.effect(() => {
    // 只启动，不 await；run() 内部自带 try/catch，绝不让 rejection 逃逸
    void bridge.run().catch((e) => ctx.logger?.error('[aimail-sse] run failed', e))
    return () => {
      ctrl.abort()            // 先中断 fetch/read/sleep
      bridge.dispose()        // 再停 handle / 清 resume 缓存
    }
  }, 'aimail.sessionBridge()')

  // 地址生命周期变化（bind/restore/pause/resume/unbind）→ 热挂载/热摘除
  ctx.effect(() => onBindingChanged(() => bridge.reconcileSoon()), 'aimail.bindingReconcile()')
}
```

要点：
- `run()` 里第一步是 `await bridge.ensureBindingReady()`（有 10s 上限），失败也只 log，不抛；
- `reconcile()`：枚举当前 agent-scope bindings，**新增的** `startAgentPullEntry()`，**删除/暂停的** `handle.stop()`；`reconcileSoon()` 去抖 250ms，`signal.aborted` 后不再启动新 loop；
- 所有 `ctx.on(...)` 都挂在 `apply` 的 fiber 上，随 fiber 自动解绑。

### 6.3 SSE 循环：复用 core，修可中断性

不重复实现一套解析器，复用 `core/gateway.ts` 的 `GatewayClient.streamRound()` / `SseParser` / `startPolling()`，但补三处：

1. `streamRound(onBatches, signal, pingTimeoutMs)`：
   - `fetch(url, { …, signal })`；
   - `signal.addEventListener('abort', () => { void reader.cancel().catch(() => {}) }, { once: true })`；
   - catch 里先判 `signal?.aborted`，返回 `'stop'` 而不是 `'drop'`/`'error'`；
   - finally 里移除监听并 `reader.releaseLock()`。
2. `GatewayClient.request()` 增加可选 `signal`，`pullList()` 透传；长轮询 hold 也能被 abort 打断。
3. `startPolling()`/`startAgentPullEntry()`/`startAgentPullEntries()` 增加 `signal` 选项，并把它传给 `startPolling`（`sleep` 已经支持）。

行为保持：
- `auto` 模式、`409 occupied` 不退级、3 次连续错误降级 poll、指数退避 1→30s；
- 收到 `mail` 批次仍逐条 `await onEmail`，**成功才 ack**；
- 失败不 ack，下一轮重拉（不丢信）。

### 6.4 唤醒层：`get → resume → followup`

```ts
private async wake(sessionId: string | undefined, msg: UserMessage): Promise<'live'|'resumed'|'created'|'no-target'> {
  if (!sessionId) return 'no-target'
  let agent = this.ctx.agents.get(sessionId)
  if (agent) { agent.followup(msg); return 'live' }

  try {
    const handle = await this.ctx.agents.resume({ resumeSessionId: sessionId, agentOptions: this.agentOptions() })
    this.resumed.set(sessionId, handle)      // 缓存，不按消息 dispose
    handle.agent.followup(msg)
    return 'resumed'
  } catch (e) {
    this.ctx.logger?.warn(`[aimail-sse] resume ${sessionId} failed: ${String(e)}`)
    return 'no-target'
  }
}
```

- `followup` **立即返回**；不在投递路径里 `whenIdle`。
- `session/event` 旁路：

```ts
ctx.on('session/event', (session, event) => {
  if (event.type !== 'turn/end') return
  if (!this.watched.has(session.id)) return
  ctx.logger?.info(`[aimail-sse] turn end for ${session.id}: ${event.data.reason.kind}`)
})
```

- `watched` 只用于日志/指标，不参与投递判定。
- 若 `sessionId` 存在但 resume 失败：**默认**新建一次性 session（保持兼容），并把 binding 的 `session_id` 重绑到新 session（避免下一封又新建）；同时 log 一条 `stale session rebound`。这一点见 §8-D3。

### 6.5 绑定层：bind 必须写 `session_id`

这是本次最关键的语义修正。

`aimail_address(action='bind', …)` 增加 `sessionId`（由 dsh 适配层注入，不进 LLM schema），三种模式都要落盘：

| 模式 | 现在 | 修订后 |
|---|---|---|
| 已绑定且带 key | `already_bound`，什么都不写 | `already_bound` + **本会话**写 `session_id` / `preset`（0 次网关调用，不消费 code、不轮换 key） |
| 归档恢复 | 恢复文件 + pointer | 同上，恢复后写 `session_id` |
| 新地址 | 激活 + 写 binding | 激活时就把 `session_id` 放进 cfg，一次落盘 + `fireBindingChanged` |

配套：
- `activateAddressCodePersist()` 增加 `sessionId` 选项，写进 cfg 后再 `fireBindingCreated`；
- `aimailAddress()` 在 `already_bound` / `restored` 分支调用 `updateAgentConfig(sid, email, { session_id, preset })`；
- **清理重复**：同一个 `session_id` 若已挂在别的 binding 上，先把它从旧 binding 清掉（本地扫描），避免 `resolveBySessionId` 命中歧义；
- 所有 `pause/resume/unbind` 也 `fireBindingChanged(cfg)`，让 bridge 热摘除/热挂载；
- `setProfileDirResolver` 在 dsh 入口接上（`<DSH_HOME>/profiles/<DSH_PROFILE>`），让 pointer 落到正确 profile。

### 6.6 工具解析路径：本地、快速、不联网

`dsh/tools.ts`：

- `aimail_address` **跳过** `resolve(exec)`，直接以 `{ sessionId: exec.agent?.id ?? '' }` 调 handler；
- 其它工具仍 `resolveCtx`，但 `mail-service.resolveCtx` 改为**纯本地**：
  1. `resolveBySessionId(sessionId)`；
  2. 未命中 → 若本 profile 作用域内（`AIMAIL_SYSTEM_ID` 指定，或全机只有一个 system）恰好有**唯一一个** `session_id` 为空的 agent-scope binding，**收养**它（本地写 `session_id`）后返回；
  3. 仍未命中 → 抛**快速**的类型化错误：
     ```json
     { "success": false, "reason": "unbound", "hint": "call aimail_address(action='bind', email, code, gateway_url) in this conversation" }
     ```
- 删除/默认关闭 `tryAutoBindSession()`（保留 `AIMAIL_AUTO_BIND=1` 作为显式逃生门），避免任何工具调用被网络拖住。
- 启动时的 `ensureDefaultAgentBinding()` 保留（无绑定时兜底），但只放在 bridge 的 `ctx.effect` 里、有超时、失败只 log。

### 6.7 HTTP push 监听器

- 仍装在 `inbound`，但：
  - 由 `ctx.effect` 托管；
  - 启动顺序改为「先 listen，再 best-effort bind」或「bind 有 10s 上限，超时也 listen 并告警」二选一（见 §8-D5）；
  - `dispose` 时 `server.close()` + `server.closeAllConnections?.()`；
  - 推送路径的 503/200 语义保持不变。
- 不再从 `inbound` 启动 agent-scope 拉流。

### 6.8 去重、顺序、ack

- 去重键：`delivery.id`（网关稳定唯一）；TTL 默认 10 分钟、上限 1000 条；
- 串行：一个 Promise 链，`onEmail` 内部 `await` 入队任务，保证 **ack 发生在投递之后**；
- 多地址：每个地址一个 loop，但共享一个串行队列，避免并发 `resume` 同一 session；
- 重启后：已 ack 的不会再来；未 ack 的重拉会命中 TTL 去重（如果重启前见过），否则最多重复一封信，可接受。

### 6.9 可观测性

统一前缀 `[aimail-sse]`，每条关键路径一行：
- `connected endpoint=… mode=stream`
- `event delivery=123 session=… target=live|resumed|created`
- `ack ids=[…]`
- `reconnect in 2s (streak=2)`
- `409 occupied — another client holds this mailbox; retry in 4s`
- `turn end session=… reason=completed`

`ctx.logger` 优先，拿不到时退回 `console`。

---

## 7. 文件级改动清单

| 文件 | 改动 | 风险 |
|---|---|---|
| `src/dsh/session-bridge.ts` | **新增**：apply/effect、SSE 循环、去重队列、wake、reconcile、turn/end 观察 | 中 |
| `src/dsh/index.ts` | 导出新入口；更新注释 | 低 |
| `src/dsh/inbound.ts` | 移除 detached 启动 + `startInboundPull`；push listener 改 `ctx.effect`；deliver 的 fresh-session 分支收敛 | 中 |
| `src/dsh/tools.ts` | `aimail_address` 跳过 resolve；所有工具注入 `sessionId` | 低 |
| `src/dsh/mail-service.ts` | `resolveCtx` 改本地 + 收养；移除默认 auto-bind；暴露 `linkSession` | 中 |
| `src/core/gateway.ts` | `streamRound`/`request` 接 AbortSignal；abort 时 cancel reader | 中 |
| `src/core/address-code.ts` | `aimailAddress` 增加 `sessionId`；三模式写 binding；`activateAddressCodePersist` 增加 sessionId；`startPolling` 接 signal | 中 |
| `src/core/poll-entry.ts` | `startAgentPullEntr*` 增加 signal 选项 | 低 |
| `src/core/binding-hooks.ts` | 新增 `onBindingChanged` / `fireBindingChanged`；`fireBindingCreated` 兼容 | 低 |
| `src/core/tools.ts` / `mail/index.ts` | `MailToolCtx` 增加 `sessionId`；`aimailAddressTool` 透传 | 低 |
| `package.json` | 新 export、版本 0.2.13 | 低 |
| `cordis.patch.yml` | 新增 `mail-session-bridge` 行 | 低 |
| `README.md` / `docs/agent-adapter-playbook*.md` | 更新 dsh 架构图与「bind 即会话绑定」说明 | 低 |
| `tssdk/test/startup-hook.test.ts` / `tssdk/test/pull-startup-hook.test.ts` | 源码级 pin 断言需要跟着入口迁移（`inbound.ts` → `session-bridge.ts`） | 低 |
| 测试 | 见 §9 | — |

---

## 8. 关键设计决策（请评审拍板）

**D1. bind 的语义：地址绑定到「当前会话」还是「整机/profile」？**
建议：**当前会话**。已存在的、`session_id` 为空的全局绑定，由第一个使用邮件工具的会话「收养」（本地写，不联网）。这样既支持「在对话框里 bind」，也兼容 CLI 预先绑定的老用户。
代价：同一地址在多会话间是「最后 bind 者得」；需要文档写明。

**D2. 没有 `session_id` 的地址收到邮件怎么办？**
建议：保留「新建一次性 session」作为兜底（system/bridge 绑定、CLI 绑定但还没人收养的情况），但：
- `cwd` 改为可配置（`AIMAIL_INBOUND_CWD`，默认 profile 的 workspace 而不是 `process.cwd()`）；
- 不再在 turn 结束后立刻清空 `session_id`（让这个新会话可以继续收同一地址的后续邮件，直到用户显式 unbind/rebind）。
如果你希望「绝不新建」，也可以改成「不 ack + 告警」，但邮件会停在网关。

**D3. session 失效（resume 失败）怎么办？**
建议：新建一次性 session 并把 binding 重绑过去，log 一条 `stale session rebound`；不静默丢信。
备选：只在连续 N 次失败后重绑，避免 session 暂时不可恢复时误判。

**D4. 传输默认模式？**
建议：保持 `auto`（先探测 SSE，失败降级长轮询，10 分钟重探）；`AIMAIL_PULL_MODE=stream|poll` 仍可强制。

**D5. HTTP 监听与就绪信号**
建议：`listen` 不再无上限等待 `ensureDefaultAgentBinding`；改为「bind 最多等 10s，超时也 listen 并打一条 `unbound` 告警」。这样端口永远可用，且不会因为网络问题让 UI 看着像卡住。
备选：维持「port up ⇒ binding 已写」的旧不变量，但给 bind 加超时并在超时后仍 listen（行为等价，只是告警口径不同）。

**D6. ack 时机**
建议：`followup` 入队即 ack（文档的「投递即返回」）；不等待 `turn/end`，否则长回合会把 SSE 读循环拖死。

**D7. 是否保留 `startAgentLevel` / `onBindingCreated` 的旧热挂载路径？**
建议：core 保留给 pi/openclaw；dsh 用新的 `session-bridge` + `onBindingChanged`，避免两套热挂载同时跑。

---

## 9. 测试计划

### 9.0 实施前先复现基线（建议）

在改代码之前，先用当前 0.2.12 做两个 5 分钟实验，把「挂起」落到具体现象上：

1. **bind 不可达**：新开一个**未绑定**会话（不要用已收养的），让它调 `aimail_address(action="bind", …)`；预期看到工具在 `resolveCtx` 处返回 `no aimail binding for session …`，binding 文件没有变化。若如此，根因 A 成立。
2. **SSE 断不干净**：先 `nslookup aimail.token.tm` 拿到网关 IP，再用 `lsof -nP -p <dsh-pid> -iTCP | grep <ip>` 找到那条 ESTABLISHED 连接；修改 `~/.dsh/profiles/desktop/cordis.patch.yml`（加一行注释即可）触发热重载；观察旧连接是否在 200ms 内消失、日志是否出现 `409`/`occupied` 退避。若旧连接持续 ~30s，根因 D 成立。
3. **投递到新会话**：给已绑定地址发一封信；看 `~/.dsh/sessions/` 下是否新增目录、新 session 的 `cwd` 是否为 profile 目录。若如此，根因 B/C 成立。

> 这三步不改代码、不改 binding，只读+发信；确认后再按 P0→P4 实施。

### 9.1 单元/集成（vitest，`tssdk/packages/agent-aimail/test/dsh/`）

1. **apply 非阻塞**
   - fake gateway `fetch` 永不 resolve；
   - 断言 `apply()` 在 50ms 内返回；`ctx.effect` 的 setup 同步执行；dispose 后 200ms 内 fetch/read 被 abort。
2. **SSE 帧解析/跨 chunk/心跳**
   - 复用 `SseParser` 现有向量；补一个「一条事件被拆成 3 个 chunk」的用例。
3. **唤醒**
   - fake `agents.get` 返回 live agent → 断言 `followup` 一次、消息 `source.kind==='user'`、文本含 `message_id`；
   - `get` 为空、`resume` 成功 → 断言 resume 一次 + followup；
   - `resume` 抛错 → 断言走 fallback 且只建一个 session。
4. **去重/顺序**
   - 同一 delivery id 连发两次 → followup 一次；
   - 连发 5 封 → followup 顺序与到达一致，且 `resume` 不并发。
5. **bind 写 session**
   - `already_bound` + `sessionId` → 断言 binding 文件出现 `session_id`，零网关调用；
   - 新地址激活 → 断言 `session_id` 在 `fireBindingChanged` 之前已落盘；
   - 同一 session 之前挂在别的 binding → 旧 binding 的 `session_id` 被清掉。
6. **工具路径**
   - 未绑定会话调 `send_mail` → 快速返回 `unbound`（<200ms，且没有 fetch 调用）；
   - 未绑定会话调 `aimail_address` → 能到达 handler（不被 resolve 拦住）。
7. **abort 语义**
   - `streamRound` 在 abort 后返回 `'stop'`，不当作 `'drop'`（否则会误降级 poll）。
8. **409**
   - 模拟 409 → 不退级、按退避重试同一端点。

### 9.2 手工验收（本机 dsh desktop）

1. 安装 0.2.13，重启 app；确认 9099 监听、日志出现 `[aimail-sse] connected`。
2. 在**当前对话框**粘贴绑定信息（已绑定地址只给 email；新地址再加 code + gateway_url），让 agent 调 `aimail_address bind`；
   - 期望：≤5s 返回 `success:true`，binding 出现 `session_id`，**不新建会话**。
3. 用外部邮箱给该地址发信；
   - 期望：≤10s 内**当前对话框**收到 followup（同一 session id），turn 正常完成并回信。
4. 同一对话框再 bind 一次（`already_bound`）；
   - 期望：立即返回，不消费 code、不轮换 key、`session_id` 不变。
5. 编辑 `cordis.patch.yml` 触发 HMR / 重启 app；
   - 期望：旧连接 200ms 内断开；新实例不出现 409；端口不 EADDRINUSE。
6. 断网 30s 再恢复；
   - 期望：指数退避重连，恢复后补收未 ack 的邮件，不重复投递。
7. 发一封 5MB 带附件的邮件；
   - 期望：不阻塞其它邮件（串行但可观测），附件按现有预处理链处理。

### 9.3 回归

- `tssdk` 全量 `pnpm test`；
- 源码级 pin：`startup-hook.test.ts`（listen 后才 notify）和 `pull-startup-hook.test.ts`（`void startInboundPull`）跟着 dsh 入口迁移更新，**不能删掉这两个断言**（它们是「端口/拉流就绪后才对外声明」的护栏）；
- L3 `dsh-plugin` cell（`aimail install --home ~/.dsh` / `dsh plugin add`）保持通过；
- openclaw/pi 的 pull 行为不变（core 只加可选 signal）。

---

## 10. 验收标准

- [ ] `apply()` 不 await 任何网络/SSE；dev 下人为让网关 hang，DSH 仍能正常启动、对话可输入。
- [ ] 在任何未绑定会话里，`aimail_address bind` 能到达 handler；`send_mail` 快速返回 `unbound` 而不是长时间等待。
- [ ] bind 成功后 binding 带 `session_id`，且**不重启**就能收信。
- [ ] 入站邮件投递到 `session_id` 对应的**同一个对话框**；不再为每封信新建 session（除非 D2/D3 兜底）。
- [ ] `followup` 不阻塞 SSE 读循环；长回合期间后续邮件排队。
- [ ] HMR/重启后旧 SSE 连接 ≤200ms 断开；新实例无 409 长时间退避。
- [ ] 断网重连后不重复、不丢信（delivery id 去重 + 不 ack 重拉）。
- [ ] `session/event` 的 `turn/end` 有日志，能定位「收到但没回」的问题。
- [ ] 全量 TS 测试 + L3 dsh cell 通过。

---

## 11. 兼容 / 迁移 / 风险

**兼容**
- 不改网关协议、不改 `/aimail/inbound` 路径、不改 9099 默认端口、不改 address-code 激活 API。 <!-- contract-allowed: design/acceptance doc must quote the contract surface verbatim -->
- openclaw/pi 仍用现有 `startAgentLevel`/`startInboundPull`；core 的新增参数全部可选。
- 老 binding（无 `session_id`）通过 §6.6 的「收养」逻辑继续可用。

**迁移**
- 升级到 0.2.13 后第一次在会话里用邮件工具时，自动收养唯一全局 binding；
- 若同一机器有多个 agent-scope binding，则不自动收养，要求显式 `aimail_address bind`（避免投错）。
- 版本号 minor；README 写明「bind = 绑定到当前会话」。

**风险**
- **单槽位 SSE**：同一地址被两个 dsh 实例/两个进程同时拉流 → 409。缓解：明确日志 + 退避 + 可切 `AIMAIL_PULL_MODE=poll`；不试图绕过网关锁。
- **resume 失败**：桌面端有 jsonl 持久化，一般没问题；万一失败走 D3 兜底。
- **长回合**：`followup` 在 turn 中会排队到下一轮；这是 DSH 的正确语义，但用户可能觉得「回得慢」。文档写明。
- **安全**：push 仍走 HMAC；pull 依赖网关对 agent key 的校验，不新增信任边界。
- **HMR 双实例**：如果 DSH 的 HMR 在新实例起来前没完全 dispose 旧 fiber，仍可能短暂 409；用 abort 修复把窗口压到 <200ms。

---

## 12. 实施顺序与估算

| 阶段 | 内容 | 产出 | 估算 |
|---|---|---|---|
| P0 | `ctx.effect` + AbortSignal（gateway/stream/poll）+ 现有行为不变 | 可中断、可重载 | 0.5d |
| P1 | bind 写 `session_id` + 工具跳过 resolve + 本地 resolve/收养 | 对话框内 bind 可用 | 1d |
| P2 | 新增 `session-bridge`：SSE + 去重 + 串行 + wake + turn/end | 邮件唤醒当前会话 | 1.5d |
| P3 | 热挂载/reconcile（bind/pause/resume/unbind）+ 清理旧路径 | 不重启生效 | 0.5d |
| P4 | 测试、文档、版本、L3 | 发布 0.2.13 | 1d |

建议按 P0 → P1 → P2 → P3 → P4 串行；每个阶段独立可回滚（P0/P1 不改变收信主路径）。

---

## 13. 明确不做

- 不引入 MCP server / 不改 15 个工具的名字与语义；
- 不改 Python SDK、不改 bridge、不改网关；
- 不改 `cordis.patch.yml` 里 `system-prompt` persona 行；
- 不把 SSE 放进 tool call 或 agent 回合里等待；
- 不做「多会话同时持有一个邮箱」的负载均衡（网关就是单槽位）。

---

## 14. 请评审确认

1. D1「bind = 当前会话」是否接受（含「最后一个 bind 者得」）？
2. D2/D3 的兜底策略（新建一次性 session + 重绑）是否接受？还是宁可「不 ack + 告警」？
3. D5 的监听/就绪语义是否要维持旧的「port up ⇒ binding 已写」不变量？
4. 是否同意新增 `agent-aimail/session-bridge` 子入口（第 4 个 cordis 行），还是把逻辑合回 `inbound`？
5. 版本按 0.2.13 发布、还是先出 `0.2.13-rc.1` 在本机验证？
