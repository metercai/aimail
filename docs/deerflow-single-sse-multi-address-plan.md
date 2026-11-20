# DeerFlow 单 SSE 多地址路由 V2 改造调研

状态：**调研方案，不落地**  
范围：DeerFlow pysdk adapter、DeerFlow Gateway、AIMail V2 SSE  
不修改：当前 V2 测试、QA 流程、`agent-aimail 0.5.0` 代码

---

## 1. 结论

**DeerFlow 比 Hermes 更适合改造成“单 SSE + 多地址路由”的 V2 模型。**

原因：

- DeerFlow Gateway 是一个 FastAPI 进程；
- 同一进程管理多个 named agent；
- thread 是持久化 session，并可独立创建/恢复；
- 每个 thread 自带隔离 workspace；
- `start_run` 可直接指定 `assistant_id` 和 `thread_id`；
- 没有 Hermes 那种 profile context / session authority 的跨 profile 隔离障碍。

所以：

```text
一个 AIMail SSE
  -> 一个 DeerFlow Gateway 进程
  -> recipient address -> assistant
  -> email thread -> thread_id
  -> start_run()
```

在 DeerFlow 侧是自然的，不需要 Hermes host router 那样的跨 profile 注入。

但当前 DeerFlow adapter **还不是** V2 模型：

- 入站仍是 webhook/bridge push；
- thread 固定按地址生成，不是按邮件 thread；
- 没有 AgentSystemId；
- `set_agent_context` 使用进程全局配置，多地址并发有串线风险；
- 没有单 SSE 生命周期。

---

## 2. DeerFlow 当前模型

### 2.1 Gateway / Agent / Thread

依据：

