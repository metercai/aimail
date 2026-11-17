# tssdk SSE 架构统一改造方案（评审稿）

- 状态：**待评审，不执行**
- 参照规范：[`docs/aimail-host-sse-contract.md`](aimail-host-sse-contract.md)
- 参照实现：DSH SSE adapter（`tssdk/packages/agent-aimail/src/dsh/`）
- 目标：把 DSH 的“Agent 内激活 + workspace 绑定 + SSE 收信”抽取为 tssdk
  的公共运行时，再让 Pi / OpenClaw 以薄适配层接入；新运行时**不依赖 CLI**。
- 兼容：旧 CLI/push 路径保留为 legacy，不在新运行时中混用。

---

## 1. 现状评估

| 维度 | DSH（新） | Pi（现状） | OpenClaw（现状） |
|---|---|---|---|
| 传输 | SSE pending stream | 本地 listener + pull | HTTP inbound + pull |
| 激活 | `aimail_system` / `aimail_address` | CLI/pointer/auto-bind | CLI/commands/auto-bind |
| 工作区 | 稳定 workspace，session 可丢弃 | 未抽象为公共层 | 每 agent 有 workspace/agentDir |
| Shell/CLI | 新路径不调用 CLI | `register-cli`、CLI fallback | `register-cli`、commands、CLI fallback |
| 工具 | 15 + `aimail_system`，分组 | 15 工具 | 15 工具 |
| 主要入口 | `dsh/mail-service`、`mail-hub` | `pi/index.ts` | `openclaw/index.ts` |

### 当前问题

1. **公共能力被 DSH 目录私有化**：`mail-hub`、workspace 解析、session 投递、
   `aimail_system`/`aimail_address` 都在 `src/dsh/`，Pi/OpenClaw 无法复用。
2. **CLI 回退仍然存在**：通用 `ensureAgentBinding` 在没有 owned system 时可调用
   `ensureSystem` → `aimail install`；新运行时必须显式禁用。
3. **入口实现重复**：三个 host 各自实现 inbound、pull、binding ensure、tool
   registration，语义漂移风险高。
4. **Pi/OpenClaw 仍是 push/inbound 模型**：与“SSE only、无 listener”契约不一致。
5. **workspace/session 契约只在 DSH 生效**：其他 host 没有统一的
   “地址→workspace、thread→session”规则。
6. **测试按 host 分散**：L2/L3 难以复用同一套 C1–C11 契约矩阵。

---

## 2. 目标架构

```text
src/
  core/                 # 协议、config、preprocess、mail 语义、provision API
  contract/             # 共享契约常量/生成物（沿用现有）
  runtime/              # NEW: host-neutral SSE/activation/routing
    types.ts            # HostAdapter / WorkspaceProvider / SessionRuntime
    activation/
      system.ts         # aimail_system 语义（HTTP only）
      address.ts        # aimail_address 语义（B1/B2/current workspace）
    stream/
      hub.ts            # one SSE per gateway + credential scope
      router.ts         # recipient → binding → workspace → session
      ack.ts            # at-least-once/ack/dedup
    binding/
      ensure.ts         # workspace/pointer/default binding, allowCli:false
      workspace.ts      # stable workspace resolution
    tools/
      groups.ts         # MAIL_TOOLS / MAIL_BOARD / MAIL_ADMIN
  hosts/
    dsh/                # thin Cordis adapter (current primitives)
    pi/                 # NEW thin Pi extension adapter
    openclaw/           # NEW thin per-agent plugin adapter
```

原则：

- **一个公共 Hub，三个 host adapter**；host 只提供 workspace/session/tool/pointer
  原语，不复制 mail 路由。
- **一个 SSE 连接对应 `(gateway_url, credential scope)`**；投递按 recipient 路由。
- **binding 只写 workspace_id/workspace_path，不写 session_id**。
- **新运行时禁止 CLI fallback**；activation 只走 HTTP + Agent 工具。
- **push/inbound 只保留在 legacy 包/旧 L2**。

