# Hermes AIMail 单 SSE + Profile 路由 + 回复会话追溯方案

状态：**方案调研，不落地**  
范围：Hermes multiplex gateway、AIMail pysdk/plugin、V2 单 AgentSystemId SSE  
不修改：`tests/host-acceptance/**`、当前 V2 QA 流程、`agent-aimail 0.5.0` 代码

---

## 1. 需求

目标模型：

```text
一个远端 AIMail Gateway
  └── 一个 AgentSystemId
        └── 一条 SSE

Hermes 一个 gateway 进程
  ├── profile A
  ├── profile B
  └── ...
```

要求：

1. 仍然只保留一条 SSE；
2. 入站邮件按绑定地址路由到对应 profile；
3. **新邮件**创建新 session；
4. **回复邮件**必须追溯到原 session，不能每次入站都新建；
5. 回复匹配要覆盖：
   - 回复 Agent 发出的邮件；
   - 回复用户最初发来的邮件；
   - 邮件客户端只带 `In-Reply-To` / `References` 的情况；
6. 未成功进入目标 session 时不得 ack；
7. 不依赖 AIMail CLI，不依赖 per-profile webhook 端口。

### 1.1 绑定粒度：one profile = one address

Hermes 采用：

```text
profile = AIMail 绑定单元 = DSH workspace 的等价空间
```

因此：

- 地址绑定在 **profile 层**，不再往下拆 DSH workspace；
- 当前产品模型是 **一个 profile 一个 AIMail 地址**；
- 一个 profile 对一个 Gateway 一条 SSE；
- 因为一个 profile 只有一个地址，所以实际呈现为“一个地址一条 SSE”；
- 这是 profile=workspace 模型的自然结果，不是 V2 契约强制“按地址建 SSE”。

Hermes profile 内部确实有 `workspace/` 目录（文件/工具工作目录），但它不是第二个 AIMail 绑定层级。AIMail 的绑定对象是 profile，不是 profile 内的 workspace 目录。

V2 的 AgentSystemId 技术上可以聚合多个地址，但 Hermes 当前模型不需要、也不启用 profile 内多地址聚合。

---

## 2. 核心结论

### 2.1 单 SSE 在 AIMail 协议侧可行

V2 的 SSE 单位是：

```text
(AgentSystemId, Gateway)
```

不是：

```text
(address, Gateway)
```

所以单 SSE 模型下，一个 AgentSystemId 可以覆盖多个地址；地址再按“一地址一 profile”路由到不同 profile。  
AIMail Gateway 不需要知道 Hermes profile。

但这不是 Hermes 的 profile 内多地址聚合：每个 profile 仍然只绑定一个地址。

### 2.2 真正的难点是 Hermes 的“新 session vs 原 session”

Hermes 的 session 是按确定性 key 复用的：

```text
agent:<profile>:<platform>:<chat_type>:<chat_id>[:<thread_id>]
```

