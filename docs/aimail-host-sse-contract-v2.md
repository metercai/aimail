# AIMail Host SSE Contract

状态：现行契约  
读者：Agent 宿主实现者、Gateway 实现者、QA  
规范词：MUST / SHOULD / MAY 按 RFC 2119 解释

本文是一份完整、独立的契约，覆盖安装、地址激活、SSE、路由、解绑迁移、
数据布局、安全与验收。读者不需要其他文档即可实现。

---

## 1. 核心模型

AIMail 只有三个核心概念：

```text
Gateway
  └── GatewaySystemId        # Gateway 在地址激活时返回的系统归属
        └── Address          # 邮件地址
              └── Agent 空间  # 多地址宿主：workspace / agentId
                    └── Session
```

- **地址激活只有一个概念**：用地址激活码完成地址绑定；
- Gateway 侧属于哪个系统/域，由激活码在申请时确定；
- 本地不执行独立的“系统对接”，不创建 system 管理配置；
- 地址激活返回的 `GatewaySystemId` 是稳定分组键；
- 本地还有一个 `AgentSystemId`，是客户端实例身份，用于 SSE 聚合。

两个 ID 不可混用：

| ID | 归属 | 作用 |
|---|---|---|
| `GatewaySystemId` | Gateway | 地址归属、域/manager/审计、`.aimail` 目录分组 |
| `AgentSystemId` | 本地 Agent 实例 | SSE 聚合身份、客户端实例识别 |

---

## 2. 用户安装流程

用户只需要理解两个东西：

1. 我的邮件地址；
2. Gateway 申请页/邮件给我的**地址激活 prompt**。

流程：

```text
/apply 页面
  → 用户填地址、管理者邮箱、域/系统信息
  → Gateway 确定地址归属的 GatewaySystemId
  → 生成地址激活码
  → 邮件下发 activation prompt
  → 用户粘贴到 Agent 对话框
  → Agent 调用 aimail_address
  → Gateway 返回 GatewaySystemId + 地址凭据
  → 本地写 binding
  → 建立/更新 SSE
```

用户不需要选择“系统模式”，也不需要在本地再做一次系统对接。共享域、独享域、
系统名、manager、base/advanced，全部由 Gateway 在申请时确定并随激活码走。

---

## 3. 地址激活

### 3.1 唯一工具

宿主 MUST 只暴露 `aimail_address`：

```ts
{
  action: 'bind' | 'pause' | 'resume' | 'unbind' | 'move'
  email: string
  code?: string
  gateway_url?: string
  workspace_path?: string   // move 时使用
}
```

- `bind` 必须有 `email` 和 `code`；
- `gateway_url` 通常来自 activation prompt；
- 不接受 `system_code`、`manager`、`system_name`、`domain` 等用户参数；
- 不接受 admin/domain/system raw key；
- 不提供独立 `aimail_system` 工具。

### 3.2 激活请求

`aimail_address bind`：

1. 用 `code` 向 Gateway 兑换；
2. Gateway 校验码、地址、归属、有效期；
3. Gateway 返回：
   - `GatewaySystemId`
   - 地址
   - 地址凭据
   - manager（如适用）
   - 域/系统元数据（如适用）
4. 本地写 binding；
5. 注册/更新 AgentSystemId；
6. 建立或复用该 Gateway 的 SSE；
7. 返回绑定结果。

### 3.3 失败

以下情况不写 binding、不 arm、不 ack：

- code 无效/过期/已使用；
- code 与 email 不匹配；
- Gateway 未返回 `GatewaySystemId`；
- 凭据缺失；
- 本机无法生成 AgentSystemId；
- binding 已存在且冲突。

---

## 4. GatewaySystemId

`GatewaySystemId` 由 Gateway 在地址激活时返回，是地址的服务端归属。

### 4.1 作用

- 标识地址属于哪个 Gateway 系统/域；
- 作为 `.aimail` 本地目录的稳定分组键；
- 用于 Gateway 侧审计、manager、域策略；
- 用于多地址归属校验。

### 4.2 不作用

- 不是 SSE 身份；
- 不是本地路由键；
- 不是 Session 路由键；
- 不要求本地有 system 管理配置；
- 不要求本地保存 admin/domain/raw key。

### 4.3 稳定性

- 同一地址重新激活，GatewaySystemId 应保持稳定；
- 地址迁移到另一个系统/域时，GatewaySystemId 可以变化；
- 变化时 binding 必须更新，目录按新分组迁移；
- GatewaySystemId 比 AgentSystemId 更稳定：硬件/agent home 变化不会改变它。