---

## 3. HostAdapter 抽象

不要把 host API 硬塞进一个巨大接口；用最小能力面 + host 自己实现：

```ts
interface HostAdapter {
  readonly id: 'dsh' | 'pi' | 'openclaw'

  home(): string
  pointer: {
    read(): Promise<{ system_id?: string; email?: string } | undefined>
    write(systemId: string, email?: string): Promise<void>
  }

  workspaces: {
    default(): Promise<WorkspaceRef | undefined>
    ensure(path: string): Promise<WorkspaceRef>
    resolveForSession(sessionId: string): Promise<WorkspaceRef | undefined>
    list?(): Promise<WorkspaceRef[]>
  }

  sessions: {
    get(sessionId: string): SessionHandle | undefined
    create(opts: {
      sessionId: string
      workspace: WorkspaceRef
      meta?: Record<string, unknown>
      agentOptions?: unknown
    }): Promise<SessionHandle>
    resume?(sessionId: string, agentOptions?: unknown): Promise<SessionHandle>
    followup(session: SessionHandle, message: UserMessage): void
    whenIdle?(session: SessionHandle): Promise<void>
    dispose?(session: SessionHandle): Promise<void>
  }

  tools: {
    register(definition: MailToolDefinition): void
    list?(): string[]
  }

  agents?: {
    // OpenClaw: gateway → isolated agents
    get(agentId: string): AgentRef | undefined
    list(): AgentRef[]
  }
}
```

Host-neutral runtime 不直接 import `@deepseek-ai/dsh-*`、`@earendil-works/pi-*`
或 `openclaw/*`；这些只在对应 host adapter 内出现。

### 3.1 公共接口的约束

- `WorkspaceRef = { id, path, title? }`，`path` 为绝对 canonical path；
- `SessionHandle` 只暴露 `id`、`workspace`、`followup`、可选 `whenIdle/dispose`；
- `HostAdapter` 不拥有 SSE 连接、不实现 ack、不做 binding 解析；
- 所有 host adapter 必须支持“新 session 的 cwd = binding workspace”。

---

## 4. 公共运行时行为

### 4.1 激活

- `runtime/activation/system.ts`：从 DSH `system-core` 提取，只依赖
  `GatewayClient` + 文件系统 + HostAdapter.workspace.default；
- `runtime/activation/address.ts`：从 DSH `address.ts` 提取，使用
  `HostAdapter.workspaces.resolveForSession` 或 `ensure` 得到当前 workspace；
- 工具分组保持：
  - `MAIL_TOOLS`：7 个邮件工具
  - `MAIL_BOARD`：7 个 board/角色工具
  - `MAIL_ADMIN`：`aimail_address` + `aimail_system`（系统工具宿主专属）
- 不再通过 CLI `register-cli`、`aimail install` 完成激活。

### 4.2 Binding ensure

- 启动时只对 **无 workspace** 的 binding 写 host 默认 workspace；
- 已有 workspace 一律保留（地址激活优先）；
- 调用参数固定 `allowCli:false`；
- 没有 owned system 时返回明确错误并跳过，不 spawn shell。

### 4.3 SSE Hub

- `runtime/stream/hub.ts` 从 DSH `mail-hub` 提取；
- 按 `(gateway_url, credential scope)` 分组：
  - system/domain key 覆盖的地址：一条流；
  - 多 system 同 gateway：每 system 一条（key 作用域限制）；
  - B2 仅有 per-address key：per-address fallback，但只有**一个 router**；
- 流只负责 pending delivery，不存储第二份邮件；
- delivery 路由：recipient → binding → workspace → thread/session；
- ack 在 session 接受后执行；pause/unbound/failure 不 ack。

### 4.4 Session 路由

- 新 thread：在 binding workspace 创建 disposable session；
- 回复：thread root → local meta session；同一 workspace 内 followup；
- session 不写 binding；LocalMeta 保留 `session_id/workspace_path` 仅作线程映射；
- host adapter 负责 `create/followup/whenIdle`，runtime 不关心 host 内部 session 实现。

