> **已被合并/取代（2026-11-23）**：本文件逐条复核后并入 `docs/qa/PRODUCT-REVISION-REQUEST.md`（范围收敛为 tssdk + gateway，不含 pysdk）。
> 复核结论：3.1 / 3.2 已在 0.5.1 修复（勿再改）；3.6 作为缺陷撤回（真因为 QA 夹具）；其余条目已并入并按优先级重排。
> 本文件仅作历史留痕，**不要**据此执行。

# tssdk V2 修订说明（待执行）

状态：**独立修订说明，未执行**  
适用：`tssdk/packages/agent-aimail`  
前置：当前 V2 QA 结论为 HOLD，见 `tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md`  
执行时机：QA 冻结/允许改动 tssdk 后，单独分支执行；不要与当前 V2 测试进程并行修改

---

## 1. 目标

修订当前 tssdk，使 V2 的入站闭环满足：

```text
aggregate SSE
  -> binding
  -> preflight
  -> processInboundMail
  -> host session
  -> accepted / intercepted / failed
  -> ack 仅 accepted/intercepted
```

必须保持：

- SSE 单位是 `(AgentSystemId, Gateway)`；
- 预处理在 host inbound adapter 内、session 之前；
- 不在 SSE 核心层做预处理；
- 失败/未进入 session 不 ack；
- 不允许 V1 prefix 判断 V2 binding；
- 不允许 pull client 缺少 aggregate path/header。

---

## 2. 当前实现位置

### 2.1 SSE / pull 核心

| 文件 | 作用 |
|---|---|
| `src/core/system-pull.ts` | aggregate SSE，收 delivery，调 `onEmail` |
| `src/core/address-code.ts` | polling / SSE batch / ack 基础实现 |
| `src/core/poll-entry.ts` | agent-scope polling entry（legacy/adapter 路径） |
| `src/core/agent-system-id.ts` | AgentSystemId 派生 |

`system-pull.ts` 本身**不预处理**，只把 raw delivery 交给 `onEmail`。

### 2.2 预处理位置

| 宿主 | 文件 | 顺序 |
|---|---|---|
| DSH `mail-hub` | `src/dsh/mail-hub.ts` | `processInboundMail` → workspace → `routeThread` → session |
| DSH `inbound` | `src/dsh/inbound.ts` | `processInboundMail` → session |
| OpenClaw | `src/openclaw/inbound.ts` | `processInboundMail` → `deliverToAgent` |
| Pi / agent-level | `src/core/agent-level.ts` | `processInboundMail` → `onAgent` |

结论：

```text
预处理已经在 host adapter 内、session 之前
```

但顺序和错误语义需要修订。

---

## 3. 当前缺陷

### 3.1 P0 — `isAgentScopeBinding` 仍用 V1 前缀

文件：

```text
src/core/poll-entry.ts
```

当前：

```ts
export const AGENT_SCOPE_SYSTEM_PREFIX = 'shared_addr_'

export function isAgentScopeBinding(cfg) {
  if (!cfg?.system_id || !cfg.api_key) return false
  return cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX)
}
```

V2 binding：

```text
system_id       = GatewaySystemId
agent_system_id = asid_...
```

`system_id` 不再是 `shared_addr_*`，谓词恒 false。

影响：

- polling 不启动；
- `startAgentPullEntries` 过滤掉 V2 binding；
- OpenClaw/Pi/DSH 的 pull 路径可能静默不工作。

修订：

```ts
export function isAgentScopeBinding(cfg) {
  return Boolean(cfg?.agent_system_id && cfg.api_key)
}
```

兼容：

- 如必须支持 V1 旧 binding，可仅在 `agent_system_id` 缺失时回退旧前缀，并打 WARN；
- V2 主路径不得依赖 `shared_addr_`。

### 3.2 P0 — `poll-entry` 构造的 client 缺 aggregate path/header

文件：

```text
src/core/poll-entry.ts
```

当前：

```ts
new GatewayClient(cfg.gateway_url, cfg.api_key, PULL_HTTP_TIMEOUT_MS, cfg.email)
```

问题：

- 缺 aggregate stream/pending/ack path；
- 缺 `X-Agent-System-Id`；
- 与 `system-pull.ts` 的 client 构造不一致。

影响：

- polling 能启动但请求打到非 aggregate 路径；
- 邮件被读但不进入正确 pending/ack 链。

修订：

- 提取共享构造函数：

