# AIMail Host SSE Activation Contract v2

状态：**现行契约**  
适用对象：通过 SSE 拉取邮件、在 Agent 会话内完成激活的 Agent 宿主  
规范词：MUST / SHOULD / MAY 按 RFC 2119 解释

本文是一份完整契约。它不依赖任何历史文档；读者只需要本文即可实现
AIMail 的安装、激活、SSE 收信、地址路由和安全边界。

---

## 1. 目标与非目标

### 1.1 目标

1. 用户从 Gateway 的申请页获得**地址激活 prompt**，粘贴进 Agent 对话框即可完成安装；
2. 激活码只在宿主内通过工具兑换，不需要 CLI、外部脚本或预写配置；
3. Agent 系统可以是单地址，也可以是多地址；
4. 多地址时，地址绑定到稳定的 Workspace/Agent 空间，入站再路由到 Session；
5. 同一 Agent 系统访问多个 Gateway 时，每个 Gateway 使用独立 SSE；
6. 邮件到达后按 recipient → binding → workspace → thread/session 路由，成功后才 ack；
7. 全流程不在对话、日志、binding 中暴露 admin/domain/system raw key。

### 1.2 非目标

- 不定义 Gateway 的 SMTP/存储/管理面板内部实现；
- 不要求宿主支持 CLI；
- 不要求宿主使用 webhook 或本地 inbound listener；
- 不把 Session 作为持久路由键。

---

## 2. 术语

| 术语 | 含义 |
|---|---|
| Gateway / gw | AIMail 网关；base 或 advanced 对安装流程完全等价。 |
| Agent 系统 | 一个 Agent 产品实例，例如 DSH web、OpenClaw gateway、Pi 进程。 |
| Agent 空间 | 稳定容器，可承载多个 Session；DSH 为 workspace，OpenClaw 为 agentId。 |
| Session | 一次 LLM 对话；可创建、调用、结束。 |
| AgentSystemId | Agent 系统实例的稳定客户端身份，不是 Gateway 的 system_id。 |
| B1 | 带系统约束的地址绑定，需要本地系统对接。 |
| B2 | 无系统约束的地址绑定，不需要本地系统。 |
| Activation code | Gateway `/apply` 申请后通过邮件下发的一次性码。 |
| Activation prompt | 邮件中自动生成、可直接粘贴到 Agent 对话框的激活指令。 |
| Binding | 地址在宿主的持久配置：Gateway、地址、空间、凭据、状态。 |
| 空间路由 | 地址 → Agent 空间 → Session 的投递过程。 |

---

## 3. 宿主能力模型

宿主 MUST 在安装/启动时声明自己属于哪一类。分类由宿主自身结构决定，不是
由 Gateway 决定。

### 3.1 多地址宿主（multi-address）

MUST 同时具备：

1. **稳定空间**：有名称、跨 Session 稳定的 Workspace/Agent 空间；
2. **多 Session**：空间内可创建多个独立 Session；
3. **主动调用**：外部事件可创建 Session，或向指定 Session 投递；
4. **空间解析**：可从 Session/事件解析出其所属空间；
5. **Session 可弃用**：binding 只保存空间，不保存 Session 身份。

满足后：

- 一个 Agent 系统可绑定多个地址；
- 不同地址可绑定不同空间；
- 同一地址不同 thread 可对应不同 Session；
- 同一 thread 的回复必须回到原 Session；
- 新 thread 创建新 Session，不干扰前台 Session。

### 3.2 单地址宿主（single-address）

没有稳定空间，或虽有 cwd/project 但无法主动创建/调用目标 Session 的宿主：

- 整个 Agent 系统实例最多一个 active 地址；
- 入站邮件进入当前 Session；
- 不提供多地址、空间路由、Session 级隔离；
- MUST 拒绝第二个地址绑定；
- MUST NOT 暴露系统级 onboarding 工具。

### 3.3 判定规则

| 能力 | 多地址 | 单地址 |
|---|---|---|
| 绑定多个地址 | 允许 | 禁止 |
| 地址绑定空间 | 必须 | 固定当前空间 |
| 新 thread 新 Session | 必须 | 不支持 |
| 回复回原 Session | 必须 | 当前 Session |
| AgentSystemId | 必须 | 必须 |
| 每个 Gateway 一条 SSE | 必须 | 必须 |

---

## 4. 地址绑定模型

### 4.1 两种绑定

#### B1：带系统约束

