# AIMail Host SSE Activation Contract v2

状态：**现行契约**  
读者：Agent 宿主实现者、Gateway 实现者、QA  
规范词：MUST / SHOULD / MAY 按 RFC 2119 解释

本文是完整、独立的契约。安装、激活、SSE、路由、解绑迁移、数据布局、
安全与验收均由本文定义；不依赖其他历史文档。

---

## 1. 目标与边界

### 1.1 目标

1. 用户在 Gateway 申请页获得**地址激活 prompt**，粘贴到 Agent 对话框即可安装；
2. 激活码只在宿主进程内通过工具兑换，不依赖 CLI/外部脚本/预写 binding；
3. 宿主可以按自身结构选择**单地址**或**多地址**模式；
4. 多地址模式中，地址绑定稳定 Agent 空间，入站再路由到 Session；
5. 一个 Agent 系统访问多个 Gateway 时，每个 Gateway 一条独立 SSE；
6. 邮件只有成功投递到 Session 后才 ack；
7. 对话、日志、binding 不暴露 raw admin/domain/system key。

### 1.2 非目标

- 不定义 Gateway 的 SMTP、数据库、管理面板实现；
- 不要求 webhook、本地 inbound listener、CLI；
- 不把 Session 当持久路由键。

---

## 2. 术语

| 术语 | 定义 |
|---|---|
| Gateway / gw | AIMail 网关；base/advanced 对安装流程等价。 |
| Agent 系统 | 一个 Agent 产品实例，如 DSH web、OpenClaw gateway、Pi 进程。 |
| Agent 空间 | 稳定容器，可承载多个 Session；DSH=workspace，OpenClaw=agentId。 |
| Session | 一次 LLM 对话；可创建、调用、结束。 |
| AgentSystemId | Agent 系统实例的稳定客户端 ID，基于 agent home + 硬件指纹。 |
| GatewaySystemId | Gateway 在系统对接时生成的 system 标识，是服务端作用域 ID。 |
| B1 | 带系统约束的地址绑定，需要本地系统对接。 |
| B2 | 无系统约束的地址绑定，不需要本地系统。 |
| Address code | 地址一次性激活码。 |
| System code | 系统一次性激活码，B1 使用。 |
| Activation prompt | 申请页邮件中自动生成、可粘贴到 Agent 的指令。 |
| Binding | 地址在宿主的持久配置。 |
| Aggregation | Gateway 按 AgentSystemId 聚合其已绑定地址的 pending deliveries。 |

> **两个 system id 不相同：**
> - `GatewaySystemId` 是 Gateway 服务端的系统/域/manager 作用域；
> - `AgentSystemId` 是本地客户端实例身份，用于 SSE 聚合与本地路由。
> 二者不得互相替代。

---

## 3. 宿主能力模型

### 3.1 多地址宿主

MUST 同时具备：

1. 稳定 Agent 空间（名称稳定、可解析）；
2. 空间内可创建多个 Session；
3. 外部事件可创建 Session 或向指定 Session 投递；
4. 地址绑定空间，入站按空间路由；
5. binding 不保存 Session 身份。

满足后允许：

- 一个 Agent 系统绑定多个地址；
- 不同地址绑定不同空间；
- 同 thread 回复回到原 Session；
- 新 thread 创建新 Session，不干扰前台。

### 3.2 单地址宿主

没有稳定空间，或有 cwd/project 但无法主动创建/调用目标 Session：

- 整个实例最多一个 active 地址；
- 入站邮件进入当前 Session；
- MUST 拒绝第二个地址；
- MUST NOT 暴露系统级 onboarding 工具；
- 不支持空间路由和 Session 隔离。

### 3.3 能力矩阵

| 能力 | 多地址 | 单地址 |
|---|---|---|
| 多地址绑定 | 允许 | 禁止 |
| 地址→空间 | 必须 | 固定当前空间 |
| 新 thread 新 Session | 必须 | 不支持 |
| 回复回原 Session | 必须 | 当前 Session |
| AgentSystemId | 必须 | 必须 |
| 每个 Gateway 一条 SSE | 必须 | 必须 |