```ts
buildAgentSystemClient(cfg, agentSystemId)
```

- 统一使用：

```ts
new GatewayClient(url, key, timeout, identity, {
  streamPath: '/api/v1/agent-system/pending/stream',
  pendingPath: '/api/v1/agent-system/pending',
  ackPath: '/api/v1/agent-system/pending/ack',
  extraHeaders: { 'X-Agent-System-Id': agentSystemId },
})
```

- `poll-entry`、`system-pull`、host pull 入口不得各自复制路径字符串。

### 3.3 P0/P1 — OpenClaw 读到邮件但未进 session

QA 观察：

```text
STREAM 200
GET /contacts ...（邮件已读）
无 turn
无 ack
pending 仍存在
```

当前代码：

```text
openclaw/inbound.ts
  deliverInbound()
    processInboundMail()
    resolveDispatchAgentId()
    threadSessionKey()
    deliverToAgent()
    logAimailDispatch()
```

需要核查：

1. `resolveInboundTarget()` 是否拿到正确 binding；
2. `resolveDispatchAgentId()` 是否仍受 V1 prefix 影响；
3. `threadSessionKey()` 是否被 host policy 拒绝；
4. `deliverToAgent()` 的 hooks 是否返回 401/400/404；
5. `logAimailDispatch()` 是否实际执行；
6. `out.ok` 是否只在 `delivered` 时为 true。

不 ack 是符合契约的；但如果 `deliverInbound` 已经返回 `delivered` 而 pull 未 ack，则是另一条链问题。

修订：

- 在 `deliverInbound` 的每个失败分支记录：
  - status；
  - detail；
  - agentId；
  - sessionKey；
  - binding address。
- `logAimailDispatch` 必须覆盖 pull 路径；
- 增加一个“dispatch result”单测，确保 `ok=false` 不会被 ack；
- 修 `resolveDispatchAgentId` 的 V1 prefix：

```ts
const addressScope = Boolean(cfg.agent_system_id)
```

### 3.4 P1 — DSH `mail-hub` 预处理早于 workspace preflight

当前：

```text
processInboundMail()
  -> workspaceFor()
```

如果 workspace 不存在：

- 附件可能已下载；
- meta/snapshot/FTS 可能已写；
- 然后返回 false，不 ack。

修订：

```text
binding
  -> workspaceFor()
  -> preflight session/workspace
  -> processInboundMail()
  -> session
```

只有在目标确定存在后才做重型预处理和副作用。

### 3.5 P1 — ack 语义没有类型化

当前多个 host 用 `boolean` 表示投递结果。

问题：

- `intercepted` 与 `delivered` 都可以 true；
- `failed` 与 `no binding` 都可能 false；
- pull 侧只看 boolean，容易把“已消费”与“已投递”混为一谈。

修订：

```ts
type InboundResult =
  | { status: 'accepted'; detail?: string }
  | { status: 'intercepted'; detail?: string }
  | { status: 'deferred'; detail?: string }
  | { status: 'rejected'; detail?: string }
```

ack 规则：

```text
accepted    -> ack
intercepted -> ack（已消费，不需要 agent）
deferred    -> ack（已进入 FIFO/队列）
rejected    -> 不 ack
```

### 3.6 P1 — DSH 会话工具面（B4）

这不是 tssdk 预处理能解决的，但会影响验收。

需要同时确认：

- HTTP session create；
- mux session follow(activate)；
- agentPreset / session 工具开关；
- 15 件 mail tools 是否实际注入。

tssdk 侧能做：

- 在 host adapter 记录“目标 session 是否实际有 mail toolset”；
- 启动时打印 toolset 可用性；
- 不把“session 创建成功”误判为“工具可用”。

### 3.7 P2 — `startAgentLevel({ onEmail })` escape hatch

`core/agent-level.ts` 的 `onEmail` 会绕过默认预处理。

修订：

- 文档标注 raw escape hatch；
- 生产路径不允许传 `onEmail`；
- 或改名为 `onRawDelivery`，并强制显式 opt-in。

### 3.8 P2 — OpenClaw stats 为 0

aggregate handle 返回：

```ts
stats: () => ({ pulled: 0, acked: 0, errors: 0 })
```

会掩盖真实拉取/ack 统计。

修订：

- 透传 `system-pull` 的实际 stats；
- 至少记录 round/delivery/ack 数。

### 3.9 P2 — Pi/OpenClaw 多地址路由边界

