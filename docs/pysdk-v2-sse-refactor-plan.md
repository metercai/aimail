# pysdk V2 改造方案：去 CLI / 去 push，插件内 SSE 闭环

状态：**方案汇总，不落地**  
目标版本：AIMail Host SSE V2  
范围：Hermes、DeerFlow 的 pysdk/plugin 路径  
不修改：当前 V2 测试、QA 流程、运行代码

---

## 0. 前置条件：当前 V2 QA HOLD

当前独立 QA 结论是 HOLD：

- **B1**：`isAgentScopeBinding` 仍按 V1 `shared_addr_` 前缀；
- **B2**：适配器 pull client 缺 aggregate path 与 `X-Agent-System-Id`；
- **B3**：OpenClaw 读到邮件但未进入 session；
- **B4**：DSH 会话工具面为空。

证据：

- [`tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md`](tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md)

Hermes/DeerFlow 的 SSE runtime 都会复用同一套 pull/client 核心，因此：

```text
B1/B2 必须先修
B3/B4 的宿主交付口径必须先明确
```

否则新宿主只会复制同样的 pull/ack 缺陷。

---

## 1. 总目标

```text
删除 CLI 依赖
删除 push/webhook 端点依赖
删除 per-profile webhook 端口
删除 bridge / host patch

所有入站由插件内的 V2 SSE 完成
所有业务闭环在插件进程内完成
```

业务闭环：

```text
V2 aggregate SSE
  -> binding / recipient
  -> 邮件预处理
  -> 选择宿主执行单元
  -> 创建/复用 session
  -> agent turn
  -> send_mail 出站
  -> 记录 outbound Message-ID alias
  -> ack
```

---

## 2. 宿主模型矩阵

| 宿主 | 地址绑定层 | session/workspace 容器 | 单 SSE 范围 | 当前落地条件 |
|---|---|---|---|---|
| Hermes | profile | profile（session 在 profile 下） | **一个 SSE = 一个 profile = 一个地址** | profile-scoped SSE 可行 |
| DeerFlow | agent / assistant | thread（thread 自带 workspace） | **一个 SSE = 一个 Gateway = 多地址** | 可改造，host 内直接路由 |
| DSH | workspace | workspace | 单 SSE 多地址 | 已有 V2 路径，需修 QA B1/B2 |
| Pi | 单地址 | 单 workspace | 单 SSE 单地址 | 已有 V2 路径 |
| OpenClaw | agent/workspace | session | 单 SSE 多地址（同 AgentSystemId） | 已有 V2 路径，需修 B3 |

### Hermes 约束

Hermes 的 profile 是隔离边界：

- profile = workspace；
- session 归属 profile；
- 公共 plugin API 不能跨 profile 注入；
- 因此当前只能：

```text
one SSE per (profile AgentSystemId, Gateway)
one profile = one address
```

不做 profile 内多地址聚合。

### DeerFlow 优势

DeerFlow 的 Gateway 是一个进程，管理多个 agent/thread：

```text
Gateway
  └── agent / assistant（地址绑定层）
        ├── thread 1（session + workspace）
        ├── thread 2
        └── ...
```

所以可以做：

```text
one SSE per (host AgentSystemId, Gateway)
  -> address -> assistant
  -> email thread -> thread_id
  -> start_run()
```

---

## 3. 共享 pysdk 核心改造

### 3.1 保留

- 邮件预处理：identity/persona/enrichment/attachment/thread/search；
- `_GatewayClient` / signed request；
- `aimail_address` 激活逻辑；
- 邮件/board tools；
- 共享 tool-spec；
- activation SKILL。

### 3.2 删除/降级为 legacy

| 组件 | 处理 |
|---|---|
| `pysdk/install.py` | 不再作为 Hermes/DeerFlow 主安装路径 |
| `pysdk/hermes/ensure_config.py` | 删除 `platforms.webhook` 写入 |
| `pysdk/hermes/patch_webhook.py` | Legacy，不再 patch host |
| `pysdk/hermes/register_profiles.py` | Legacy，不自动注册地址 |
| `pysdk/deer-flow/aimail_inbound.py` | Legacy，不再作为 V2 入站 |
| DeerFlow bridge / webhook | Legacy，V2 不使用 |
| `aimail_system` | 不注册 |
| per-address `webhook_url` | V2 地址不使用 |
| CLI 安装/注册链 | 从 Hermes/DeerFlow 主路径移除 |