---

## 4. 两个系统 ID 的职责

### 4.1 GatewaySystemId

由 Gateway 在系统对接时生成，MUST 只用于：

- 服务端 system/domain/manager/default worker 作用域；
- B1 地址的归属校验；
- 系统级 API key/domain key 的签发范围；
- 系统级审计、白名单、欢迎邮件 manager。

它 MUST NOT：

- 作为本地 SSE 的身份；
- 作为本地路由键；
- 作为 Session 路由键；
- 跨 Agent 系统共享。

### 4.2 AgentSystemId

本地派生：

```text
AgentSystemId = stable_hash(agent_home_directory, machine_hardware_fingerprint)
```

要求：

- 同机同 home 稳定、可重复生成；
- 不同实例/机器全局唯一（碰撞可忽略）；
- 不含 secret；
- 每个 Gateway 分别注册；
- Gateway 授权它聚合**由该 AgentSystemId 激活/绑定的地址**，而不是该 Gateway 下所有地址。

### 4.3 B1 不做系统对接的后果

B1 MUST 先完成系统对接；不能只兑换地址码。

若不执行系统对接：

- 缺少 GatewaySystemId 对应的 system/domain 配置；
- 缺少系统级 manager/default worker/domain key；
- 无法完成 B1 的系统归属校验；
- 地址绑定不得写入，不得 arm SSE，不得 ack。

因此 B1 的 `system_code` 不是可选项的“提示”，而是 B1 完成条件。

B2 不需要本地系统配置；Gateway 返回的 `GatewaySystemId` 只作为服务端归属信息，
不要求本地 system 目录。

---

## 5. 地址绑定：B1 与 B2

### 5.1 唯一工具

宿主 MUST 只暴露 `aimail_address`：

```ts
{
  action: 'bind' | 'pause' | 'resume' | 'unbind' | 'move'
  email: string
  code?: string
  system_code?: string
  gateway_url?: string
  workspace_path?: string   // move 时使用
}
```

- 无 `system_code`：B2；
- 有 `system_code`：B1；
- B1 且本地已有系统：忽略 `system_code`，直接地址绑定；
- B1 且本地无系统：先系统对接，再地址绑定；
- `manager`、`system_name`、`domain` MUST NOT 由用户直接提供；来自 Gateway/activation prompt；
- MUST NOT 接受 raw admin/domain/system key。

`aimail_system` 不是独立工具；系统对接只能作为 B1 的懒步骤。

### 5.2 B1 懒流程

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

系统对接失败：不写地址、不 arm、不 ack、不消费地址码。

---

## 6. 激活码闭环

### 6.1 申请

用户在 Gateway `/apply` 填写：

- 管理者/申请人邮箱；
- 地址名；
- 域/系统信息；
- 申请类型。

Gateway 决定：

- 共享域/独享域；
- 系统标识名；
- manager；
- B1/B2；
- 是否包含 system code。

### 6.2 下发

激活邮件 MUST 包含可直接粘贴的 activation prompt：

- `email`
- `code`
- B1 的 `system_code`
- `gateway_url`
- Agent 执行说明

MUST NOT 包含 raw keys。base/advanced、域、manager 均由 Gateway 在申请时决定。

### 6.3 使用

```text
复制 prompt → Agent 调 aimail_address → B1/B2 判定 → 绑定 → arm
```

用户只感知：邮件地址、地址激活 prompt。

### 6.4 一次性与错误

- code 一次性、有有效期；
- 地址码只对地址；系统码只对系统；
- system code 已用且本地 system 存在：可忽略；
- system code 无效且无 system：B1 失败；
- 地址码已绑定其他地址/系统：失败；
- CLI/外部脚本/预写 binding 不是激活路径。

---

## 7. SSE 拓扑

### 7.1 连接单位

```text
(AgentSystemId, Gateway) → 一条 SSE
```

不是每个地址、每个 workspace、每个 Session、每个 GatewaySystemId 一条。

### 7.2 多 Gateway

同一 AgentSystemId 可对接多个 Gateway：