- 地址属于某个 Gateway system/domain；
- 激活前需要本地系统对接；
- 系统对接在地址绑定内部懒执行；
- 用户不需要选择“B1”，该信息来自 activation prompt。

#### B2：无系统约束

- 地址不依赖本地 system；
- 地址激活码直接完成绑定；
- 不产生本地 system 配置；
- 适用于共享域、独立地址、单地址宿主。

### 4.2 唯一工具入口

宿主 MUST 只暴露 `aimail_address` 作为地址生命周期入口：

```ts
{
  action: 'bind' | 'pause' | 'resume'
  email: string
  code: string
  system_code?: string
  gateway_url?: string
}
```

- 无 `system_code`：B2；
- 有 `system_code`：B1；
- B1 且本地已有对应 system：忽略 `system_code`，直接地址绑定；
- B1 且本地无 system：先用 `system_code` 完成系统对接，再地址绑定；
- `manager`、`system_name`、`domain` 不作为用户参数；它们来自激活码/激活 prompt；
- 工具 MUST NOT 接受 admin key、domain key、raw system key。

### 4.3 B1 懒处理

```text
aimail_address(system_code?, address_code)
  ├─ 无 system_code → B2
  └─ 有 system_code
       ├─ 本地已有 system → 忽略 system_code
       └─ 本地无 system   → 系统对接
  → 地址激活
  → 写 binding + pointer
  → arm SSE
```

系统对接失败时：

- 不写地址 binding；
- 不 arm SSE；
- 不 ack；
- 返回可诊断错误，激活码不被错误消费。

---

## 5. 激活码闭环

### 5.1 申请

用户在 Gateway 的 `/apply` 页面填写：

- 管理者/申请人邮箱；
- 地址名；
- 域/系统信息；
- 申请类型（地址/系统绑定需求）。

页面和 Gateway 决定：

- 共享域还是独享域；
- 系统标识名；
- 安全员/manager；
- 本次是 B1 还是 B2；
- 是否需要 system activation code。

### 5.2 下发

Gateway 向申请人邮箱发送激活邮件。邮件 MUST 包含可直接粘贴到 Agent
对话框的 activation prompt，其中包含：

- `email`
- `code`
- B1 时的 `system_code`
- `gateway_url`
- 给 Agent 的执行说明

邮件 MUST NOT 包含 raw admin/domain/system key。

### 5.3 使用

```text
用户复制 activation prompt
  → Agent 调用 aimail_address
  → 工具解析 code/system_code/gateway_url
  → B1/B2 自动判定
  → 完成绑定
  → arm SSE
```

用户只感知：

```text
邮件地址
地址激活 prompt
```

base/advanced、共享/独享、manager 等差异由 Gateway 在申请时落进 prompt，
不由用户选择。

### 5.4 一次性与错误

- code 一次性、有有效期；
- 地址码用于地址，system code 用于系统；混用 MUST 拒绝；
- system code 已使用且本地 system 存在：可忽略；
- system code 过期/无效且无 system：失败，不写地址；
- 地址码已绑定其他地址/系统：失败；
- 不接受 CLI、外部脚本、预写 binding 作为激活方式。

---

## 6. AgentSystemId

### 6.1 定义

AgentSystemId 是 **Agent 系统实例** 的稳定客户端 ID，不是 Gateway system_id。

派生输入：

```text
agent_home_directory
machine_hardware_fingerprint
```

要求：

- 同机器、同 agent home：稳定、可重复生成；
- 不同 Agent 实例/机器：全局唯一（碰撞可忽略）；
- 不含 secret；
- 不能由地址或 Gateway system_id 代替。

### 6.2 注册

首次激活访问 Gateway 时：

1. 宿主计算 AgentSystemId；
2. 激活请求携带该 ID；
3. Gateway 记录该 AgentSystemId；
4. Gateway 为该 ID 授权地址聚合；
5. 该 Gateway 下所有 B1/B2 地址归入该 AgentSystemId。

AgentSystemId 在每个 Gateway 分别注册；同一 Agent 系统访问多个 Gateway 时，
在每个 Gateway 各有一份授权记录。

### 6.3 稳定性

- 重启：不变；
- 地址增删：不变；
- 系统增删：不变；
- agent home 迁移/硬件变化：变为新 ID；旧 binding 需要重新激活，不能静默复用。

---

## 7. SSE 拓扑

### 7.1 连接单位

SSE 的连接单位是：

```text
(AgentSystemId, Gateway)
```

即：