---

## 5. Pi 适配方案

### 5.1 目标模型

Pi 的 **Workspace（平台/Channel 与 Session 之间）** 作为稳定容器：

- 地址 binding 写 `workspace_id/path`；
- 新邮件在 binding workspace 创建/唤醒 pi session；
- 回复按 thread 回到该 workspace 的 session；
- 不再启动本地 inbound listener，不再需要 bridge/webhook。

### 5.2 需要先验证的 Pi API

评审后先做一个**只读 spike**，确认：

1. 如何列出/创建 workspace，返回稳定 id/path；
2. 如何取得当前 session 的 workspace；
3. 如何创建/resume session 并 `sendUserMessage`（wake）；
4. 如何给 session 注入 tools / 读取 tool list；
5. plugin extension 生命周期（启动/停止/重载）是否允许常驻 SSE consumer。

Spike 结论进入 `docs/host-capability-matrix.md`，不先猜 API。

### 5.3 适配层

```text
hosts/pi/
  adapter.ts      # PiWorkspaceProvider/PiSessionRuntime
  tools.ts        # 公共 tool registry → pi.registerTool
  index.ts        # extension apply: activation tools + hub start
  pointer.ts      # ~/.pi pointer read/write
```

不再保留 `pi/index.ts` 中的 inbound listener、HMAC handler、CLI register 逻辑；
旧代码移到 `legacy/pi` 或以 legacy flag 保留。

---

## 6. OpenClaw 适配方案

### 6.1 目标模型

OpenClaw Gateway 进程下多个隔离 Agent，每个有：

- workspace / agentDir；
- 独立 session 历史；
- 独立 binding（或同 system 的多个地址）。

适配层：

```text
hosts/openclaw/
  adapter.ts      # per-agent workspace/session primitives
  tools.ts        # common tool registry
  index.ts        # plugin entry: agent discovery + hub
  pointer.ts      # existing openclaw pointer/identity
```

### 6.2 路由

- Gateway 层只保留一个 SSE router；
- 邮件到达后按 recipient → agent binding → agent workspace；
- 多 agent 同 system/domain key：共享 system stream；
- 无覆盖 key 时 per-agent stream fallback，但不得每个 session 一条；
- agent 的 `agentDir`/workspace 通过 adapter 查询，不复制 openclaw 内部逻辑。

### 6.3 需要验证

- plugin entry 能否常驻后台 consumer（而不仅是 route handler）；
- 多 agent 的 key/stream scope 如何映射；
- workspace 解析函数是否与 openclaw 自身 `resolveAgentWorkspaceDir` 同源；
- 旧 commands/register-cli 是否完全由 Agent 工具替代。

---

## 7. CLI 解耦与兼容

### 7.1 新运行时

- **禁止** runtime import `core/ensure-system` 的 CLI fallback；
- **禁止** runtime import `register-cli`、`cli-op-entry`、`sdk-ops` 中的 CLI 执行；
- `ensureAgentBinding` 调用点传 `allowCli:false`；
- 安装/升级不再要求 `aimail` 二进制。

### 7.2 Legacy

- 保留 `legacy/`（或独立 `agent-aimail-legacy` 包）：
  - push/inbound listener
  - HMAC
  - CLI register
  - old L2
- 新包不导出 legacy entry；旧宿主固定旧版本。
- 版本策略建议：新架构为主版本升级（例如 `agent-aimail@1.0.0`），legacy
  分支只做安全修复；不把两套运行时混在同一个默认 entry。

### 7.3 代码边界检查

新增 L0 静态门禁：

```text
runtime/** + hosts/{dsh,pi,openclaw}/** 不得 import child_process / execFile
runtime/** 不得 import core/ensure-system 的 ensureSystem
新包 cordis/patch 不得包含 mail-inbound / register-cli
```

---

## 8. 分阶段实施