```text
AgentSystemId
  ├─ Gateway A → SSE A
  ├─ Gateway B → SSE B
  └─ Gateway C → SSE C
```

每个 Gateway 各自一条 SSE；binding 用 `gateway_url + email` 唯一定位。

### 7.3 聚合授权

Gateway MUST：

- 认证 AgentSystemId；
- 授权它聚合**由该 AgentSystemId 激活/绑定的地址**；
- 不把该 Gateway 下其他 AgentSystemId 的地址投给它；
- 地址新增/暂停/恢复/解绑后更新聚合集合；
- 多 Gateway 各自独立 pending/ack。

### 7.4 本地路由

```text
delivery.gateway_url + recipient
  → binding lookup
  → Agent 空间
  → thread root
  → 已有 Session: followup
  → 无 Session: create（cwd=workspace_path）
  → 成功投递后 ack
```

无 binding、paused、空间 miss、Session 创建失败：不 ack、留 pending、记录原因。

---

## 8. Binding 与数据布局

Root 由宿主定义（默认 `~/.aimail`，可配置）。文件名取 Gateway/宿主契约单源，
本文不硬编码具体文件名。

```text
<aimail_home>/
├── systems/<GatewaySystemId>/        # B1 系统级配置（B2 可不存在）
│   ├── system config                 # gateway_url/domain/manager/keys
│   ├── board/role_prompt/
│   └── <clean_addr>/
│       ├── address binding           # 见 8.1
│       ├── address ledger
│       └── mail/
│           ├── YYYYMM/in-*.json
│           ├── YYYYMM/out-*.json
│           ├── YYYYMM/attch/<mid>/
│           ├── meta/<first2>/<mid>.json
│           ├── threads/<first2>/<tid>.json
│           └── .search/index.db
```

B2 地址可以没有 systems 父目录；由宿主按实现布局保存，但 MUST 与 B1 地址一样
有 binding、ledger、incoming/outgoing 快照、attachment、meta、thread、search。

### 8.1 Binding 必填

| 字段 | 含义 |
|---|---|
| `gateway_url` | 所属 Gateway |
| `email` | 地址 |
| `agent_system_id` | 本机 AgentSystemId |
| `workspace_id` / `workspace_path` | 多地址宿主必填；单地址为当前空间 |
| `system_id` | B1 的 GatewaySystemId；B2 可为空/仅 provenance |
| 凭据 | Gateway 签发的地址/系统凭据 |
| `manager_address` | 安全员（来自 Gateway/activation） |
| `paused` | 本地暂停 |
| `binding_kind` | B1 / B2 |

禁止：

- `session_id` 或等价 per-session 键；
- 一次性 code；
- raw admin/domain/system key；
- 用 workspace 之外的地址身份做路由。

### 8.2 权限

- 目录 0700；
- binding/system config/pointer 0600；
- 邮件快照/附件 0600/0700；
- 日志不输出密钥。

---

## 9. SSE 投递契约

### 9.1 连接

- 每个 `(AgentSystemId, Gateway)` 一条连接；
- 连接身份由 Gateway 签发的 AgentSystemId 凭据认证；
- 不按地址重复建连；
- 同一 Gateway 下所有 active 地址共享该连接；
- 多 Gateway 各自连接，不合并 pending/ack。

### 9.2 流与重连

- 心跳/长连接保持；
- 断线指数退避重连；
- 重连后重新认证并恢复聚合集合；
- 不因重连重复创建 Session/重复 ack；
- 409/占用按 Gateway 规则退避，不误判为邮件失败。

### 9.3 Ack

- 只有成功接受进 Session 后才 ack；
- workspace miss、binding miss、paused、Session 创建失败：不 ack；
- at-least-once，重复 delivery 幂等；
- 多 Gateway 的 ack 只发给对应 Gateway。

### 9.4 状态

```json
{
  "mode": "sse",
  "agentSystemId": "...",
  "gateways": [
    {"gatewayUrl":"...", "connected":true, "lastEventAt":"...", "lastError":""}
  ]
}
```