- 同一个 AgentSystemId；
- 同一个 Gateway；
- 一条 SSE。

不是：

- 每个地址一条；
- 每个 workspace 一条；
- 每个 Session 一条；
- 每个 Gateway system_id 一条。

### 7.2 多 Gateway

一个 Agent 系统可以同时对接多个 Gateway。此时：

```text
AgentSystemId
  ├─ Gateway A → SSE A
  ├─ Gateway B → SSE B
  └─ Gateway C → SSE C
```

每个 Gateway 一条独立 SSE；同一 Gateway 下所有地址共享该条 SSE。

### 7.3 Gateway 聚合授权

Gateway MUST：

- 认证 AgentSystemId；
- 授权它聚合本 Gateway 下已绑定地址；
- 按 AgentSystemId 返回 pending deliveries；
- 支持地址新增/暂停/恢复后更新聚合范围；
- 不把一个地址的邮件投给另一个 AgentSystemId。

### 7.4 本地路由

收到聚合 batch 后：

```text
delivery.gateway_url + recipient
  → binding lookup
  → Agent 空间
  → thread root
  → 已有 Session: followup
  → 无 Session: create（cwd = 空间路径）
  → 投递成功后 ack
```

无 binding、paused、空间 miss、Session 创建失败：

- MUST NOT ack；
- 留 pending；
- 记录可诊断原因。

---

## 8. Binding 与持久化

每个地址一个 binding，至少包含：

| 字段 | 说明 |
|---|---|
| `gateway_url` | 所属 Gateway |
| `email` | 地址 |
| `system_id` | B1 时的 Gateway system；B2 可空 |
| `agent_system_id` | 本机 AgentSystemId |
| `workspace_id` / `workspace_path` | 多地址宿主的稳定空间 |
| 凭据 | Gateway 签发的地址/系统凭据 |
| `manager_address` | 安全员（从 Gateway/激活结果来） |
| `paused` | 本地暂停状态 |

禁止：

- 以 Session id 作为 binding 路由键；
- 在 binding 中保存一次性激活码；
- 在 binding 中保存 raw admin/domain/system key。

B2 可以没有本地 system 配置；B1 必须有对应 system 配置。

---

## 9. 安装状态与混合态

安装不是单一路径，而是多个状态轴：

- system：无 / 有；
- 地址：0 / 1 / 多；
- 绑定类型：B1 / B2 / 混合；
- Gateway：1 / 多；
- 状态：active / paused / 部分失败；
- 宿主：单地址 / 多地址。

### 9.1 典型状态

#### S0 空系统

- 无 system、无 binding、无 SSE；
- 等待 activation prompt。

#### S1 B2 单地址

- 无 system；
- 一个 B2 binding；
- 当前 Gateway 一条 SSE。

#### S2 B1 单地址

- 有 system；
- 一个 B1 binding；
- 当前 Gateway 一条 SSE。

#### S3 B1 多地址

- 有 system；
- 多个地址绑定不同空间；
- 当前 Gateway 仍一条 SSE，本地路由。

#### S4 B2 多地址（多地址宿主）

- 无 system；
- 多个 B2 binding；
- 当前 Gateway 一条 SSE。

#### S5 B1+B2 混合

- 有 system，同时存在不依赖 system 的 B2 地址；
- 当前 Gateway 一条 SSE；
- 本地按 binding 路由，不能假设所有地址都有 system。

#### S6 多 Gateway

- 同一 AgentSystemId 对接多个 Gateway；
- 每个 Gateway 一条 SSE；
- binding 用 `gateway_url + email` 唯一定位；
- 地址可在不同 Gateway 上重名，不得混淆。

#### S7 部分失败

- 地址已写 binding，但 SSE 未 arm；
- 或 system 成功、地址失败；
- 或某个 Gateway 离线；
- 已成功地址 MUST 继续工作；失败项 MUST 留 pending/可重试，不得伪造 active。

#### S8 重启恢复

- 启动读取已有 binding；
- 按 `(AgentSystemId, Gateway)` 重新 arm；
- 不重新激活、不创建新地址；
- 无 binding 时 0 SSE 且启动成功。

#### S9 暂停/恢复

- paused 地址不参与聚合，不 ack；
- resume 后重新纳入聚合并 drain。

#### S10 身份变化

- agent home/硬件变化 ⇒ 新 AgentSystemId；
- 旧 binding 不能静默沿用；
- 必须重新激活/重新注册，避免跨实例串信。

### 9.2 混合态要求

