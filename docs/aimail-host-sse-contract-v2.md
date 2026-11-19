# AIMail Host SSE Activation Contract v2

状态：**V2 目标契约（设计稿）**  
取代：[`aimail-host-sse-contract.md`](aimail-host-sse-contract.md) 的 V1 表述  
适用范围：所有通过 SSE 拉取邮件、在 Agent 会话内完成激活的宿主

> V2 的核心变化不是换传输，而是把三件长期混淆的事情拆开：
> 1. Agent 系统是**单地址**还是**多地址**；
> 2. 地址绑定是 **B1（带系统）** 还是 **B2（无系统）**；
> 3. SSE 的连接单位是 **AgentSystemId**，不是地址，也不是网关 system_id。

---

## 1. 术语

| 术语 | 定义 |
|---|---|
| Gateway / gw | AIMail 网关；base 或 advanced 均可，激活流程完全一致。 |
| Agent 系统 | 一个 Agent 产品实例；例如 DSH web、OpenClaw gateway、Pi 进程。 |
| Workspace / Agent 空间 | 可承载多个 Session 的稳定容器；DSH 叫 workspace，OpenClaw 对应 agentId。 |
| Session | 一次 LLM 对话；可创建、可调用、可弃用。 |
| AgentSystemId | **Agent 系统实例的稳定客户端 ID**，不是 Gateway 的 system_id。 |
| Address binding | 邮件地址与 Agent 空间/系统的绑定。 |
| Activation code | 从 gw `/apply` 申请、通过邮件下发的一次性码。 |
| Activation prompt | 邮件中自动生成、用户复制到 Agent 对话框的完整激活指令。 |
| B1 | 带系统约束的地址绑定：需要本地系统对接。 |
| B2 | 无系统约束的地址绑定：不需要本地系统。 |

---

## 2. 两类 Agent 系统

契约首先要求宿主声明自己的能力，而不是先假设“所有 Agent 都一样”。

### 2.1 多地址模式（multi-address）

必须同时具备：

1. **稳定空间**：有名称、相对稳定的 Workspace/Agent 空间；
2. **多 Session**：空间内可创建多个 LLM Session；
3. **主动调用 Session**：外部事件可以创建 Session，或向指定 Session 投递；
4. **空间路由**：地址绑定到空间；入站邮件按 recipient 路由到空间，再按 thread 路由到 Session；
5. **Session 可弃用**：binding 不保存 session 身份。

满足后可支持：

- 一个 Agent 系统绑定多个地址；
- 不同地址绑定不同空间；
- 同一地址的回复按 thread 回到原 Session；
- 新 thread 创建新 Session，不影响前台 Session。

当前映射：

| 宿主 | 空间 | Session 能力 | 结论 |
|---|---|---|---|
| DSH | workspace | `agents.create/resume/followup` | 多地址 |
| OpenClaw | agentId | `/hooks/agent` + 可选 sessionKey | 多地址（thread 连续需 sessionKey） |
| Pi | cwd/project | 只有当前 Session，无后台创建 | **单地址** |

### 2.2 单地址模式（single-address）

没有稳定空间，或虽有 cwd/project 但不能主动创建/调用目标 Session 的宿主，只能：

- 一个 Agent 系统实例只绑定**一个**邮件地址；
- 入站邮件进入该实例当前 Session；
- 不支持多地址、空间路由和 Session 级隔离；
- 不提供系统级 onboarding 工具。

Pi 是典型：cwd 是“项目”，但没有后台 `createSession/deliverToSession`，所以只能单地址。

### 2.3 契约判定

实现方必须在安装/启动时声明：

```text
mode = multi-address | single-address
```

声明后，以下规则强制：

- multi-address：地址必须绑定 workspace/agentId；不同空间不同地址；
- single-address：整个实例最多一个 active 地址；多地址 bind 必须拒绝。

---

## 3. 地址绑定：B1 与 B2

### 3.1 B2：无系统约束

- 不需要本地系统对接；
- 地址激活码直接完成绑定；
- 适用于共享域、独立地址、单地址宿主；
- 绑定后不产生本地 system 配置。

### 3.2 B1：有系统约束

- 地址属于某个 Gateway system/domain；
- 激活前需要本地系统对接；
- 激活码申请时已经确定 B1/B2，用户不需要选择；
- 系统对接发生在地址绑定内部，不单独暴露工具。

### 3.3 统一入口