状态不得包含 key/code/邮件正文。

---

## 10. 入站路由与 Session 语义

1. 用 `gateway_url + recipient` 找 binding；
2. 无 binding/paused：不 ack；
3. 解析 binding 的 workspace；
4. thread root 顺序：`references[0]` → `in_reply_to` → `message_id`；
5. 读本地 thread meta 的 Session；
6. 同空间且 Session 可用：followup；
7. 否则新建 Session，cwd/binding 空间；
8. 投递成功后写 meta（session/workspace）并 ack；
9. Session 可弃用；binding 不持久化 Session；
10. 多 Gateway 不共享 thread meta，除非同一空间且同一 Gateway。

---

## 11. Welcome / manager

- Welcome 是普通 inbound，走 pending/SSE，不依赖 listener；
- Welcome 正文的 persona/signature/current_time 与 manager 指令 MUST 保持语义；
- manager cc/回复是普通 inbound；
- 首次 welcome 重放必须按 message id 去重；
- 无 binding 不自动回复、不 ack；
- B1 manager 来自系统/申请；B2 由 Gateway 决定。

---

## 12. 安装状态与混合态

状态轴：system 有/无、地址 0/1/多、B1/B2/混合、Gateway 单/多、active/paused/部分失败、单/多地址宿主。

### 12.1 状态

| 状态 | 说明 |
|---|---|
| S0 | 空系统，无 SSE |
| S1 | B2 单地址 |
| S2 | B1 单地址 |
| S3 | B1 多地址 |
| S4 | B2 多地址（多地址宿主） |
| S5 | B1+B2 混合 |
| S6 | 多 Gateway |
| S7 | 部分失败（某地址/某 Gateway） |
| S8 | 重启恢复 |
| S9 | pause/resume |
| S10 | AgentSystemId 变化（home/硬件迁移） |

### 12.2 混合态要求

- 已完成项不能被未完成项阻塞；
- 单地址宿主拒绝第二地址；
- 多地址宿主不能混空间；
- 多 Gateway 不混 pending/ack；
- 外部预写 binding 不是激活；
- 任何状态都必须能启动，未就绪项给出可诊断状态。

---

## 13. 解绑与迁移

### 13.1 pause

- 本地标记 paused；
- 从该 Gateway 聚合集合移除；
- 不 ack 新 delivery；
- resume 后重新纳入并 drain。

### 13.2 unbind（本地）

- MUST 停止该地址的投递路由；
- 从聚合集合移除；若该 Gateway 无其他地址，停止该 Gateway SSE；
- 归档 binding 与本地凭据；
- 仅当 pointer 指向该地址时清除 pointer；
- Gateway 远端 key 不自动删除；远端 deregister 是独立操作；
- 解绑后 pending 不 ack，保持可诊断；
- 重新 bind 同一地址可从归档恢复；新 code 则按 Gateway 规则轮换。

### 13.3 move（同 AgentSystemId 内换空间）

- 只更新 binding 的 workspace_id/path；
- 不兑换新 code；
- 不新建 SSE；
- 后续邮件路由到新空间；
- 已投递 Session 不迁移，旧空间继续保留历史；
- move 必须校验新 workspace 存在且属于当前 Agent 系统。

### 13.4 跨 AgentSystemId / 跨机器迁移

- 旧实例必须先 unbind；
- 新实例生成新 AgentSystemId；
- 地址重新激活/重新签发凭据，不能复制旧 binding 直接使用；
- 避免跨实例串信；
- Gateway 侧需解除旧 AgentSystemId 的聚合授权。

### 13.5 跨 Gateway 迁移

- 不是本地迁移；
- 新 Gateway 需要独立激活码和 binding；
- 本地以 `gateway_url + email` 区分，不能复用旧 Gateway 的 pending/ack。

### 13.6 Agent home/硬件变化

- AgentSystemId 变成新值；
- 旧 binding 不再有效；
- 必须重新激活并建立新聚合授权；
- 不静默沿用旧 ID。

---

## 14. 工具面

一个逻辑 toolset 包含：