- 任何状态组合都必须能启动；
- 未完成项不能阻塞已完成项；
- 单地址宿主出现第二个地址必须拒绝；
- 多地址宿主不能把不同空间混成一个；
- 多 Gateway 不能把不同 Gateway 的 pending/ack 混用；
- 任何状态都不得用外部预写 binding 作为激活。

---

## 10. 工具面

宿主 MUST 暴露一个逻辑 toolset，包含：

- 邮件工具（发送、联系人、摘要、搜索）；
- Board/A2A 工具；
- `aimail_address`。

MUST NOT 暴露：

- `aimail_system` 独立工具；
- 系统激活码专用工具；
- 任意接受 raw key 的工具。

系统对接只能作为 `aimail_address` 的 B1 懒步骤存在。

---

## 11. 安全

- activation prompt 只含一次性 code 和公开 Gateway URL；
- manager 由申请页/Gateway 决定；
- AgentSystemId 不是 secret，不能替代认证；
- SSE 用 Gateway 签发的凭据；
- 日志不记录 code、raw key、完整认证头；
- 外部写 binding 不是激活路径，只允许测试夹具；
- 单地址宿主必须拒绝多地址，防止串信。

---

## 12. 一致性要求

实现 MUST 能证明：

| ID | 场景 | 判据 |
|---|---|---|
| C1 | 单地址宿主 | 第二个地址 bind 被拒绝 |
| C2 | B2 | 无 system，地址码兑换，binding 有空间，SSE arm |
| C3 | B1 懒系统 | 无 system 时先系统对接再地址；已有 system 时忽略 system_code |
| C4 | 激活码闭环 | prompt 中 email/code/gateway 可执行；base/advanced 同流程 |
| C5 | AgentSystemId | 重启稳定；home/硬件变化生成新 ID |
| C6 | 单 Gateway 聚合 | 多地址共享一条 SSE，按 recipient 路由 |
| C7 | 多 Gateway | 每个 Gateway 一条独立 SSE，互不串 pending |
| C8 | 混合 B1/B2 | 同一 AgentSystemId 下正确路由 |
| C9 | Session 路由 | 同 thread followup，新 thread 新 Session；空间=cwd |
| C10 | ack | 成功投递后才 ack；miss/paused 不 ack |
| C11 | 重启 | 已有 binding 启动即 arm；无 binding 0 SSE |
| C12 | 安全 | 无 raw key/code 日志；外部预写不算激活 |

---

## 13. 宿主适配附录

### 13.1 DSH

- 多地址；
- 空间 = workspace；
- Session 创建/恢复 = agents；
- SSE 按 AgentSystemId+Gateway；
- 当前实现尚未实现 AgentSystemId 聚合授权，需按本文补齐。

### 13.2 OpenClaw

- 多地址；
- 空间 = agentId；
- thread 连续需要 `hooks.allowRequestSessionKey=true` + 前缀白名单；
- 未开启时 workspace 路由可用，但每封邮件新 session；
- 插件必须声明真实工具名，`registerTool` 工厂需要 `names` 元数据；
- SSE 按 AgentSystemId+Gateway。

### 13.3 Pi

- 单地址；
- 无后台创建/调用 Session；
- 地址绑定当前 cwd；
- 不提供系统级工具；
- SSE 按 AgentSystemId+Gateway，但一个实例只有一个地址。

---

## 14. 参考实现状态

本文是契约，不是完成声明。实现状态：

| 能力 | DSH | OpenClaw | Pi |
|---|---|---|---|
| 多地址 | 已实现 | 条件实现 | 不支持 |
| 空间路由 | 已实现 | 条件实现 | 单地址 |
| AgentSystemId | 待实现 | 待实现 | 待实现 |
| 多 Gateway SSE | 待实现 | 待实现 | 待实现 |
| B1 懒系统对接 | 待实现 | 待实现 | 不适用 |
| `aimail_address` 唯一入口 | 待统一 | 待统一 | 已单地址 |

未实现项不得在验收中声明为已完成。

---

## Appendix A — activation prompt 示例

```text
请调用 aimail_address 完成地址激活：
action="bind"
email="alice@example.com"
code="addr-..."
system_code="sys-..."    # 仅 B1 出现
gateway_url="https://gateway.example.com"
```

## Appendix B — 用户可见概念

用户只需要理解：

1. 我的邮件地址；
2. 申请页/邮件给我的地址激活 prompt。

其余域、系统、manager、B1/B2、base/advanced 均由 Gateway 生成。