### 3.3 新增共享模块

建议：

```text
pysdk/v2/
├── sse_runtime.py       # aggregate SSE + reconnect + ack
├── binding.py           # binding 解析 / Gateway 分组
├── agent_system.py      # AgentSystemId 派生
├── thread.py            # email thread root / alias
├── session_map.py       # thread -> host session/thread
├── outbound_registry.py # outbound Message-ID alias
├── delivery.py          # delivery -> host adapter
└── ack.py               # ack 策略
```

### 3.4 SSE runtime

每个 host 的 runtime：

```text
bindings
  -> group by (AgentSystemId, GatewaySystemId)
  -> one aggregate SSE per group
  -> delivery
  -> host router
  -> ack
```

要求：

- `X-Agent-System-Id`；
- aggregate stream/pending/ack 路径；
- heartbeat/reconnect；
- delivery 幂等；
- ack 只在宿主接受后；
- 无 push 回调、无本地 inbound HTTP。

### 3.5 Binding

binding 至少包含：

```text
address
gateway_url
gateway_system_id
agent_system_id
api_key
host_target
```

`host_target`：

- Hermes：profile id；
- DeerFlow：assistant_id / user；
- DSH：workspace；
- Pi/OpenClaw：本地 agent id。

不得再从 address 猜 system 前缀；以 `agent_system_id` 为准。

### 3.6 Ack

```text
host accepted -> ack
host queued    -> ack（已进入宿主 FIFO）
host rejected  -> no ack
no binding     -> no ack
host absent    -> no ack
```

`start_run`/adapter 的 accepted 必须和 ack 对齐；不能读取即 ack。

### 3.7 幂等

- `delivery_id`；
- 或 `(gateway_system_id, mail_id)`；
- 重复投递不重复创建 session；
- 崩溃后重投不重复 run。

---

## 4. Hermes 改造

### 4.1 目标

```text
profile = workspace
one profile = one address
one SSE per profile/Gateway
```

不引入 profile 内多地址聚合；不引入 Host Router。

### 4.2 组件

```text
pysdk/hermes_plugin/
├── plugin.yaml
├── __init__.py        # register(ctx)
├── adapter.py         # AIMailAdapter(BasePlatformAdapter)
├── runtime.py         # profile-scoped SSE
├── profile.py         # profile home / AgentSystemId
├── tools.py           # aimail_* tools
└── skills/activation/
```

### 4.3 入站

使用 Hermes platform adapter：

```text
SSE delivery
  -> AIMailAdapter.handle_message(
       MessageEvent(
         source=SessionSource(
           platform=aimail,
           chat_id=<thread chat id>,
           chat_type="dm",
           ...
         )
       )
     )
```

不要用 `ctx.inject_message` 作为主路径。

### 4.4 新邮件/回复

Hermes session key 是确定性的：

```text
agent:<profile>:aimail:dm:<chat_id>
```

- 新邮件：新 chat_id -> 新 session；
- 回复：同 chat_id -> 原 session；
- chat_id 由 email thread root 派生；
- 回复追溯依赖 `In-Reply-To`/`References` + alias。

### 4.5 删除

- `platforms.webhook`；
- webhook route/secret；
- `webhook_subscriptions`；
- `patch_webhook.py`；
- `ensure_config` 的 webhook 分支；
- per-profile 端口；
- `aimail install` CLI。

### 4.6 限制

- 公共 plugin API 不能跨 profile；
- 单 SSE 多 profile 仍需 Host Router，不在本方案；
- 一个 profile 一个地址，不做多地址聚合。

---

## 5. DeerFlow 改造

### 5.1 目标

```text
one SSE per (host AgentSystemId, Gateway)
  -> address -> assistant
  -> email thread -> thread_id
  -> start_run()
```

### 5.2 当前问题

- 入站是 webhook/bridge；
- thread 固定按地址：`uuid5("aimail", email)`；
- 不是按邮件 thread；
- `set_agent_context` 是进程全局；
- `multitask_strategy="reject"`；
- 无单 SSE / AgentSystemId；
- outbound Message-ID 闭环未确认。

### 5.3 目标 thread

```text
thread_id = uuid5(
  "aimail-thread",
  gateway_system_id + address + thread_root
)
```

- 新邮件：新 thread；
- 回复：同 thread；
- thread 自带 workspace；
- 不按地址固定 thread。