---

## 5. AgentSystemId

### 5.1 定义

```text
AgentSystemId = stable_hash(agent_home_directory, machine_hardware_fingerprint)
```

要求：

- 同机同 home 稳定、可重复生成；
- 不同 Agent 实例/机器全局唯一（碰撞可忽略）；
- 不含 secret；
- 不是 GatewaySystemId；
- 每个 Gateway 分别注册/授权。

### 5.2 作用

- 标识本地 Agent 系统实例；
- Gateway 按 AgentSystemId 授权聚合它激活的所有地址；
- SSE 连接单位为 `(AgentSystemId, Gateway)`。

### 5.3 变化

agent home 或硬件指纹变化 ⇒ 新 AgentSystemId：

- 旧 binding 不能静默复用；
- 需要重新激活/重新授权；
- 目录仍可按 GatewaySystemId 分组，但 binding 的 `agent_system_id` 必须更新。

---

## 6. SSE 拓扑

### 6.1 连接单位

```text
(AgentSystemId, Gateway) → 一条 SSE
```

不是：

- 每个地址一条；
- 每个 GatewaySystemId 一条；
- 每个 workspace 一条；
- 每个 Session 一条。

### 6.2 多 Gateway

一个 Agent 系统可对接多个 Gateway：

```text
AgentSystemId
  ├── Gateway A → SSE A
  ├── Gateway B → SSE B
  └── Gateway C → SSE C
```

每个 Gateway 一条独立 SSE；不同 Gateway 的 pending/ack 不混用。

### 6.3 Gateway 聚合授权

Gateway MUST：

- 认证 AgentSystemId；
- 只授权它聚合**由该 AgentSystemId 激活/绑定的地址**；
- 不投递该 Gateway 下其他 AgentSystemId 的地址；
- 地址新增/暂停/恢复/解绑后更新集合；
- 按 GatewaySystemId 做服务端归属校验。

### 6.4 本地路由

```text
delivery.gateway_url + recipient
  → binding lookup
  → Agent 空间
  → thread root
  → 已有 Session: followup
  → 无 Session: create（cwd=workspace_path）
  → 成功投递后 ack
```

无 binding、paused、空间 miss、Session 创建失败：

- 不 ack；
- 留 pending；
- 记录原因。

---

## 7. Binding 与数据布局

根目录默认 `~/.aimail`，可配置。

```text
<aimail_home>/
└── systems/
    └── <GatewaySystemId>/
        └── <clean_addr>/
            ├── address binding
            ├── address ledger
            └── mail/
                ├── YYYYMM/in-*.json
                ├── YYYYMM/out-*.json
                ├── YYYYMM/attch/<mid>/
                ├── meta/<first2>/<mid>.json
                ├── threads/<first2>/<tid>.json
                └── .search/index.db
```

- 以 `GatewaySystemId` 分组，而不是 AgentSystemId；
- 同一 GatewaySystemId 下可以有多个地址；
- 同一 Gateway 下可以有多个 GatewaySystemId；
- 共享/独立地址也由 Gateway 返回一个 GatewaySystemId，因此同样落组；
- 不要求 `systems/<id>/` 下有 system 管理配置；目录分组不等于本地系统对接。

### 7.1 Binding 必填

| 字段 | 含义 |
|---|---|
| `gateway_url` | 所属 Gateway |
| `email` | 地址 |
| `system_id` | 激活返回的 GatewaySystemId |
| `agent_system_id` | 本机 AgentSystemId |
| `workspace_id` / `workspace_path` | 多地址宿主必填；单地址为当前空间 |
| 凭据 | Gateway 签发的地址凭据 |
| `manager_address` | 安全员（如 Gateway 返回） |
| `paused` | 本地暂停 |

禁止：

- Session id；
- 一次性 code；
- admin/domain/system raw key；
- 用 workspace 之外的地址身份做路由。

### 7.2 权限

- 目录 0700；
- binding/pointer 0600；
- 邮件/附件 0600/0700；
- 日志不输出密钥。

---

## 8. SSE 投递契约

### 8.1 连接

- 每个 `(AgentSystemId, Gateway)` 一条；
- 认证使用 Gateway 签发的 AgentSystemId 凭据；
- 同 Gateway 下所有 active 地址共享；
- 多 Gateway 不合并。

### 8.2 重连