- 邮件工具；
- board/A2A 工具；
- `aimail_address`。

MUST NOT 包含：

- 独立 `aimail_system`；
- 系统激活码专用工具；
- 接受 raw key 的工具。

系统对接只能作为 B1 懒步骤存在。

---

## 15. 安全

- activation prompt 仅含一次性 code 和公开 Gateway URL；
- manager 由 Gateway/申请页决定；
- AgentSystemId 不是 secret；
- SSE 使用 Gateway 签发的凭据；
- 日志不含 code/raw key/认证头；
- 外部写 binding 非法；
- 单地址宿主拒绝多地址；
- 多 Gateway 不交叉授权；
- 解绑不残留可路由的本地身份。

---

## 16. 一致性要求

| ID | 场景 | 判据 |
|---|---|---|
| C1 | 单地址宿主 | 第二地址 bind 拒绝 |
| C2 | B2 | 无 system，地址码兑换，binding/SSE 正确 |
| C3 | B1 懒系统 | 无 system 先系统对接；已有 system 忽略 system_code |
| C4 | 激活 prompt | 仅靠邮件 prompt 完成绑定；base/advanced 同流程 |
| C5 | AgentSystemId | 同机稳定；home/硬件变化生成新 ID |
| C6 | 单 Gateway 聚合 | 只聚合该 AgentSystemId 激活的地址；一条 SSE |
| C7 | 多 Gateway | 每 Gateway 一条 SSE，不混 pending/ack |
| C8 | 混合 B1/B2 | 同一 AgentSystemId 正确路由 |
| C9 | Session 路由 | 同 thread followup，新 thread 新 Session，空间=cwd |
| C10 | ack | 成功投递后才 ack；miss/paused 不 ack |
| C11 | 重启 | 已有 binding 启动即 arm；无 binding 0 SSE |
| C12 | 安全 | 无 raw key/code 日志；外部预写不算激活 |
| C13 | 解绑 | 停路由/聚合/pointer；不误删其他地址 |
| C14 | 迁移 | 同系统 move 保持地址；跨系统重新激活 |

---

## 17. 宿主适配附录

### 17.1 DSH

- 多地址；workspace；agents Session；
- 一条 SSE per (AgentSystemId,Gateway)；
- 需要按本文实现 AgentSystemId 聚合授权。

### 17.2 OpenClaw

- 多地址；agentId 空间；
- thread 连续需 `hooks.allowRequestSessionKey=true` + 前缀白名单；
- 未开启时 workspace 路由可用，thread 不连续；
- 插件必须声明真实工具名，工厂注册需 `names` 元数据；
- 一条 SSE per (AgentSystemId,Gateway)。

### 17.3 Pi

- 单地址；
- 无后台 createSession/deliverToSession；
- 地址绑定当前 cwd；
- 不提供系统级工具；
- 一条 SSE per (AgentSystemId,Gateway)。

---

## 18. 参考实现状态

本文是契约，不是完成声明：

| 能力 | DSH | OpenClaw | Pi |
|---|---|---|---|
| 多地址 | 已实现 | 条件实现 | 不支持 |
| 空间路由 | 已实现 | 条件实现 | 单地址 |
| AgentSystemId | 待实现 | 待实现 | 待实现 |
| 多 Gateway SSE | 待实现 | 待实现 | 待实现 |
| B1 懒系统对接 | 待实现 | 待实现 | 不适用 |
| 唯一 `aimail_address` | 待统一 | 待统一 | 已单地址 |
| 解绑/迁移 | 部分实现 | 部分实现 | 部分实现 |

未实现项不得在验收中声明完成。

---

## Appendix A — activation prompt

```text
请调用 aimail_address 完成地址激活：
action="bind"
email="alice@example.com"
code="addr-..."
system_code="sys-..."    # 仅 B1
gateway_url="https://gateway.example.com"
```

## Appendix B — 用户可见概念

1. 我的邮件地址；
2. 申请页/邮件给的地址激活 prompt。

域、系统、manager、B1/B2、base/advanced 均由 Gateway 生成。