**只保留 `aimail_address`，移除独立 `aimail_system` 工具。**

`aimail_address` 参数：

```ts
{
  action: 'bind' | 'pause' | 'resume'
  email: string
  code: string              // 地址激活码
  system_code?: string      // 可选；出现即 B1
  gateway_url?: string      // 通常来自 activation prompt
}
```

规则：

- 无 `system_code` → **B2**；
- 有 `system_code` → **B1**；
- B1 且本地已有系统 → 忽略 `system_code`，直接走地址绑定；
- B1 且本地无系统 → 先用 `system_code` 完成系统对接，再执行地址绑定；
- `manager`、`system_name`、`domain` 不作为用户参数暴露；它们来自 activation prompt/网关申请结果；
- 不允许用户手工填写 manager 来绕过申请页。

### 3.4 B1 懒处理

```text
aimail_address(system_code, address_code)
  ├─ 本地已有 system? → 忽略 system_code
  └─ 没有 system?     → 先系统对接（domain/manager/默认工作员）
                        → 再地址激活绑定
  → 写 binding + pointer
  → arm SSE
```

系统对接失败时，地址绑定不写、不 arm、不 ack。

---

## 4. 激活码闭环

### 4.1 申请

用户在 Gateway 的 `/apply` 页面填写：

- 申请人/管理者邮箱；
- 地址名；
- 域/系统信息（页面自动给出可选域）；
- 单地址还是多地址/系统绑定（页面根据域和申请类型决定）。

Gateway 生成激活码并发送邮件到申请人邮箱。

### 4.2 邮件内容

邮件必须包含一个可直接复制到 Agent 对话框的 **activation prompt**，其中已经包含：

- 邮件地址；
- 地址激活码；
- B1 时的系统激活码；
- Gateway URL；
- 必要的域/系统提示（但不暴露 raw key）；
- 给 Agent 的执行指令：调用 `aimail_address bind`。

用户只需要理解两个概念：

```text
邮件地址
地址激活 prompt
```

base / advanced、共享域 / 独享域、有没有系统标识名、安全员是谁，都由 gateway 在申请时生成，用户不感知。

### 4.3 使用

```text
用户复制 activation prompt 到 Agent 对话框
  → Agent 调用 aimail_address
  → 工具读取 prompt 中的 code/system_code/gateway_url
  → B1/B2 自动判定
  → 绑定成功
  → SSE arm
```

### 4.4 一次性与错误处理

- 激活码一次性、有有效期；
- 地址码用于地址；系统码用于系统；混用必须拒绝；
- B1 的 system_code 已被使用且 system 存在 → 可忽略；
- B1 的 system_code 已过期/无效且无 system → 失败，不写地址；
- 地址码已绑定其他地址/系统 → 失败；
- 不允许通过 CLI、外部脚本或预写文件来完成激活。

---

## 5. AgentSystemId

### 5.1 定义

AgentSystemId 是 **Agent 系统实例** 的稳定标识，不是 Gateway 的 `system_id`。

它由宿主在首次激活时生成：

```text
AgentSystemId = stable_hash(
  agent_home_directory,
  machine_hardware_fingerprint
)
```

要求：

- 同一机器、同一 agent home：可重复生成，稳定；
- 不同 agent 实例/不同机器：全局唯一（碰撞可忽略）；
- 不包含 secret；
- 不是 Gateway system_id，也不是邮箱/地址。

### 5.2 注册

首次通过 `aimail_address` 访问 Gateway 时：

1. 宿主计算 AgentSystemId；
2. 携带该 id 完成首次激活/注册；
3. Gateway 记录该 AgentSystemId；
4. Gateway 为该 AgentSystemId 授权多地址聚合拉取；
5. 后续 B1/B2 地址都归到该 AgentSystemId。

### 5.3 SSE 连接单位

```text
一个 AgentSystemId = 一条 SSE 连接
```

不是：

- 每个地址一条；
- 每个 workspace 一条；
- 每个 Session 一条；
- 每个 Gateway system_id 一条。

一个 Agent 系统绑定多个地址（B1 或 B2 混合）时：

- 仍只有一条 SSE；
- Gateway 按 AgentSystemId 聚合该系统的所有地址邮件；
- 本地收到 batch 后按 recipient → binding → workspace → thread/session 路由。

### 5.4 Gateway 侧职责

Gateway 必须：