- [DeerFlow App 架构](https://deerflow.tech/en/docs/application)
- [Agents and Threads](https://deerflow.tech/en/docs/application/agents-and-threads)

当前结构：

```text
DeerFlow Gateway（FastAPI, 一个进程）
  ├── custom agent A
  │     └── thread A1 / thread A2 ...
  ├── custom agent B
  │     └── thread B1 / thread B2 ...
  └── default agent
```

关键事实：

- thread 是持久化 conversation session；
- thread 有独立 workspace；
- 每个 thread 有 message history、artifacts、agent reference；
- 支持创建、checkpoint、resume；
- 一个 Gateway 可管理多个 agent。
- Gateway 是 `state.db`/thread 状态的 owner（DeerFlow 侧）。

这和 Hermes 的 profile 隔离不同：

```text
Hermes: profile -> session authority
DeerFlow: Gateway -> agent + thread
```

DeerFlow 没有“每 profile 一个进程/authority”的墙，因此单 SSE 路由到多个 agent/thread 更容易。

### 2.2 当前 AIMail DeerFlow 入站

当前代码：

- `pysdk/deer-flow/aimail_inbound.py`
- `pysdk/deer-flow/aimail_deerflow.py`

链：

```text
AIMail Gateway
  -> bridge
  -> POST /aimail/inbound
  -> HMAC 校验
  -> 共享 process_inbound_mail
  -> start_run()
```

当前投递：

```python
thread_id = uuid5("aimail", email)
```

也就是说：

```text
一个地址 -> 一个固定 thread
```

不是：

```text
一个邮件 thread -> 一个 thread_id
```

这会导致：

- 同一地址的所有邮件共用一个 session；
- 回复邮件无法独立追溯原邮件 thread；
- 新邮件也会并入同一个 thread；
- 多地址时每个地址一个 thread，地址之间可以并行，但地址内 thread 粒度太粗。

### 2.3 当前 context 注入

`aimail_deerflow.set_agent_context()` 调用共享核心的：

```python
_ACTIVE_AGENT_CONFIG
_CONFIG_LOADER
os.environ["AIMAIL_AGENT_EMAIL"]
```

这是进程级全局状态。

当前 HTTP 模式下，每个 `/aimail/inbound` 请求都会重设它；如果多个请求并发：

```text
request A: set agent A
request B: set agent B
request A: preprocess -> 可能读到 agent B 的配置
```

存在地址串线风险。

单 SSE 如果顺序处理可以规避，但会牺牲并发；正确方向是：

- contextvar；
- 或每次调用显式传配置；
- 或 per-address lock/serial queue。

---

## 3. 目标模型

### 3.1 路由层级

```text
AIMail Gateway
  └── 一条 SSE (AgentSystemId, GatewaySystemId)
        └── DeerFlow Gateway
              ├── recipient -> agent/assistant
              ├── email thread -> thread_id
              ├── new thread -> create
              └── reply -> resume
```

### 3.2 绑定关系

```text
address
  -> DeerFlow assistant_id
  -> system_id / gateway
```

地址绑定仍然来自 AIMail binding；DeerFlow 不再为每个地址启动 inbound HTTP 端点。

### 3.3 Thread 粒度

```text
thread_id = uuid5(
  "aimail-thread",
  gateway_system_id + address + thread_root
)
```

- 新邮件：`thread_root = Message-ID`；
- 回复：`thread_root = 原 thread root`；
- 同一 thread 的回复得到同一 `thread_id`；
- DeerFlow 的 `start_run(..., thread_id)` 自动复用该 thread；
- 新 thread 自动创建 workspace。

### 3.4 新邮件 vs 回复

| 入站 | thread_id | DeerFlow 行为 |
|---|---|---|
| 新邮件，无引用 | 新 uuid5 | `start_run` 创建新 thread |
| 回复，命中 alias | 原 uuid5 | 原 thread resume |
| 重复 delivery | 原 uuid5 | 幂等，不重复 run |
| 未知引用 | 新 uuid5 | 新 thread，不猜旧 session |

### 3.5 Ack

```text
start_run accepted
  -> ack
start_run rejected / busy / no binding
  -> no ack
```

当前 `start_run` 返回 record 后 handler 已 200；V2 应把“accepted”作为 ack 条件。

---

## 4. 与当前 DeerFlow 代码的差异

### 4.1 移除 webhook/bridge

当前：

```text
/aimail/inbound
X-Webhook-Signature
per-address secret
local inbound URL
```

V2：

- 不再需要 per-address webhook endpoint；
- 不再需要 bridge；
- 不再需要 HMAC replay；
- SSE 本身已认证；
- 一个 SSE 消费者在 Gateway lifespan 内启动。

保留旧 inbound 路由只作为 legacy/兼容，不与 SSE 同时消费同一地址。

### 4.2 Thread ID 改为邮件 thread

当前：

```python
_thread_id_for(email)  # uuid5("aimail", email)
```

改为：

```python
_thread_id_for(gateway_system_id, address, thread_root)
```

- 仍是 UUID5，满足 DeerFlow thread ID 格式；
- 同一地址可以有多条邮件 thread；
- 回复追溯到原 thread。

### 4.3 新/旧 thread 的 `start_run`

当前：

```python
RunCreateRequest(
  assistant_id=...,
  config={"configurable": {"thread_id": _thread_id_for(email)}},
  multitask_strategy="reject",
  if_not_exists="create",
)
```

目标：

```python
thread_id = resolve_thread_id(...)
RunCreateRequest(
  assistant_id=cfg.assistant_id,
  config={"configurable": {"thread_id": thread_id}},
  ...
)
```

`if_not_exists="create"` 已经支持新 thread；回复使用同一 thread_id 即可 resume。

需要确认：

- `start_run` 对未知 thread_id + `if_not_exists="create"` 的实际行为；
- thread workspace 创建是否同步完成；
- 是否存在 thread ID 校验/格式限制；
- 无 agent 绑定时的 fallback 是否安全。

### 4.4 Outbound Message-ID alias

同 Hermes 方案：

- `send_mail` 带 `In-Reply-To` / `References`；
- 记录 Gateway 返回的 outbound `Message-ID`；
- alias 指向 `thread_id`；
- 外部回复才能命中原 thread。

没有这一步，DeerFlow 的 thread 追溯只能覆盖“回复入站原邮件”，无法覆盖“回复 Agent 出站邮件”。

---

## 5. 为什么 DeerFlow 比 Hermes 容易

| 维度 | Hermes | DeerFlow |
|---|---|---|
| 进程模型 | 一个 gateway，多 profile scope | 一个 Gateway，多 agent/thread |
| 跨 profile 投递 | 无公共 API | 不需要 profile；直接 start_run |
| 新 session 创建 | platform adapter / host router | `start_run` 直接创建 thread |
| session 容器 | profile | thread |
| workspace | profile 下目录，不是 session 容器 | thread 自带 workspace |
| 单 SSE 路由 | 需要 Host Router + IPC | Gateway 内直接路由 |
| 回复追溯 | thread→chat_id | thread→thread_id |

DeerFlow 的 thread 本身就是“session + workspace 容器”，与 DSH 的 workspace 语义更接近：

```text
DeerFlow thread
  ├── session history
  └── workspace
```

因此：

- 地址绑定 assistant；
- thread 绑定 workspace/session；
- 一个 SSE 可路由多个地址/assistant；
- 回复复用同一 thread_id 即可复用 workspace。

---

## 6. 当前阻塞点

### 6.1 入站仍是 push，不是 SSE

`aimail_inbound.py` 依赖：

- `/aimail/inbound` HTTP route；
- `X-Webhook-Signature`；
- per-address webhook secret；
- bridge/本地 inbound URL。

V2 单 SSE 需要：

- 一个 SSE consumer；
- 不绑定 per-address HTTP；
- ack 由 SSE consumer 控制。

### 6.2 Thread 粒度是地址，不是邮件 thread

当前：

```text
address -> one fixed thread
```

目标：

```text
address + email thread -> one thread
```

这是回复追溯的核心改造。

### 6.3 `set_agent_context` 是进程全局

`_ACTIVE_AGENT_CONFIG` + `AIMAIL_AGENT_EMAIL` 是全局。

一个 SSE 顺序消费时可工作，但：

- 不同地址并发处理会串配置；
- 不同邮件 thread 并发会串；
- handler 内 await `start_run` 前后都可能被其他 delivery 改上下文。

需要：

- contextvar；
- 或显式把 cfg 传入 `process_inbound_mail`；
- 或 per-address/per-delivery lock。

推荐 contextvar + 显式 cfg，避免牺牲并发。

### 6.4 `multitask_strategy="reject"`

当前：

```python
multitask_strategy="reject"
```

同一 thread 正在运行时，新回复可能被拒绝。

V2 需要明确：

- 拒绝 ⇒ 不 ack，pending 重投；
- 或排队到 thread FIFO，accepted 后 ack；
- 不能把“拒绝”当“已投递”。

### 6.5 单 SSE 单例

DeerFlow Gateway 可能以 uvicorn 多 worker 运行。

如果每个 worker 都启动 SSE：

- 同一 AgentSystemId 多条 SSE；
- 重复拉取；
- 重复 ack/重复 run。

需要：

- 单 worker；或
- leader lock（文件锁/DB lock）；
- 或 host-level singleton；
- 启动/停止与 lifespan 绑定。

### 6.6 用户/租户上下文

DeerFlow 的 custom agent 可能按 user 存储，thread 也可能有 user/tenant 归属。

当前 AIMail DeerFlow adapter 主要靠：

```text
assistant_id
agent_id
system_id
```

没有明确的 AIMail user/tenant 映射。

如果 Gateway 是多用户部署，单 SSE 必须解决：

```text
recipient address -> user -> assistant
```

否则可能：

- 用错用户默认 agent；
- 跨用户读写 thread；
- 跨租户泄漏。

如果只支持单用户，则明确限制部署为 single-user。

### 6.7 Outbound Message-ID 闭环

DeerFlow 当前 AIMail MCP `send_mail` 是否返回 outbound `Message-ID` 需要确认。

没有它：

- 回复 Agent 出站邮件无法命中 alias；
- 只能按新 thread 处理；
- 回复追溯不完整。

### 6.8 旧 thread 迁移

现有地址 thread：

```text
uuid5("aimail", email)
```

新模型：

```text
uuid5("aimail-thread", gateway + address + root)
```

两者不同。

需要决定：

- 旧 thread 只读保留；
- 新邮件开始新 thread；
- 或做一次 thread 映射迁移/归档；
- 不能把旧地址 thread 当作所有新邮件的默认 session。

---

## 7. 改造清单

### 7.1 新增 SSE consumer

位置：DeerFlow Gateway lifespan / startup hook。

职责：

- 读 AIMail bindings；
- 按 `(AgentSystemId, GatewaySystemId)` 建一条 SSE；
- 收到 delivery 后：
  1. 校验 recipient 绑定；
  2. 解析 thread；
  3. 选择 assistant；
  4. `start_run`；
  5. accepted 后 ack。

### 7.2 Thread 映射

新增持久化映射：

```text
aimail_thread
  gateway_system_id
  address
  thread_root
  thread_id
  assistant_id
  state
  last_seen
```

alias：

```text
message_id -> thread_root
direction: inbound | outbound
```

建议放在 DeerFlow 的 state store 或 AIMail 侧数据库；必须跨重启持久。

### 7.3 绑定

现有 per-address binding 增加/确认：

```text
assistant_id
user_id / tenant_id （如多用户）
agent_id
gateway_system_id
```

地址只决定 agent/assistant；thread 决定 session/workspace。

### 7.4 入站执行

伪代码：

```python
async def deliver(delivery):
    cfg = find_binding(delivery.address)
    if not cfg:
        return REJECT

    root = resolve_thread_root(delivery.headers)
    thread_id = thread_id_for(cfg, root)
    assistant = cfg.assistant_id

    run_body = RunCreateRequest(
        assistant_id=assistant,
        input={"messages": [{"role": "user", "content": content}]},
        config={"configurable": {"thread_id": thread_id}},
        metadata={
            "idempotency_key": f"aimail:{delivery.mail_id}",
            "aimail_email": delivery.address,
        },
        multitask_strategy="queue",  # 或 reject + no-ack
        if_not_exists="create",
    )
    record = await start_run(run_body, thread_id, request)
    return ACCEPTED if record else REJECT
```

### 7.5 回复

- reply alias hit → same thread_id；
- `start_run` 复用 thread；
- workspace 随 thread 持久；
- 不创建新 thread。

### 7.6 出站

- `send_mail` 自动带 `In-Reply-To` / `References`；
- 记录 outbound Message-ID；
- 写 alias；
- 外部回复可追溯。

### 7.7 Legacy

- 旧 `/aimail/inbound` + webhook 仅作为 legacy；
- V2 SSE 与 legacy 不能同时消费同一地址；
- 切换需要显式迁移。

---

## 8. 实施阶段

### Phase 0 — 能力验证

- 固定 DeerFlow 版本；
- 验证 `start_run` 未知 thread_id + `if_not_exists=create`；
- 验证新 thread workspace 创建；
- 验证同一 thread_id 回复复用；
- 验证 `multitask_strategy` 行为；
- 验证 assistant/user 解析。

### Phase 1 — Thread 映射

- 实现 thread root 解析；
- 实现 alias 表；
- 单地址、单 thread 测试；
- 不接 SSE，先用单元测试。

### Phase 2 — SSE consumer

- 单 SSE、单地址；
- ack/重连/重启；
- 无 webhook。

### Phase 3 — 多地址

- 一个 SSE 多地址；
- 不同 assistant；
- 不同 thread；
- contextvar 隔离；
- 并发测试。

### Phase 4 — 回复追溯

- 回复入站邮件；
- 回复出站邮件；
- References 链；
- 未知引用；
- subject 兜底默认关闭。

### Phase 5 — 生产化

- uvicorn worker 单例；
- 用户/租户映射；
- legacy 切换；
- 监控/日志。

---

## 9. 测试计划

| 测试 | 期望 |
|---|---|
| 新邮件 | 新 thread_id，新 workspace |
| 回复入站 | 原 thread_id，原 workspace |
| 回复出站 | 原 thread_id |
| 两个 thread 同地址 | 两个 thread，不合并 |
| 多地址一个 SSE | 地址路由正确，无串线 |
| 不同 assistant | 使用各地址绑定的 assistant |
| 并发投递 | context 不串 |
| start_run reject | 不 ack，pending 保留 |
| 重复 delivery | 幂等，不重复 run |
| Gateway 重启 | thread 映射持久 |
| 多 worker | 仅一个 SSE（lock） |
| 多用户 | 不跨用户/租户 |

---

## 10. 开放问题

1. DeerFlow 当前版本 `start_run` 对未知 thread_id + `if_not_exists=create` 是否总是创建？
2. Thread workspace 是同步创建还是 run 开始后创建？
3. `multitask_strategy` 支持哪些值，是否有队列语义？
4. `assistant_id` 是否足以确定 agent，还是还需要 user/tenant？
5. `start_run` 的 `request` 是否携带认证用户，AIMail inbound 如何构造？
6. `send_mail` 是否返回 Gateway outbound Message-ID？
7. 多 worker 部署是否官方支持；若支持，leader lock 放哪里？
8. 旧地址 thread 是否迁移，还是只读保留？

---

## 11. 最终结论

DeerFlow 可以改造成单 SSE 多地址 V2 模型，而且比 Hermes 更自然：

```text
一个 SSE
  -> 一个 DeerFlow Gateway
  -> address 选 assistant
  -> email thread 选 thread_id
  -> start_run 创建/恢复 thread
```

关键改造不是 host 跨 profile 路由，而是：

1. 去掉 webhook/push，改成 SSE consumer；
2. 地址内 thread 粒度从 address 改为 email thread；
3. alias 支持回复追溯；
4. `set_agent_context` 从进程全局改为 contextvar/显式 cfg；
5. ack 与 `start_run` accepted 对齐；
6. 单 SSE 单例与多用户隔离。

如果这六点解决，DeerFlow 是比 Hermes 更适合承载“单 SSE + 多地址路由”的宿主。

本文档只做调研，不修改当前 V2 测试或运行代码。