- 心跳/长连接；
- 指数退避重连；
- 重连后重新认证并恢复聚合集合；
- 不重复创建 Session/不重复 ack；
- 409/占用按 Gateway 规则退避。

### 8.3 Ack

- 只有成功接受进 Session 后才 ack；
- binding miss/paused/workspace miss/Session 创建失败：不 ack；
- at-least-once，按 message/thread 幂等；
- ack 只发给对应 Gateway。

### 8.4 状态

```json
{
  "mode": "sse",
  "agentSystemId": "...",
  "gateways": [
    {"gatewayUrl":"...", "connected":true, "lastEventAt":"...", "lastError":""}
  ]
}
```

状态不得包含 key/code/正文。

---

## 9. 入站路由与 Session

1. `gateway_url + recipient` 查找 binding；
2. 无 binding/paused：不 ack；
3. 解析 binding 的 workspace；
4. thread root：`references[0]` → `in_reply_to` → `message_id`；
5. 读本地 thread meta 的 Session；
6. 同 workspace 且 Session 可用：followup；
7. 否则新建 Session，cwd=workspace_path；
8. 投递成功写 meta 并 ack；
9. Session 可弃用；binding 不保存 Session；
10. 多 Gateway 的 thread meta 不跨 Gateway 混用。

---

## 10. Welcome / manager

- Welcome 是普通 inbound，走 pending/SSE；
- 不需要 listener/webhook；
- welcome 正文的 persona/signature/current_time 与 manager 指令保持语义；
- manager cc/回复是普通 inbound；
- 重放按 message id 去重；
- 无 binding 不自动回复、不 ack；
- manager 由 Gateway 返回/申请时确定，本地不手工指定。

---

## 11. 单地址与多地址宿主

### 11.1 多地址

MUST 具备：

- 稳定 Agent 空间；
- 多 Session 创建/调用；
- 地址绑定空间；
- thread→Session 路由。

### 11.2 单地址

没有稳定空间，或不能主动创建/调用目标 Session：

- 整个实例最多一个 active 地址；
- 入站进入当前 Session；
- 拒绝第二个地址；
- 无系统级工具。

### 11.3 矩阵

| 能力 | 多地址 | 单地址 |
|---|---|---|
| 多地址绑定 | 允许 | 禁止 |
| 地址→空间 | 必须 | 固定当前空间 |
| 新 thread 新 Session | 必须 | 不支持 |
| 回复回原 Session | 必须 | 当前 Session |
| AgentSystemId | 必须 | 必须 |
| 每 Gateway 一条 SSE | 必须 | 必须 |

---

## 12. 安装与混合态

状态轴：地址 0/1/多、GatewaySystemId 单/多、Gateway 单/多、active/paused/失败、单/多地址宿主。

| 状态 | 说明 |
|---|---|
| S0 | 空系统，无 SSE |
| S1 | 单地址 |
| S2 | 同一 GatewaySystemId 多地址 |
| S3 | 同一 Gateway 多 GatewaySystemId |
| S4 | 多 Gateway |
| S5 | 混合地址/系统 |
| S6 | 部分失败（某地址/某 Gateway） |
| S7 | 重启恢复 |
| S8 | pause/resume |
| S9 | unbind/move |
| S10 | AgentSystemId 变化 |

要求：

- 已完成项不能被未完成项阻塞；
- 单地址宿主拒绝第二个地址；
- 多地址宿主不能混空间；
- 多 Gateway 不混 pending/ack；
- 外部预写 binding 不是激活；
- 任何状态都能启动，未就绪项可诊断。

---

## 13. 解绑与迁移

### 13.1 pause

- 标 paused；
- 从聚合集合移除；
- 不 ack；
- resume 后重新纳入并 drain。

### 13.2 unbind

- 停止该地址路由；
- 从聚合集合移除；若 Gateway 无其他地址，停止该 Gateway SSE；
- 归档 binding/本地凭据；
- 仅当 pointer 指向该地址时清除 pointer；
- Gateway 远端凭据不自动删除；远端 deregister 是独立操作；
- pending 不 ack，保持可诊断。

### 13.3 move：同 AgentSystemId 换空间

- 更新 `workspace_id/path`；
- 不兑换新 code；
- 不新建 SSE；
- 后续邮件路由新空间；
- 已投递 Session 不迁移；
- 新空间必须存在且属于当前 Agent 系统。

### 13.4 move：换 GatewaySystemId / Gateway

