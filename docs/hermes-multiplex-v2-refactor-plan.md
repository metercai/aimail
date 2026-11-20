# Hermes 多 Profile 架构下的 AIMail pysdk V2 改造方案

状态：调研 + 方案，尚未实现  
目标版本：AIMail Host SSE V2（`agent-aimail 0.5.0` 的 Hermes 适配）  
范围：Hermes pysdk / Hermes plugin，不含 Gateway 协议变更

---

## 0. 先决条件：当前 V2 独立 QA 结论为 HOLD

在实施本方案前，必须先修复当前 V2 的 QA 阻断项（见
`tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md`）：

- **B1**：`isAgentScopeBinding` 仍按 V1 `shared_addr_` 前缀判断；
- **B2**：适配器构造的 pull client 缺 aggregate path 与 `X-Agent-System-Id`；
- **B3**：OpenClaw 读到邮件但没有进入 agent session（不 ack 是符合契约的）；
- **B4**：DSH 会话工具面为空，需确认会话创建后的工具加载口径。

Hermes 的 SSE runtime 也会复用同一 pull/client 核心，因此 B1/B2 必须先修，
B3/B4 的宿主交付口径也必须先明确，否则 Hermes 方案无法进入实现验收。

---

## 1. 调研结论：Hermes 已改为“一条 gateway 服务多个 profile”

### 1.1 官方文档与代码确认

Hermes 最新架构已不是“一个 profile 一个 gateway”：