### Phase 0：评审与能力验证（不写生产逻辑）

- 确认 HostAdapter 接口；
- Pi/OpenClaw workspace/session API spike；
- 确认 OpenClaw 多 agent stream scope；
- 输出 host capability matrix / 风险清单。

### Phase 1：抽取公共 runtime（DSH 不回归）

- 从 `dsh/` 移出 activation、hub、binding、workspace 到 `runtime/`；
- DSH adapter 改为薄适配；
- 保持 DSH 现有 L2/L3 全绿。

### Phase 2：Pi

- 实现 Pi adapter + pointer + tool registry；
- Pi L1（fake host）+ L2（SSE workspace welcome）+ L3；
- 移除 Pi inbound listener。

### Phase 3：OpenClaw

- 实现 per-agent adapter、workspace/agentDir 映射；
- 多 agent 路由 + system stream；
- OpenClaw L1/L2/L3。

### Phase 4：CLI 清零与打包

- 新 runtime 无 CLI import；
- legacy 拆分/冻结；
- 工具分组、system/address 工具全 host 可见；
- 版本/发布流程切换。

### Phase 5：QA 矩阵

- 每个 host 跑 C1–C11；
- 旧 L2 只跑 legacy；
- new L2/L3 通过后签字。

---

## 9. 测试矩阵

| 层 | DSH | Pi | OpenClaw |
|---|---|---|---|
| L0 contract/无 CLI 静态 | 现有 | 新增 | 新增 |
| L1 adapter fake host | 现有 | 新增 | 新增 |
| L2 SSE 无 CLI | 现有 `run-dsh-l2-sse.sh` | 新增 `run-pi-l2-sse.sh` | 新增 `run-openclaw-l2-sse.sh` |
| L3 live LLM | 现有 12 格 | 新增矩阵 | 新增矩阵 |
| C1–C11 | 全跑 | 全跑 | 全跑 |

新增 L2 共同覆盖：

- 系统/地址激活均通过 Agent 工具（无 CLI）；
- binding workspace == 激活 session workspace；
- welcome 经 SSE 进入该 workspace；
- manager 回复回同一 workspace/session；
- restart 不覆盖 workspace；
- 共享 gateway/scope 只有一条 SSE。

---

## 10. 风险与待评审问题

1. **Pi workspace API 是否稳定**：需要 spike 确认；如果 Pi 只有 session 没有
   可枚举 workspace，需要定义“当前工作区”作为稳定路径。
2. **OpenClaw 多 agent key 作用域**：系统/domain key 是否覆盖所有 agent；
   若不能，必须保留 per-agent fallback，不能假装单连接。
3. **插件生命周期**：Pi/OpenClaw plugin 是否允许后台常驻 SSE consumer；
   只能在 route handler 内拉取的话，需要 host 生命周期扩展。
4. **旧用户迁移**：已有 binding 没有 workspace；需要一次性 repair 或
   重新 bind；旧 push/inbound 配置如何处理。
5. **版本策略**：是否接受 major 升级，legacy 包是否独立发布。
6. **工具命名/可见性**：Pi/OpenClaw 的 tool registry 是否支持
   `aimail_system` 这类 host-only 工具，还是需要 host 级 toolset。
7. **共享 SSE 的 scope 边界**：多 system 同 gateway 的无平台 key 情况；
   需要 gateway 提供 platform scope key 才能做到真正单连接。

---

## 11. 评审通过后的第一批交付

1. `docs/host-capability-matrix.md`（Pi/OpenClaw API 实测）
2. `src/runtime/types.ts` + 单测
3. DSH 迁移到 runtime，L2/L3 不回归
4. 新 L0 无 CLI 静态门禁
5. Pi spike（不合并生产代码）

评审重点请确认：

- HostAdapter 接口是否足够薄且不泄漏 host API；
- legacy 是否独立包/目录；
- 是否接受 major 版本；
- Pi/OpenClaw 的 workspace/session 语义是否按本方案映射。