- 地址归属变化，需重新激活；
- 旧 binding unbind；
- 新 binding 使用新 code；
- 目录按新 GatewaySystemId 迁移；
- 不同 Gateway 不共享 pending/ack/session meta。

### 13.5 跨 AgentSystemId / 跨机器

- 旧实例 unbind；
- 新实例生成新 AgentSystemId；
- 地址重新激活/重新签发凭据；
- 不能复制旧 binding 直接使用。

### 13.6 home/硬件变化

- 新 AgentSystemId；
- 旧 binding 失效；
- 重新激活；
- 不静默复用旧 ID。

---

## 14. 工具面

一个逻辑 toolset 包含：

- 邮件工具；
- board/A2A 工具；
- `aimail_address`。

不包含：

- 独立 `aimail_system`；
- 系统激活码专用工具；
- 接受 raw key 的工具。

---

## 15. 安全

- activation prompt 仅含一次性 code 和公开 Gateway URL；
- manager 由 Gateway/申请页决定；
- AgentSystemId 不是 secret；
- SSE 使用 Gateway 签发凭据；
- 日志不含 code/key/认证头；
- 外部写 binding 非法；
- 单地址宿主拒绝多地址；
- 多 Gateway 不交叉授权；
- unbind 不残留可路由身份。

---

## 16. 一致性要求

| ID | 场景 | 判据 |
|---|---|---|
| C1 | 单地址宿主 | 第二地址拒绝 |
| C2 | 地址激活 | 仅靠 address code 完成绑定；返回 GatewaySystemId |
| C3 | 目录分组 | `.aimail/systems/<GatewaySystemId>/<address>` 正确 |
| C4 | AgentSystemId | 同机稳定；home/硬件变化生成新 ID |
| C5 | 单 Gateway 聚合 | 只聚合该 AgentSystemId 激活的地址；一条 SSE |
| C6 | 多 Gateway | 每 Gateway 一条 SSE，不混 pending/ack |
| C7 | 多地址/多系统 | 同 Gateway 下不同 GatewaySystemId 正确路由 |
| C8 | Session 路由 | 同 thread followup，新 thread 新 Session，空间=cwd |
| C9 | ack | 成功投递后才 ack；miss/paused 不 ack |
| C10 | 重启 | 已有 binding 启动即 arm；无 binding 0 SSE |
| C11 | 安全 | 无 raw key/code 日志；外部预写不算激活 |
| C12 | 解绑 | 停路由/聚合/pointer；不误删其他地址 |
| C13 | 迁移 | 同系统 move 保持地址；跨系统重新激活 |

---

## 17. 宿主适配

### 17.1 DSH

- 多地址；
- 空间=workspace；
- Session=agents；
- 每 `(AgentSystemId,Gateway)` 一条 SSE；
- 目录按 GatewaySystemId 分组。

### 17.2 OpenClaw

- 多地址；
- 空间=agentId；
- thread 连续需 `hooks.allowRequestSessionKey=true` + 前缀白名单；
- 未开启时空间路由可用，thread 不连续；
- 工具工厂注册需 `names` 元数据；
- 每 `(AgentSystemId,Gateway)` 一条 SSE。

### 17.3 Pi

- 单地址；
- 无后台 createSession/deliverToSession；
- 地址绑定当前 cwd；
- 每 `(AgentSystemId,Gateway)` 一条 SSE。

---

## 18. 参考实现状态

| 能力 | DSH | OpenClaw | Pi |
|---|---|---|---|
| 多地址 | 已实现 | 条件实现 | 不支持 |
| 空间路由 | 已实现 | 条件实现 | 单地址 |
| AgentSystemId | 待实现 | 待实现 | 待实现 |
| 多 Gateway SSE | 待实现 | 待实现 | 待实现 |
| 地址激活仅 code | 待统一 | 待统一 | 待统一 |
| 目录按 GatewaySystemId | 部分 | 部分 | 部分 |
| 解绑/迁移 | 部分 | 部分 | 部分 |

未实现项不得在验收中声明完成。

---

## Appendix A — 真实地址激活邮件与 prompt

以下文本来自 Gateway 当前的地址激活邮件模板
（`aimail-gateway/src/core/api/activation.rs` 的 address application email）。
花括号变量由 Gateway 发送时替换。

邮件标题：

```text
AIMail address activation code for {address}
```

邮件正文：