Pi 是单地址，应只允许 pointer 地址；
OpenClaw 多地址必须按 recipient binding 路由，不能使用首个 binding 兜底。

修订：

- 加单地址断言；
- 多地址 route 明确报错，不静默取第一个；
- 记录 recipient/binding/agentId。

---

## 4. 目标调用链

```text
system-pull
  -> raw delivery
  -> host inbound adapter
        -> binding / recipient
        -> preflight target
             - workspace exists?
             - session target?
             - host agent available?
        -> processInboundMail(cfg, headers)
        -> deliver to session
        -> InboundResult
  -> accepted/intercepted/deferred = ack
  -> rejected/throw = no ack
```

### 4.1 预处理规则

- 只在 host adapter；
- 只在 preflight 通过后；
- 使用当前 binding 的 `systemId/email`；
- 不做进程全局 agent context；
- ping/pong 在预处理内拦截；
- 附件/meta 只在将要投递时写。

### 4.2 不变量

```text
没有进入目标 session/FIFO ⇒ 不 ack
没有 binding ⇒ 不预处理
没有 workspace/session target ⇒ 不预处理（DSH）
```

---

## 5. 文件修改清单

| 文件 | 修改 |
|---|---|
| `src/core/poll-entry.ts` | `isAgentScopeBinding` 用 `agent_system_id`；共享 aggregate client；stats |
| `src/openclaw/inbound.ts` | `resolveDispatchAgentId` 用 `agent_system_id`；失败日志；typed result |
| `src/dsh/mail-hub.ts` | workspace preflight 提前；typed result；stats |
| `src/dsh/inbound.ts` | preflight；typed result；ack |
| `src/pi/index.ts` | 单地址断言；aggregate stats |
| `src/core/agent-level.ts` | raw escape hatch 标记 |
| `src/core/system-pull.ts` | 不改传输语义；只透传统计/回调结果 |
| `src/core/preprocess.ts` | 原则上不改；只保证调用顺序 |

---

## 6. 测试计划

### 6.1 单元

- V2 binding：`isAgentScopeBinding` true；
- V1 binding：显式 legacy 分支；
- aggregate client：path + header；
- `resolveDispatchAgentId`：V2 不走 `shared_addr_`；
- DSH：无 workspace 时不调用 `processInboundMail`；
- typed result / ack matrix；
- OpenClaw：`ok=false` 不 ack。

### 6.2 L2

- DSH 单地址/双地址；
- Pi 单地址；
- OpenClaw 单地址/双地址；
- OpenClaw pull → hooks → turn → ack；
- DSH pull → session → tool surface → ack。

### 6.3 真实宿主

- 修 B1/B2 后出现 `polling enabled`；
- 收件后 30–60s 内请求；
- turn 产生；
- pending 行 ack 后消失；
- 未进 session 时 pending 保留。

### 6.4 回归

- L0；
- V2 release gate；
- advanced E2E 不受影响。

---

## 7. 执行顺序

1. 独立分支；
2. 先修 B1/B2 + 单元；
3. DSH preflight/ack；
4. OpenClaw 失败日志和 dispatch；
5. Pi 单地址断言；
6. L2；
7. 真实宿主 QA；
8. 合并。

---

## 8. 回滚

- 保留旧 `shared_addr_` 判定作为 legacy 分支；
- feature flag：`AIMAIL_LEGACY_AGENT_SCOPE_PREFIX=1`；
- 旧 push/webhook 路径不回退到 V2 地址；
- 回滚只切 tssdk 版本，不改 gateway。

---

## 9. 验收标准

```text
[ ] V2 binding 被识别为 agent-scope
[ ] aggregate client path/header 正确
[ ] DSH 无 workspace 不预处理、不 ack
[ ] OpenClaw pull 进入 session 且 ack
[ ] Pi 单地址不被多地址污染
[ ] 失败/拒绝不 ack
[ ] 重启后仍可拉取
[ ] 真实宿主 B1/B2 复验通过
[ ] 不影响 advanced/L0
```

## 10. 仍待确认

1. OpenClaw B3 的根因（hooks policy / dispatch target / sessionKey / log）；
2. DSH B4 的工具面注入口径；
3. `AIMAIL_PULL_MODE` 的 sse/poll 切换是否仍需要；
4. 旧 `shared_addr_` binding 的兼容窗口；
5. 真实 OpenClaw/DSH 版本固定。

---

本文档只定义修订，不执行、不修改当前 V2 测试。