- 认证 AgentSystemId；
- 校验它有权聚合哪些地址；
- 返回该 AgentSystemId 的 pending deliveries；
- 不在地址之间串信；
- 支持地址增删/暂停后聚合范围更新。

### 5.5 本地路由

收到聚合 batch 后：

```text
delivery.recipient
  → binding lookup (email)
  → 空间 workspace/agentId
  → thread root
  → 存在则 followup 原 session
  → 否则新建 session（cwd=workspace_path）
  → 成功投递后 ack
```

无 binding、paused、workspace miss、session 创建失败：

- **不 ack**；
- 留在 pending；
- 记录可诊断原因。

---

## 6. 安装场景

### 6.1 首次安装：B2 单地址

```text
安装插件
→ 用户粘贴 B2 activation prompt
→ aimail_address bind(code, email)
→ 生成/注册 AgentSystemId
→ 写 binding + pointer
→ 一条 SSE arm
→ welcome 经 SSE 到达
```

适用于 Pi、单地址 DSH、独立地址。

### 6.2 首次安装：B1 多地址

```text
安装插件
→ 用户粘贴 B1 activation prompt（含 system_code）
→ aimail_address bind(code, system_code)
→ 本地无 system：先系统对接
→ 地址激活
→ AgentSystemId 注册
→ 一条 SSE arm
→ 后续多地址继续绑定到同一 AgentSystemId
```

### 6.3 已有系统/地址

```text
宿主启动
→ 读已有 binding/pointer
→ 启动即 arm 已有 AgentSystemId 的 SSE
→ 不重新激活、不创建邮箱
```

写入绑定的唯一合法来源仍是宿主内 `aimail_address`。外部预写只允许作为测试夹具，不能作为产品激活路径。

---

## 7. 安全边界

- activation prompt 只含一次性 code 和公开 Gateway URL；
- 不含 admin key、domain key、raw system key、bridge key；
- manager 由申请页/网关决定，工具不接受；
- AgentSystemId 不是 secret，不能替代认证；
- SSE 认证仍使用 Gateway 签发的凭据；
- 日志不得记录 code、raw key、完整认证头。

---

## 8. 宿主能力矩阵

| 能力 | 多地址 | 单地址 |
|---|---|---|
| 绑定多个地址 | 必须 | 禁止 |
| 地址→空间 | 必须 | 不适用（固定当前空间） |
| 新 thread 新 session | 必须 | 不允许 |
| 回复回原 session | 必须 | 当前 session |
| AgentSystemId | 必须 | 必须 |
| 一条 SSE | 必须 | 必须 |
| 独立 system tool | 不提供 | 不提供 |

---

## 9. 与 V1 的差异

| V1 | V2 |
|---|---|
| `aimail_system` + `aimail_address` 两个工具 | 只保留 `aimail_address`，`system_code` 可选 |
| B1/B2 由工具/用户选择 | 由 activation prompt 决定 |
| SSE 按 system/address 分 | 按 AgentSystemId 聚合，一条 SSE |
| 安装场景未说明 | 明确 B1/B2 首次/已有/重启 |
| 激活码来源分散 | `/apply` → 邮件 → activation prompt 闭环 |
| 单/多地址宿主不区分 | 明确能力模型与禁止项 |

---

## 10. 实现状态（诚实标注）

V2 是目标契约。截至本文件编写时：

- DSH：多地址、workspace、session 路由已实现；
- OpenClaw：agentId 空间、sessionKey 条件支持；AgentSystemId 聚合尚未实现；
- Pi：单地址，`aimail_address`，无 system 工具；
- `aimail_system` 工具仍可能存在于旧实现，需要按 V2 移除；
- AgentSystemId 派生与 Gateway 聚合授权尚未落地。

任何未实现项不得在验收中声明为已完成。

---

## Appendix A — OpenClaw

- 空间：agentId；
- thread 连续：需 `hooks.allowRequestSessionKey=true` + `allowedSessionKeyPrefixes`；
- 未开启：workspace 路由正常，但每封邮件新 session，thread 不连续。

## Appendix B — Pi

- 单地址；
- 无后台 createSession/deliverToSession；
- 地址绑定以当前 cwd 为 workspace；
- 不提供系统级工具。

## Appendix C — 激活 prompt 示例

```text
请调用 aimail_address 完成地址激活：
action="bind"
email="alice@example.com"
code="addr-..."
system_code="sys-..."   # 仅 B1 出现
gateway_url="https://gateway.example.com"
```