### 5.4 入站

```python
cfg = find_binding(delivery.address)
root = resolve_thread_root(delivery.headers)
thread_id = thread_id_for(cfg, root)

run_body = RunCreateRequest(
    assistant_id=cfg.assistant_id,
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
```

accepted 后 ack。

### 5.5 Context 隔离

当前全局：

```text
_ACTIVE_AGENT_CONFIG
_CONFIG_LOADER
AIMAIL_AGENT_EMAIL
```

改为：

- contextvar；
- 或把 cfg 显式传入 `process_inbound_mail`；
- 或 per-delivery lock；
- 不能依赖进程全局。

### 5.6 多地址

DeerFlow 可以：

```text
SSE
  -> address A -> assistant A -> thread A1/A2
  -> address B -> assistant B -> thread B1/B2
```

一个 SSE 多地址是自然的，不需要 Host Router。

### 5.7 单例 / 多用户

- uvicorn 多 worker 时必须 leader lock；
- 或限定单 worker；
- address -> user -> assistant 的租户映射必须明确；
- 不能跨用户/租户。

---

## 6. 回复追溯（两宿主共用）

### 6.1 Alias

```text
message_id -> thread_root
direction: inbound | outbound
```

记录：

- 入站 Message-ID；
- Agent outbound Message-ID；
- 线程根。

### 6.2 判定

```text
In-Reply-To / References 命中 alias
  -> 原 thread
  -> 原 session/thread

Message-ID 已知
  -> duplicate

都没有
  -> 新 thread / 新 session
```

### 6.3 send_mail

- 自动带 `In-Reply-To` / `References`；
- 返回 outbound Message-ID；
- 写入 alias；
- 外部回复才能回溯。

### 6.4 默认策略

- subject-only 匹配默认关闭；
- 未知引用默认新 thread；
- 不跨 profile/agent 猜 session。

---

## 7. 业务闭环

```text
SSE delivery
  -> binding lookup
  -> recipient validation
  -> thread resolution
  -> preprocess
  -> host dispatch
  -> session/thread create or resume
  -> agent turn
  -> send_mail
  -> outbound Message-ID alias
  -> ack
```

任何一步失败：

```text
no ack
pending remains
```

---

## 7.1 入站预处理放在哪里

结论：

```text
在目标 profile 的 AIMail runtime 内做
在 AIMailAdapter.handle_message() 之前做
不在 agent session/turn 内做
不在全局 SSE router 里替某个 profile 做
```

完整顺序：

```text
V2 SSE
  -> recipient / binding / dedupe
  -> 路由到目标 profile runtime
  -> AIMail 预处理
  -> AIMailAdapter.handle_message()
  -> gateway 创建/复用 session
  -> agent turn
```

### 为什么必须在 handle_message 之前

1. **thread 信息决定 chat_id/session**
   - `In-Reply-To` / `References` 必须在 adapter 之前解析；
   - 新邮件/回复必须先生成稳定 chat_id；
   - 否则 adapter 无法区分新 session 和回复 session。

2. **ping/pong、重复、拦截类邮件不能创建 session**
   - 预处理会拦截 ping/pong、重复投递、无效邮件；
   - 如果先进 session 再预处理，会产生空 session/无效 turn。

3. **附件、persona、enrichment 需要进入最终 MessageEvent**
   - 预处理结果就是 agent 看到的 content；
   - 放在 session 内会变成二次处理，增加延迟和失败面。

4. **ack 语义**
   - 预处理失败 ⇒ 不创建 session，也不 ack；
   - 只有 adapter 接受后才 ack。

### 为什么必须在目标 profile runtime 内

- binding、persona、SOUL、skills、附件目录都属于 profile；
- 默认 profile/router 不应读取其他 profile 的配置；
- 单 SSE 多 profile 场景下必须先路由到目标 profile，再预处理；
- Hermes 的 profile-scoped SSE 天然满足；
- DeerFlow 没有 profile，预处理在 delivery 的 binding/assistant 上下文中做，仍在 `start_run` 之前。

### 分层职责

| 层 | 负责 |
|---|---|
| SSE runtime | recipient、binding、dedupe、路由、ack |
| profile AIMail runtime | identity/thread/persona/enrichment/attachment/ping-pong |
| AIMailAdapter.handle_message | 构造 MessageEvent，进入 host session |
| host session/agent | 业务推理、工具调用、回复 |