来自 [Gateway Session Lifecycle](https://hermes-agent.nousresearch.com/docs/developer-guide/gateway-session-lifecycle)：

- `SessionSource.chat_id` 是平台会话标识；
- `build_session_key()` 由 platform/chat_type/chat_id 等生成；
- `get_or_create_session()`：
  - key 已存在 ⇒ 返回原 session；
  - key 不存在 ⇒ 创建新 session；
- `/new`、`/reset`、suspend 是显式边界；
- 空闲时间不会自动换 session。

因此：

- **新邮件** → 新 `chat_id` → 新 session；
- **回复邮件** → 相同 `chat_id` → 原 session；
- 不需要每次调用 `ctx.inject_message`，也不需要在 SDK 里自己维护 session 生命周期；
- 关键是**把邮件 thread 稳定映射到 chat_id**。

### 2.3 推荐路径

```text
SSE
  └── Host Router（唯一 SSE 所有者）
        ├── address -> profile 路由
        ├── profile endpoint（Unix socket）
        │     └── AIMailAdapter.handle_message(MessageEvent)
        │           └── gateway get_or_create_session(chat_id)
        └── ack after endpoint accepted
```

使用 Hermes platform adapter 的 `handle_message`，而不是 `ctx.inject_message`：

- 新 `chat_id` 会创建新 session；
- 相同 `chat_id` 会复用原 session；
- 由 gateway 管理 session 生命周期；
- 不需要 router 直接碰 `SessionAuthority`；
- 不依赖 host 源码 patch。

---

## 3. 身份与键设计

### 3.1 路由维度

每条入站 delivery 至少需要：

```text
agent_system_id
gateway_system_id
recipient_address
profile_id
thread_root
hermes_chat_id
```

其中：

- `agent_system_id`：Host 级 AIMail 身份（单 SSE 方案下全 profile 共用）；
- `gateway_system_id`：远端 AIMail Gateway 身份；
- `recipient_address`：决定 profile；
- `profile_id`：Hermes profile；
- `thread_root`：邮件线程根；
- `hermes_chat_id`：传给 Hermes platform adapter 的稳定 chat_id。

### 3.2 chat_id 设计

推荐：

```text
chat_id = "aimail:" + sha256(
    profile_id
    + gateway_system_id
    + recipient_address
    + thread_root
)[:32]
```

加 profile/address 的目的：

- 同一 thread 在不同 profile/address 下不能误复用；
- 避免跨 profile 上下文泄漏；
- chat_id 不含原始邮件地址，降低日志/PII 暴露。

`chat_type` 固定为 `dm`；若未来支持邮件组，可改为 `group` 并加 participant 策略。

### 3.3 session_key 结果

Hermes 会生成：

```text
agent:<profile>:aimail:dm:<chat_id>
```

同一 thread 的回复必须得到同一 chat_id，从而得到同一 session_key。

---

## 4. 邮件 thread 识别

### 4.1 解析

从 RFC 5322 头解析：

```text
Message-ID
In-Reply-To
References
Subject
From
To
Cc
Date
```

规则：

1. 统一去掉 `< >`、首尾空白；
2. 保留原始大小写用于展示，比较时用规范化值；
3. `References` 按从旧到新排列；
4. 依次检查：
   - `In-Reply-To` 中的每个 ID；
   - `References` 从新到旧；
   - 当前 `Message-ID`。

### 4.2 已知 ID 到 thread 的别名表

需要持久化：

```text
message_id_alias
  message_id
  thread_root
  direction        inbound | outbound
  profile_id
  gateway_system_id
  address
  created_at
```

必须记录：

- 入站原始邮件的 `Message-ID`；
- Agent 通过 `send_mail` 发出的每封回复的 `Message-ID`；
- 如果 Gateway 会生成新的 `Message-ID`，记录 Gateway 实际返回的值；
- 邮件线程根 ID。

### 4.3 回复判定

```text
if In-Reply-To or References matches an alias:
    -> reply
    -> existing session
else if Message-ID already known:
    -> duplicate
    -> idempotent no-op
else:
    -> new thread
    -> new session
```

### 4.4 Subject 兜底

默认**不做** subject-only 匹配。

原因：

- 同一客户可能重复使用 `Re: invoice`；
- 同一 subject 可能对应不同 thread；
- 误合并会造成上下文串线。

可选保守兜底（默认关闭）：

```text
subject 规范化相同
AND 同一 profile/address
AND 同一 sender
AND 最近 N 天内唯一匹配
AND 没有 References/In-Reply-To
```

即使开启，也只作为最后手段，并记录 `matched_by=subject_fallback`。

### 4.5 未知引用

如果 `In-Reply-To`/`References` 存在但别名表完全不认识：

- 默认按**新 thread** 处理；
- 记录 `orphan_reference`；
- 不从 subject 猜原 session；
- 可把未知引用作为新 thread 的 parent 元数据，但不复用旧 session。

这比错误复用安全。

---

## 5. 新邮件 vs 回复邮件的执行差异

| 场景 | chat_id | session 行为 | ack 条件 |
|---|---|---|---|
| 新邮件，无引用 | 新 thread hash | 新 session | adapter 接受新 session 后 |
| 回复 Agent 邮件 | 原 thread hash | 复用原 session | 原 session 接受（或入队）后 |
| 回复用户首封邮件 | 原 thread hash | 复用原 session | 同上 |
| 重复 delivery | 原 thread hash | 幂等，不新建 | 幂等成功后 |
| 未知引用 | 新 thread hash | 新 session | 新 session 接受后 |
| subject 兜底命中 | 原 thread hash | 仅显式开启时复用 | 需标记风险 |

### 5.1 新邮件

流程：

1. 计算 `thread_root = Message-ID`；
2. 生成新 chat_id；
3. profile endpoint 调 `handle_message`；
4. gateway 创建新 session；
5. 写 thread→chat_id 映射；
6. ack。

### 5.2 回复邮件

流程：

1. 解析引用；
2. 命中别名 → 得到原 thread_root；
3. 计算原 chat_id；
4. profile endpoint 调 `handle_message`，**使用同一 chat_id**；
5. gateway `get_or_create_session` 返回原 session；
6. 写 last_seen / alias；
7. ack。

### 5.3 关键点

回复路径**不要**调用：

```text
ctx.inject_message(...)
```

作为主路径，因为它：

- 面向已有 live session；
- 需要 `allow_gateway_injection`；
- 不能可靠创建新 thread；
- 受 profile home 限制。

应统一走：

```text
AIMailAdapter.handle_message(
    MessageEvent(
        source=SessionSource(
            platform=aimail,
            chat_id=<thread chat_id>,
            chat_type="dm",
            message_id=<mail message id>,
            ...
        ),
        ...
    )
)
```

由 gateway 自己决定复用还是新建。

---

## 6. Agent 回复的 Message-ID 闭环

如果 Agent 的回复不带 `In-Reply-To` / `References`，外部用户回复时就不会形成同一 thread，回复追 session 会失败。

因此 `send_mail` 需要：

1. 默认带：
   - `In-Reply-To: <当前入站邮件 Message-ID>`
   - `References: <线程 References + 当前入站 Message-ID>`
2. 记录 Gateway 返回的实际 outbound `Message-ID`；
3. 写入 alias：
   - `outbound_message_id -> thread_root`
4. 如果邮件客户端回复的是 outbound 邮件，`In-Reply-To` 命中 outbound alias → 回溯到原 thread。

这是回复追溯的**必要条件**。

建议的工具上下文：

```text
当前入站 mail:
  message_id
  references[]
  thread_root
  profile_id
  address

send_mail:
  in_reply_to (自动填充，可覆盖)
  references (自动填充，可覆盖)
  -> 返回 outbound message_id
```

如果现有 `send_mail` 不返回 Message-ID，需要在 Gateway 响应中补；这是协议/SDK 改造点，不是 UI 层。

---

## 7. Host Router 设计

### 7.1 组件

```text
AIMail Host Router（默认 profile / host service）
  - 唯一 SSE
  - address -> profile 路由表
  - thread/alias 数据库
  - profile endpoint 客户端

Profile Endpoint（每个 profile 一个）
  - 注册 profile/address
  - 收到 Router 的 deliver(existing|new)
  - 在自己的 profile 调用 AIMailAdapter.handle_message
  - 返回 accepted / queued / rejected
```

### 7.2 路由表

来源：

- 每个 profile 的 binding；
- `(GatewaySystemId, address) -> profile_id`；
- 激活/解绑时更新；
- 禁止一个地址同时绑定两个 profile。

### 7.3 单 SSE 所有权

- 只有 Router 持有 SSE；
- profile endpoint 不建立 SSE；
- Router 断线重连只发生在一处；
- 避免 Hermes gateway 进程内 N 个 SSE 重复拉取。

### 7.4 进程间通道

推荐 Unix domain socket：

```text
<hermes_run_dir>/aimail-router.sock
```

权限：

```text
0600
```

不开放 TCP 端口，符合 multiplex 对 port-binding platform 的限制。

如果 Hermes 后续提供正式的 host-level profile dispatch API，则用它替代 socket。

### 7.5 ack

Router 收到 endpoint 结果：

```text
accepted   -> ack
queued     -> ack（已进入 session FIFO）
rejected   -> 不 ack
no endpoint-> 不 ack
```

任何情况下：

- 未进入目标 profile 的 session/FIFO；
- 不得 ack；
- 保持 AIMail pending，等待重投。

---

## 8. Session 追溯状态机

### 8.1 状态

```text
thread:
  thread_root
  profile_id
  gateway_system_id
  address
  chat_id
  session_key
  state: active | suspended | reset | orphan
  last_seen
```

### 8.2 正常回复

```text
reply -> alias hit -> thread active -> same chat_id -> existing session
```

### 8.3 原 session 已重置

Hermes 的 `/new`、`/reset`、suspend 会让同一 chat_id 下产生新的 session incarnation。

产品策略必须明确：

- 方案 A：回复仍进同一 chat 的新 session（用户显式 reset 表示要新上下文）；
- 方案 B：回复按 thread 恢复旧 transcript（需要额外 session 固定/恢复，不建议默认）。

推荐 A：

- 尊重 Hermes 的显式 session 边界；
- 不偷偷绕过 `/new`；
- 在映射中记录 `session_generation`，便于审计。

### 8.4 原 profile 不存在

- 不路由到其他 profile；
- 保持 pending 或转入管理员告警；
- 不得 fallback 到 default profile。

### 8.5 同一 thread 换了收件地址

- 如果新收件地址属于另一个 profile：
  - 不跨 profile 复用原 session；
  - 在该 profile 新建 thread/session；
  - 记录 `cross_profile_thread`，供人工审计。
- 如果仍属同一 profile：
  - 可以复用原 chat_id（thread 维度优先）。

---

## 9. 幂等与崩溃恢复

### 9.1 幂等键

```text
delivery_id
```

或：

```text
(gateway_system_id, mailbox_message_id)
```

重复投递：

- 已 ack：直接忽略；
- 已进入 session：不再创建；
- 处理中：不要并发创建第二个 session。

### 9.2 创建 session 与写映射的顺序

推荐：

1. 先计算确定性 chat_id；
2. 调 `handle_message`；
3. gateway 接受；
4. 写 thread 映射；
5. ack。

风险窗口：

- 第 3 步成功、第 4 步失败 → 重投时同 chat_id 会复用同一 session，不会重复；
- 因为 chat_id 是确定性的，不依赖映射先落库。

### 9.3 回复别名写入

- 入站 ID 可在收到时立即写；
- outbound ID 在 `send_mail` 成功响应后写；
- 如果 outbound 写失败：
  - 记录告警；
  - 后续回复可能按新 thread 处理；
  - 不应伪造 ID。

---

## 10. 为什么这是“单 SSE + 多 profile”的最小可行模型

- 一个 SSE：Router；
- 多 profile：endpoint；
- 新/旧 session：chat_id 决定；
- 回复追溯：thread_root + alias；
- ack：endpoint 接受后；
- 不依赖 CLI；
- 不依赖 per-profile webhook；
- 不依赖 Hermes 内部 SessionAuthority；
- 不要求 `ctx.inject_message` 跨 profile。

它仍然需要两个宿主条件：

1. Hermes plugin 能在 default profile 建 Unix socket 服务；
2. 每个 profile 的 plugin 能连接并调用自己的 platform adapter。

如果这两个条件不成立，就只能退回：

- profile-scoped SSE（每 profile 一条），或
- 等 Hermes 提供 host-level `dispatch_to_profile`。

---

## 11. 测试设计（未来实施时）

### 11.1 新邮件

- 新 Message-ID，无引用；
- 期望新 session；
- 旧 thread 不受影响。

### 11.2 回复入站邮件

- 外部客户端 `In-Reply-To: <inbound-msg-id>`；
- 期望复用原 session；
- 不创建新 chat/session。

### 11.3 回复 Agent 出站邮件

- Agent `send_mail` 返回 outbound message-id；
- 外部回复 `In-Reply-To: <outbound-msg-id>`；
- 期望回溯原 session。

### 11.4 References 链

- `References` 只含根 ID；
- `References` 含根 + 中间 ID；
- 期望命中同一 thread。

### 11.5 两个相似 thread

- 同 sender、同 subject、不同 Message-ID；
- 无引用；
- 期望两个 session，不合并。

### 11.6 未知引用

- `In-Reply-To` 指向未知 ID；
- 期望新 thread，不错误复用。

### 11.7 跨 profile

- 同一 thread 的回复到达另一个 profile 的地址；
- 期望不跨 profile，记录异常或新 session。

### 11.8 崩溃/重启

- Router 重启后 alias/thread 映射仍可读；
- 同 chat_id 恢复原 session；
- pending 重投不重复。

### 11.9 ack

- endpoint rejected → pending 保留；
- endpoint accepted → ack；
- profile 不在线 → pending，不 ack。

### 11.10 单 SSE

- 多 profile 只建立一条 SSE；
- 每个地址路由到正确 profile；
- 无第二个 SSE。

---

## 12. 开放问题

1. Hermes `handle_message` 是否返回已创建/复用的 session 信息（用于审计）？
2. 同一 `chat_id` 在 session 被 `/new`/suspend 后，邮件回复应重新进入新 session 还是恢复旧 transcript？（建议尊重显式边界，进新 session）
3. `send_mail` 当前是否返回 Gateway 的 outbound `Message-ID`？若否，需要补协议字段。
4. Hermes platform adapter 是否支持 `chat_type=dm` 下纯 thread hash 作为 chat_id？应按 `SessionSource` 语义验证。
5. Router 在 default profile 停止/禁用时，单 SSE 是 fail-closed 还是迁移到另一个 profile？建议 fail-closed。
6. profile 删除后，待处理 pending 是否需要管理员导出/重放？
7. Unix socket endpoint 在 Hermes 插件热重载时的注册/注销时序。

---

## 13. 结论

- 单 SSE 可以在 AIMail 协议层实现；
- 回复追溯不应依赖“每次新 session”，而应依赖**稳定 chat_id + thread alias**；
- 新邮件/回复邮件的差异应由 `thread_root` 和 `In-Reply-To`/`References` 决定；
- 最合适的执行入口是 Hermes platform adapter 的 `handle_message`，不是 `ctx.inject_message`；
- Host Router + per-profile endpoint 是当前公共 API 下可行的单 SSE 方案；
- 如果不想引入 Unix socket/host router，则 profile-scoped SSE 是更简单、更安全的 MVP。

本文档仅为调研方案，未修改任何代码、测试或当前 V2 QA 流程。