```text
{greeting}

An AIMail mailbox is ready for your agent:

  address: {address}

Please activate it within 24 hours.

If your agent does not have aimail_address, install or enable the AIMail plugin through your host's normal plugin/extension mechanism (not the aimail install CLI).

Copy the message below into your agent's conversation:

[AIMAIL-ACTIVATE]
My AIMail mailbox: {address}
Activation code: {code}
Safety officer: {manager}
Gateway: {gateway_url}
Use the AIMail address-activation skill; do not use system onboarding.

This code is sent only once - please keep this email.
Please do not reply to this email - it is sent automatically.

Best regards,
AIMail Team
```

用户复制的是从 `[AIMAIL-ACTIVATE]` 开始的整块内容。

流程：

1. 用户看到 prompt 中的安装提示；
2. 如果 `aimail_address` 不存在，先按宿主自己的插件/扩展机制安装或启用
   AIMail toolset（不使用 `aimail install` CLI）；
3. 确认 `aimail_address` 可用；
4. `[AIMAIL-ACTIVATE]` 触发地址激活 SKILL；
5. SKILL 从 prompt 提取 `address/code/gateway_url`，调用
   `aimail_address(bind)`；
6. 绑定成功后，SKILL 调用 `role_calibrator` 并完成身份校准。

SKILL 不得依赖 prompt 中的 URL 来加载；专属触发短语是：

```text
AIMAIL-ACTIVATE
```

## Appendix B — SKILL 索引

三个独立 SKILL，共享同一个 `agentmail` toolset，但触发条件和职责不同。 <!-- contract-allowed: toolset dependency name -->

| SKILL | 文件 | 触发 | 职责 |
|---|---|---|---|
| 地址激活/生命周期 | [`resources/skills/activation/SKILL.md`](../resources/skills/activation/SKILL.md) | `AIMAIL-ACTIVATE` | bind、identity calibration、pause/resume/unbind/move |
| 邮件收发 | [`resources/skills/SKILL.md`](../resources/skills/SKILL.md) | 邮件/收发意图 | 收发、回复、联系人、摘要、搜索 |
| board 协作（planned） | `resources/skills/board/SKILL.md` | board/A2A 意图 | Board 任务与角色协作 |

包内副本：

- `tssdk/packages/agent-aimail/resources/skills/activation/SKILL.md`
- `tssdk/packages/agent-aimail/resources/skills/SKILL.md`

### B.1 完整地址激活流程

```text
用户粘贴 activation prompt
  → 如果 aimail_address 不存在：按宿主机制安装/启用插件（一次）
  → 确认 aimail_address 可用
  → 加载 AIMAIL-ACTIVATE SKILL
  → 解析 address/code/gateway_url
  → aimail_address(action="bind")
      ├─ success → role_calibrator
      ├─ already_bound → role_calibrator
      ├─ tool unavailable → 安装/启用一次后重试一次
      └─ business failure → 4xx 不重试；5xx/超时重试一次
  → welcome mail（SSE）
  → 按 welcome 指示回复
  → 等待 [AIMail] Persona applied
  → 激活完成
```

### B.2 SKILL 生命周期

地址激活 SKILL 负责：

- `bind`：地址激活绑定；
- identity calibration：`role_calibrator` → welcome reply → persona applied；
- `pause` / `resume`；
- `unbind`：本地归档、停止聚合、按条件清 pointer；
- `move`：同 AgentSystemId 换 workspace；
- 错误分类与重试边界。

它不负责日常邮件内容，也不负责 board 任务。

### B.3 禁止

- 用 URL 直接加载 SKILL；
- 用 CLI/外部安装器完成地址激活；
- 写 binding 文件；
- 传 manager/raw key/system_code；
- 循环安装/激活；
- 未收到 `[AIMail] Persona applied` 就声称校准完成。

## Appendix C — 用户可见概念

1. 我的邮件地址；
2. 申请页/邮件给的地址激活 prompt。

系统/域/manager/base/advanced 均由 Gateway 生成，用户不感知。

## Appendix D — 实现备注

- 地址激活邮件已改为触发短语 `AIMAIL-ACTIVATE`，不再用 URL 加载 SKILL；
- activation SKILL 已提供；宿主安装器必须把它安装为独立 skill，不能覆盖
  邮件处理 SKILL；
- board SKILL 尚未发布，路径保留 planned；
- 旧 `docs/agent-self-setup.md` 不再作为 V2 激活入口；其安装内容已进入
  激活 prompt，工具参数与 identity calibration 已进入 activation SKILL。