### Hermes

```text
profile plugin runtime
  -> preprocess
  -> adapter.handle_message
  -> session
```

预处理在 profile 插件内，但仍在 `handle_message` 之前。

### DeerFlow

```text
Gateway SSE runtime
  -> binding/assistant context
  -> preprocess
  -> start_run
  -> thread
```

预处理在 `start_run` 之前；不能放进 thread/agent 执行阶段。

### 禁止

- 在 `handle_message`/`start_run` 之后再做 AIMail 预处理；
- 在 default profile 里用全局配置替其他 profile 预处理；
- 在 agent session 里下载附件/解析 thread/做 persona 规范化；
- 读取即 ack 后再预处理。

---

## 8. 分阶段实施

### Phase 0 — QA 前置

- 修 B1/B2；
- 明确 B3/B4 的宿主交付口径；
- 重跑 QA 真实宿主链。

### Phase 1 — 共享 SSE 核心

- binding/AgentSystemId；
- aggregate SSE；
- ack/idempotency；
- 单测 + 真 Gateway L2。

### Phase 2 — Hermes plugin

- platform adapter；
- profile-scoped SSE；
- 新/回复 session；
- 无 webhook/patch/CLI。

### Phase 3 — DeerFlow

- SSE consumer；
- thread 映射；
- contextvar；
- assistant/user；
- 单例。

### Phase 4 — 回复追溯

- alias；
- outbound Message-ID；
- 未知引用策略；
- 跨地址/跨 agent 边界。

### Phase 5 — 移除 legacy

- 停用 push/webhook；
- 停用 CLI 安装；
- 旧路径只读/显式 legacy；
- 文档与安装流程更新。

---

## 9. 测试矩阵

| 测试 | Hermes | DeerFlow |
|---|---|---|
| 无 CLI 激活 | `aimail_address` | `aimail_address` |
| 无 push 端点 | 无 webhook | 无 inbound route |
| 一条 SSE | 每 profile/Gateway 一条 | 每 Gateway 一条 |
| 多地址 | 不支持（一 profile 一地址） | 支持 |
| 新邮件新 session | 新 chat_id | 新 thread_id |
| 回复复用 | 同 chat_id | 同 thread_id |
| 回复 outbound | alias | alias |
| ack | adapter 接受后 | start_run accepted 后 |
| 重启 | thread/chat 映射持久 | thread 映射持久 |
| 并发 | profile 内串行/隔离 | contextvar 隔离 |
| 多租户 | N/A | user/tenant 映射 |

---

## 10. 风险与开放问题

1. Hermes plugin platform adapter API 版本差异；
2. Hermes `handle_message` 返回 session 信息与 chat_id 语义；
3. DeerFlow `start_run` 未知 thread + `if_not_exists=create` 行为；
4. DeerFlow `multitask_strategy` 队列语义；
5. `send_mail` 是否返回 outbound Message-ID；
6. DeerFlow 多 worker 单例；
7. DeerFlow 多用户/租户上下文；
8. 旧 thread 迁移策略；
9. 旧 webhook/CLI 与 V2 双消费禁止。

---

## 11. 验收标准

```text
[ ] 无 CLI 调用完成激活
[ ] 无 push/webhook 端点
[ ] 无 per-profile 端口
[ ] 每个 (AgentSystemId, Gateway) 一条 SSE
[ ] Hermes: 一 profile 一地址
[ ] DeerFlow: 一 SSE 多地址
[ ] 新邮件创建新 session/thread
[ ] 回复复用原 session/thread
[ ] 回复 Agent 出站邮件可追溯
[ ] 未进入宿主不 ack
[ ] 重启后 thread 映射恢复
[ ] 无跨 profile/agent/租户串线
```

---

## 12. 结论

统一方向：

```text
pysdk 只保留共享邮件核心 + 一个 V2 SSE runtime
宿主差异用 adapter 表达
```

- Hermes：profile-scoped SSE，一 profile 一地址；
- DeerFlow：Gateway 内单 SSE 多地址，address→assistant，thread→thread_id；
- 两者都不再依赖 CLI、push endpoint、webhook、bridge、host patch；
- 回复追溯统一用 email thread + Message-ID alias；
- 当前 V2 QA HOLD 必须先修，否则不能作为宿主改造基线。