- [Multiplexing Gateway Internals](https://hermes-agent.nousresearch.com/docs/developer-guide/multiplexing-gateway)
- [Multi-profile gateways](https://hermes-agent.nousresearch.com/docs/user-guide/multi-profile-gateways)
- [`hermes_cli/gateway_multiplex_mode.py`](https://github.com/NousResearch/hermes-agent/blob/main/hermes_cli/gateway_multiplex_mode.py)

关键事实：

1. `gateway.multiplex_profiles` **默认开启**；
2. 一个 host 上**一条 gateway 服务所有 profile**；
3. 显式 `false` 已 RETIRED：会被解析、记录，然后按 unset 处理；
4. `gateway.standalone: true` 只是临时兼容 shim，不是推荐拓扑；
5. profile 仍然隔离：
   - config；
   - skills；
   - memory；
   - credentials；
   - session namespace；
6. 二级 profile 不能启用 port-binding platform：
   - `webhook`
   - `api_server`
   - `msgraph_webhook`
   - `feishu`
   - `wecom_callback`
   - `bluebubbles`
   - `sms`
7. 二级 profile 的 HTTP inbound 使用共享 listener：

```text
/p/<profile>/...
```

8. session key 命名空间带 profile：

```text
agent:<profile>:...
```

### 1.2 对 AIMail 的直接结论

旧 pysdk 的假设已经不成立：

| 旧假设 | 多 profile 架构下 |
|---|---|
| 每个 profile 一个 gateway | 一个 gateway 服务所有 profile |
| 每个 profile 一个 webhook 端口 | 二级 profile 不能启用 webhook 端口 |
| 每个 profile 一个 webhook route | 不再有 per-profile 端口/route |
| CLI 逐 profile 安装 | 应由 host plugin 生命周期完成 |
| 每 profile 各自一条 SSE | 需要按 AgentSystemId 统一，但 profile 隔离限制了跨 profile 注入 |

### 1.3 新架构下必须区分的两个“空间”

- **Hermes profile**：隔离的配置/技能/记忆/凭据/session 空间；
- **AIMail AgentSystemId**：本地 AIMail 客户端实例身份，用于 Gateway 聚合。

在当前 Hermes 公共插件 API 下，推荐：

```text
Hermes profile = AIMail Agent 空间（workspace）
每个 profile = 一个 AIMail AgentSystemId
每个 (AgentSystemId, Gateway) = 一条 SSE
```

理由：

- profile 是 Hermes 自己定义的隔离边界；
- 每个 profile 有独立的 `HERMES_HOME`、session namespace、credentials；
- 公共插件上下文属于单个 profile；
- Hermes 公共 API 不允许一个 profile 的 plugin 直接向另一个 profile 创建/注入 session；
- 因此“一个 host 全部 profile 共用一条 SSE”不能只靠 pysdk 实现，需要 Hermes host 级跨 profile 总线。

如果产品坚持“一个 Hermes gateway 只允许一条 AIMail SSE”，那需要 Hermes host 提供跨 profile 的注入服务；本方案不假设该能力存在。

---

## 2. 当前 pysdk 的具体阻塞点

### 2.1 `pysdk/hermes/ensure_config.py`

当前行为：

- 给 profile 写 `platforms.webhook.enabled`；
- 分配 `port: 8644`；
- 生成 `secret`；
- 设置 `platform_toolsets.webhook` / `cli`。

问题：

- 二级 profile 在多 profile gateway 中禁止 port-binding platform；
- 每个 profile 都开端口会冲突；
- webhook route 不再是 AIMail 的入站主路径；
- V2 要求 SSE 内部闭环，不需要本地 listener。

### 2.2 `pysdk/hermes/aimail_hermes.py`

当前：

- `_pull_replay_target()` 读取本地 webhook URL/secret；
- `_on_pull_email()` 把 SSE/pull 到的邮件 **HTTP replay** 回本地 webhook；
- `_pull_route_secret()` 读 `webhook_subscriptions.json`；
- `start_agent_pull()` 只按 pointer 单地址拉取；
- `register_profile_hook` 自动注册 profile 地址。

问题：

- 依赖 per-profile webhook 端口；
- 不是 SDK 内闭环，而是“拉到本地再发 HTTP”；
- 无 AgentSystemId 聚合；
- profile 隔离依赖环境变量/全局，不适合多 profile 并发。

### 2.3 `pysdk/hermes/register_profiles.py`

当前：

- 遍历 profile；
- 每个循环临时设置全局 `os.environ["HERMES_PROFILE_DIR"]`；
- 再调用 `_auto_register_email`。

问题：

- 多 profile 同进程下，全局 env 不是隔离的；
- 依赖 CLI/注册链；
- 自动注册地址不符合 V2“地址激活唯一入口”。

### 2.4 `pysdk/hermes/patch_webhook.py`

当前：

- 通过 patch Hermes `webhook.py` 注入 AIMail 的入站链。

问题：

- 依赖 host 源码 patch；
- 与 plugin 化、profile 隔离和 SSE-only 方向冲突；
- 升级 Hermes 时 anchor 容易失效。

---

## 3. 目标架构

### 3.1 总体结构

```text
Hermes multiplex gateway (one process)
  ├── profile A  (HERMES_HOME A)
  │     ├── AIMail plugin context A
  │     ├── AgentSystemId A
  │     ├── SSE (AgentSystemId A, Gateway)
  │     └── AIMail platform adapter A
  ├── profile B  (HERMES_HOME B)
  │     ├── AIMail plugin context B
  │     ├── AgentSystemId B
  │     ├── SSE (AgentSystemId B, Gateway)
  │     └── AIMail platform adapter B
  └── ...
```

每个 profile：

- 独立插件实例；
- 独立 AgentSystemId；
- 独立 SSE；
- 独立地址 binding；
- 独立 session namespace；
- 不跨 profile 路由。

### 3.2 为什么用 Hermes Platform Adapter

Hermes 的插件 platform adapter 是官方扩展点：

- [`Adding a Platform Adapter`](https://hermes-agent.nousresearch.com/docs/developer-guide/adding-platform-adapters)
- `ctx.register_platform(...)`
- `BasePlatformAdapter.handle_message(event)` 走 gateway 正常入站路径；
- `trusted_inbound=True` 适用于服务型入站（无人类 sender），AIMail 属于这一类；
- platform adapter 由 profile 的插件上下文注册；
- 不绑定端口（`connect()` 不需要 listener）；
- 由 gateway 创建/复用 session，自动使用 `agent:<profile>:` namespace。

**不要把 AIMail 继续做成 webhook platform。**  
把 SSE 拉到的邮件直接交给 `AIMailAdapter.handle_message()`，由 gateway 完成 session 创建和路由。

### 3.3 数据流

```text
Gateway
  └── SSE (AgentSystemId, Gateway)
        └── delivery
              └── AIMailRuntime (profile)
                    └── AIMailAdapter.handle_message(event)
                          └── Hermes gateway session:
                                chat_id = thread key
                                platform = aimail
                                profile = current profile
```

Ack：

- `handle_message` 被 gateway 接受后才 ack；
- session 创建失败、profile 不服务、busy 且无法入队 ⇒ 不 ack，保持 pending；
- 重复 delivery 按 message_id/thread 幂等。

### 3.4 AgentSystemId 取值

```text
AgentSystemId = stable_hash(
  profile_home,          # HERMES_HOME / profile home
  machine_hardware_fingerprint
)
```

- 每个 profile 独立；
- 同 profile 重启稳定；
- profile 克隆/迁移到新 home ⇒ 新 AgentSystemId；
- binding 目录仍按 GatewaySystemId 分组；
- 与 DSH 的差异：DSH 的 AgentSystemId 对应整个 DSH 实例，Hermes 按 profile 隔离。

> 如果产品要求 Hermes 一个 host 一个 AgentSystemId，需要 Hermes host 提供跨 profile 的
> “默认 profile 路由到其他 profile adapter”的内部服务；当前公共 plugin API 不提供。

---

## 4. pysdk 改造设计

### 4.1 新增 Hermes plugin 包

建议目录：

```text
pysdk/hermes_plugin/
├── plugin.yaml
├── __init__.py              # register(ctx)
├── adapter.py               # AIMailAdapter(BasePlatformAdapter)
├── runtime.py               # profile-scoped SSE + routing
├── profile.py               # profile home/binding/AgentSystemId
├── tools.py                 # aimail_* tool registration
├── skills/
│   └── activation/SKILL.md
└── legacy/                  # 旧 webhook/patch 代码仅保留迁移参考
```

`plugin.yaml`：

```yaml
name: aimail
kind: platform
version: "2.0.0"
description: AIMail inbound over SSE
provides_tools:
  - aimail_address
  # mail/board tools
```

> 具体 tool list 复用共享 `pysdk/tool-spec.json`，不要另抄一份。

### 4.2 `profile.py`

职责：

- 从 plugin context 取当前 profile home，而不是全局 env：
  - `ctx.home` / manager home（以实际 Hermes 版本 API 为准）；
  - 兼容 `HERMES_HOME`/`HERMES_PROFILE_DIR` 仅作为启动测试 seam；
- 派生 AgentSystemId；
- 定位 binding 根：

```text
<profile_home>/.aimail/systems/<GatewaySystemId>/<address>/
```

- 指针：

```text
<profile_home>/.agentmail
```

- 禁止从进程全局读取“另一个 profile”的配置。

### 4.3 `runtime.py`

职责：

- 每个 profile 启动一个 `AIMailRuntime`；
- 从 binding 枚举该 profile 的地址；
- 按 Gateway 分组：
  - 每个 `(AgentSystemId, Gateway)` 一条 SSE；
  - 多 Gateway 多条 SSE；
- 使用 V2 aggregate 端点：

```text
GET /api/v1/agent-system/pending/stream
POST /api/v1/agent-system/pending/ack
X-Agent-System-Id
```

- 收到 delivery 后：
  1. 用本地 binding 校验 recipient；
  2. 构造 `MessageEvent`；
  3. 调用 `adapter.handle_message(event)`；
  4. 成功后才 ack；
- 断线重连、心跳、watermark/幂等；
- `disconnect`/plugin unload 时停止 SSE。

### 4.4 `adapter.py`

`AIMailAdapter(BasePlatformAdapter)`：

- `connect()`：
  - 不打开端口；
  - 只标记 adapter 就绪，实际 SSE 由 runtime 启动；
- `disconnect()`：
  - 停止 runtime；
- `send()`：
  - AIMail 的出站由 `send_mail` 工具负责；
  - adapter `send()` 建议返回成功并记录“由工具负责出站”，避免 gateway 把 session 回复误发到未知通道；
  - 后续如需 gateway 自动回信，可在此接入 `send_mail`。
- `handle_message()`：
  - `chat_id` 使用 thread key；
  - `sender` 使用原邮件 sender；
  - `message_id` 使用原邮件 message_id；
  - `text` 使用 V2 富化后的 payload；
  - 让 gateway 决定新建/复用 session。

事件映射：

```text
platform    = "aimail"
chat_id     = hash(thread_root)                  # 同 thread 复用 session
chat_type   = "dm"
sender_id   = sender email
sender_name = sender display name
message_id  = mail.message_id
thread_id   = thread root
```

### 4.5 `tools.py` 与激活

- 注册 `aimail_address`：
  - 只接受 `action/email/code/gateway_url/workspace_path`；
  - 不接收 `manager/system_code/system_name/domain`；
  - 激活后写 binding + pointer；
  - 触发 runtime hot-add；
- 邮件/board 工具从共享 `tool-spec.json` 注册；
- 移除 `aimail_system`；
- 激活 SKILL 随 plugin 安装，不从 URL 加载。

### 4.6 安装与生命周期

- 用户通过 Hermes plugin manager 安装/启用 `aimail` plugin；
- 每个 profile 独立 enable；
- 不执行 `aimail install` CLI；
- 不 patch Hermes 源码；
- 不写 `platforms.webhook`；
- 不创建 webhook route；
- profile 创建/删除：
  - 不自动分配地址；
  - 地址只由 `aimail_address` 激活；
  - profile 删除时 runtime 停止、binding 保留/归档由用户显式处理。

---

## 5. 旧代码迁移

### 5.1 保留

- `aimail_base` 的邮件预处理、persona、attachment、thread、search；
- `aimail_tools` 的 `_GatewayClient`；
- `aimail_board`；
- 共享 `tool-spec.json`；
- 激活 SKILL。

### 5.2 移除/降级

- `pysdk/hermes/ensure_config.py` 的 `platforms.webhook` 写入：删除；
- `patch_webhook.py`：Legacy，不再安装；
- `_pull_replay_target` / `_on_pull_email` 的 HTTP replay：由 adapter 取代；
- `register_profiles.py` 的自动注册：Legacy；
- `aimail_install`/CLI 的 Hermes 分支：Legacy。

### 5.3 兼容

- 旧 Hermes（单 profile、webhook）继续使用旧 pysdk 分支；
- 新 V2 插件只在支持 multiplex + plugin platform 的 Hermes 版本启用；
- 通过 host capability probe 决定分支，不靠版本号猜测。

---

## 6. 分阶段实施

### Phase 0 — Host capability probe

- 检测 Hermes 版本/能力：
  - multiplex 默认开启；
  - `register_platform` 支持 `trusted_inbound`；
  - profile-scoped plugin context；
  - `handle_message` 可用；
- 确认 `BasePlatformAdapter` 当前签名；
- 不可用 ⇒ 保留 legacy webhook 分支并明确拒绝 V2。

### Phase 1 — Plugin skeleton

- `plugin.yaml`；
- `register(ctx)`；
- platform adapter；
- tool registration；
- activation SKILL；
- 不启动 SSE，先验证 plugin 可见。

### Phase 2 — Profile identity + binding

- profile home 解析；
- AgentSystemId 派生；
- binding 目录/指针；
- 单 profile 激活 `aimail_address`；
- 无 webhook。

### Phase 3 — SSE runtime

- aggregate SSE per profile/gateway；
- ack 语义；
- reconnect/heartbeat；
- 无本地 listener。

### Phase 4 — Inbound adapter/session

- adapter `handle_message`；
- thread → chat_id；
- gateway 创建/复用 session；
- 验证同 thread 连续、不同 thread 独立；
- 验证 profile namespace。

### Phase 5 — Multiplex tests

- 一条 gateway + 两个 profile；
- 两个 AgentSystemId；
- 两套地址；
- 两套 SSE；
- 各自 inbound session，不串 profile；
- 无 port 占用；
- 无 webhook config。

### Phase 6 — Legacy cutover

- 新 Hermes 默认 V2 plugin；
- 旧 webhook 分支只在显式 legacy 开关下；
- 文档/安装脚本移除 Hermes CLI 路径。

---

## 7. 测试计划

### 7.1 单元

| 测试 | 断言 |
|---|---|
| profile AgentSystemId | 同 profile home 稳定；不同 profile 不同 |
| binding 目录 | `systems/<GatewaySystemId>/<addr>` |
| tool schema | 无 system_code/manager/system_name/domain |
| SSE path/header | 使用 aggregate 端点 + X-Agent-System-Id |
| ack | 成功投递后才 ack |
| duplicate delivery | 幂等，不重复创建 session |
| adapter event | thread key/chat_id/message_id 正确 |

### 7.2 Hermes fixture

- 一条 gateway；
- 两个 profile（`default` + `coder`）；
- 每 profile 安装 plugin；
- 每 profile 一个地址；
- 发送两封邮件；
- 断言：
  - default 的 session 只收到 default 地址；
  - coder 的 session 只收到 coder 地址；
  - 两个 SSE 各自存在；
  - 无 `platforms.webhook` 端口；
  - 无跨 profile session。

### 7.3 真实宿主

- Hermes version/commit 固定；
- `gateway.multiplex_profiles` 默认；
- 检查 `/p/<profile>/` 路径（仅用于对照，不用于 AIMail）；
- 检查 SSE；
- 检查 thread/session；
- 检查 profile 删除/重启；
- 检查 ack 失败导致 pending 保留。

### 7.4 回归

- L0；
- `test_v2_contract_surface.py`；
- 现有 DSH/Pi/OpenClaw V2；
- advanced E2E。

---

## 8. 风险与开放问题

1. **Hermes 插件 platform API 版本差异**
   - `trusted_inbound`、`register_platform` 字段需要 feature-detect；
   - `MessageEvent`/`handle_message` 签名以宿主为准。

2. **profile context 获取**
   - 不能依赖 `HERMES_PROFILE_DIR` 全局变量；
   - 需要确认 `ctx`/manager 暴露的 profile home API；
   - 若没有公开 API，需 Hermes 提供。

3. **一个 host 一条 SSE 的产品要求**
   - 当前公共插件 API 不支持跨 profile 注入；
   - 需要 Hermes host 级 coordinator；
   - 本方案先采用 profile-scoped SSE，避免跨 profile 数据泄漏。

4. **adapter send**
   - AIMail 出站由 `send_mail` 工具完成；
   - adapter send 的语义需要与 Hermes 团队确认，避免重复发送。

5. **session 路由**
   - platform adapter 的 chat_id/thread 语义要能表达邮件 thread；
   - 需要验证 gateway 是否为每个 thread 建一个新 session；
   - 若 gateway 按 chat_id 复用一个 session，需把 chat_id 设为 thread key。

6. **profile 克隆**
   - 新 home ⇒ 新 AgentSystemId；
   - 旧 binding 不能静默复用；
   - 需要重新激活或显式迁移。

7. **legacy**
   - 旧 webhook 分支保留时间；
   - 不得让旧 webhook 与 V2 SSE 同时消费同一地址。

---

## 9. 结论

Hermes 最新架构确认是：

```text
one gateway process → serves every profile
```

这直接否定了当前 pysdk 的“每 profile 一个 webhook 端口”路径。

建议的 V2 Hermes 改造：

1. **删除 per-profile webhook 安装**；
2. **用 Hermes platform plugin adapter 取代 webhook replay**；
3. **每个 profile 一个 AgentSystemId + 一条 aggregate SSE**；
4. **SSE delivery 直接进入 adapter.handle_message，不再本地 HTTP replay**；
5. **地址激活继续由 `aimail_address` 完成，不依赖 CLI**；
6. **profile = 空间；地址只路由到本 profile**；
7. **若产品要求一个 host 一条 SSE，另立 Hermes host-level coordinator 项目**。

该方案不依赖 Hermes 源码 patch，不使用 AIMail CLI，不使用本地 webhook 端口，符合 multiplex 的官方边界。
