# AIMail V2 契约差距与实施计划

依据：[`aimail-host-sse-contract-v2.md`](aimail-host-sse-contract-v2.md)  
范围：base gateway、advanced gateway、tssdk 三宿主、测试夹具、发布流程  
原则：先让 V2 主路径可测可发；CLI/pysdk 旧路径从主路径断开，不阻塞 V2。

---

## 0. 结论

当前代码已经具备：

- 地址码直接绑定（tssdk `aimail_address`）；
- GatewaySystemId 作为 binding 的 `system_id`；
- DSH/OpenClaw/Pi 的 SSE/拉取基础；
- 本地真实 gateway + SMTP 夹具；
- 地址激活 SKILL 文件和真实邮件 prompt。

V2 尚缺的**关键闭环**：

1. Gateway/Advanced 允许“无 webhook 的拉取地址”申请/兑换激活码；
2. AgentSystemId 的生成、注册、认证与多地址聚合；
3. tssdk 按 `(AgentSystemId, Gateway)` 建一条 SSE，去掉按地址/系统分散建流；
4. binding 增加 `agent_system_id`，目录按 GatewaySystemId 分组；
5. 唯一工具面 `aimail_address`，移除独立 `aimail_system`；
6. 宿主安装器安装 activation SKILL；
7. 真实多 Gateway/多地址/混合态测试；
8. CLI/pysdk 从 V2 主路径断开。

---

## 1. 差距矩阵

| # | 组件 | 差距 | 证据 | 严重度 |
|---|---|---|---|---|
| G-01 | base gateway | 无 webhook 地址无法申请激活码；拉取地址被 412 | `aimail-gateway/src/core/api/activation.rs` webhook 检查 | 阻断 V2 |
| G-02 | base gateway | 地址激活邮件 prompt 刚更新，需回归测试 | `activation.rs` 地址模板 | 中 |
| G-03 | base gateway | 无 AgentSystemId 注册/认证/聚合 | 代码中无 AgentSystemId | 阻断 V2 |
| G-04 | base gateway | 新 `/apply` 地址可能返回 `already_bound` 且不兑换 code | QA G12 观察 | 高 |
| A-01 | advanced | 地址申请/兑换需验证无 webhook 拉取 | `aimail-advanced/src/advanced/api/*` | 阻断 V2 |
| A-02 | advanced | 无 AgentSystemId 聚合 | 代码中无 | 阻断 V2 |
| A-03 | advanced | 地址激活邮件 prompt 未对齐 V2 | advanced 邮件模板 | 中 |
| T-01 | tssdk | 仍有独立 `aimail_system` 工具 | `src/dsh/system.ts`、`src/openclaw/tools.ts` | 高 |
| T-02 | tssdk | binding 无 `agent_system_id` | `src/core/address-code.ts`/config | 高 |
| T-03 | tssdk | SSE 按 system/address 起流，不按 AgentSystemId | `src/dsh/mail-hub.ts`、`src/core/system-pull.ts` | 阻断 V2 |
| T-04 | tssdk | 无 AgentSystemId 派生/注册 | 代码中无 | 阻断 V2 |
| T-05 | tssdk | 目录已按 `system_id` 分组，但需明确 GatewaySystemId | binding/目录 | 中 |
| T-06 | tssdk | 解绑/迁移动作不完整 | `aimail_address` 仅部分动作 | 中 |
| T-07 | tssdk | activation SKILL 未由宿主安装器安装 | `resources/skills/activation` vs host installers | 中 |
| H-01 | DSH | 多地址/多 Gateway 需重构 hub 聚合 | `mail-hub.ts` | 高 |
| H-02 | OpenClaw | 工具面/工厂 names、AgentSystemId、sessionKey 条件 | `openclaw.plugin.json`、`tools.ts` | 高 |
| H-03 | Pi | 单地址能力已收窄，需确保拒绝多地址 | `src/pi/tools.ts` | 中 |
| Q-01 | 测试 | 夹具没有 AgentSystemId/多 Gateway/无 webhook 拉取 | `tests/host-acceptance` | 高 |
| Q-02 | 测试 | L3 仍用外部/旧路径，未验证 V2 激活 prompt | QA G8/G9/G10 | 高 |
| L-01 | CLI/pysdk | 旧 CLI/system 指引与 V2 主路径混杂 | `docs/agent-self-setup.md`、pysdk | 中 |

---

## 2. 目标架构落地顺序

```text
Phase 1  Gateway/Advanced 地址激活解阻
Phase 2  AgentSystemId 协议与聚合授权
Phase 3  tssdk binding / SSE / 地址唯一入口
Phase 4  三宿主适配与 SKILL 安装
Phase 5  夹具、L2/L3、回归与发版
Phase 6  CLI/pysdk 主路径断开（legacy）
```

每个 Phase 完成后必须保持已有测试可运行；不允许“等全部做完再验证”。

---

## 3. Phase 1 — Gateway / Advanced 地址激活

### 3.1 G-01 允许拉取地址申请激活码

**目标**：地址不需要 webhook 也能申请/兑换 activation code。

实施：

- 去掉 `webhook_url` 非空作为申请激活码的前置条件；
- 只在“推送模式”要求 webhook；拉取模式允许空；
- 申请响应/邮件仍包含地址、Gateway URL、code；
- 兑换后创建 agent-scope key；
- 保持权限/一次性/过期语义。

测试：

- 新建无 webhook 地址，`/apply` 申请 code；
- tssdk `aimail_address` 兑换成功；
- 重启后 pull 能取到欢迎邮件；
- 有 webhook 的旧地址行为不回归。

验收：

```text
POST /apply-address (pull address) → code
aimail_address(bind) → success + agent key
```

### 3.2 G-04 `already_bound` / code 未兑换

**目标**：全新 `/apply` 地址的首次 `bind(code)` 必须兑换，不得被本地旧 binding 短路。

实施：

- 检查 `aimail_address` 的 `already_bound` 条件：
  - 仅在 active binding 的 email 与请求 email 完全一致、且凭据有效时短路；
  - 不得因 pointer/旧地址/system 扫描命中；
  - 有 code 时，如果 binding 不是该 code 对应地址，必须走 gateway；
- 清理 `/apply` 预写或测试夹具写入的 binding；
- 增加回归：fresh code + empty home → gateway_calls ≥1。

### 3.3 G-02/A-03 激活邮件 prompt

**目标**：base/advanced 都下发同一 V2 prompt，不使用 SKILL URL。

文本：

```text
Activate and bind my AIMail mailbox address {address} with activation code {code} at gateway {gateway_url} using `aimail_address`. If `aimail_address` is unavailable or not registered, install the `agent_aimail` plugin SDK first: from PyPI via pip for Python, or from npm for Node.js. Then retry `aimail_address` with the same parameters.
```

实施：

- base 已更新；
- advanced 对齐同一模板；
- 邮件只包含地址、code、gateway、安装提示；
- 不含 system_code、manager 参数、raw key、SKILL URL。

测试：

- 两种 gateway 的邮件正文逐字匹配；
- 用户复制整段可直接触发。

---

## 4. Phase 2 — AgentSystemId 与聚合授权

### 4.1 T-04 本地生成 AgentSystemId

**目标**：本地稳定、全局唯一的客户端 ID。

实施：

- 新增 `AgentSystemId` 生成器：
  - 输入：agent home、机器硬件指纹；
  - 输出：稳定 ID（不可含 secret）；
  - 同机同 home 重复生成一致；
  - 提供测试 seam 以固定指纹。
- 持久化只作为缓存，不能改变派生结果；
- home/硬件变化 → 新 ID。

测试：

- 两次生成一致；
- 改 home/指纹 → 不同；
- 不写入 binding 的 session。

### 4.2 G-03/A-02 Gateway 注册与授权

**目标**：Gateway 记录 AgentSystemId，并只授权它聚合自己激活的地址。

实施：

- 新增 AgentSystemId 注册/刷新接口；
- 地址激活时携带 AgentSystemId；
- Gateway 建立 `AgentSystemId ↔ address` 归属；
- SSE 认证使用 AgentSystemId 凭据；
- pending 查询按归属过滤；
- 地址新增/暂停/解绑更新授权；
- 不同 AgentSystemId 不串信。

测试：

- Agent A 不能拉取 Agent B 的地址；
- 同一 Agent 多地址聚合正确；
- 多 Gateway 各自独立。

### 4.3 Gateway 协议设计点（需在实现前定稿）

- 注册接口和凭据形式；
- SSE 认证头/身份；
- 聚合查询参数；
- ack 是否带 AgentSystemId；
- 地址归属表结构/迁移；
- 旧地址/system key 兼容。

测试先行：用两个假 AgentSystemId 验证隔离。

---

## 5. Phase 3 — tssdk 核心改造

### 5.1 T-01 唯一 `aimail_address`

实施：

- 从 DSH `system.ts` 移除独立工具注册；
- 从 OpenClaw `tools.ts` 移除 `aimail_system`；
- 从 toolset/group 移除独立 system；
- `aimail_address` 参数只保留：
  ```ts
  action, email, code?, gateway_url?, workspace_path?
  ```
- 不接收 `system_code`/manager/domain/system_name；
- 保留 `role_calibrator` 作为邮件工具，不被误认为激活工具。

测试：

- 三宿主工具面没有 `aimail_system`；
- `aimail_address` 可完成绑定；
- SKILL 不需要 system 工具。

### 5.2 T-02 binding 增加 agent_system_id

实施：

- binding schema 增加 `agent_system_id`；
- 目录继续按 `system_id`（GatewaySystemId）分组；
- B2 也有 GatewaySystemId；
- 禁止 session_id；
- 旧 binding 启动时补 `agent_system_id`（仅本地派生，不调用 Gateway）。

### 5.3 T-03 SSE 聚合

实施：

- 按 `(AgentSystemId, gateway_url)` 维护一条连接；
- 连接内按 recipient 查 binding；
- 不再按地址/系统分别建流；
- 多 Gateway 各自连接；
- 断线重连恢复聚合；
- 无 binding/paused/workspace miss 不 ack。

测试：

- 同一 Gateway 多地址只建一条连接；
- 两个 Gateway 两条连接；
- Agent A/B 隔离；
- 重启恢复。

### 5.4 T-06 生命周期

实施：

- `pause/resume` 从聚合集合增删；
- `unbind` 停路由/聚合/pointer，远端 key 不自动删；
- `move` 更新 workspace，不换 code、不新建 SSE；
- 跨 Gateway/AgentSystemId 重新激活。

### 5.5 T-07 activation SKILL 安装

实施：

- 宿主安装器把 `resources/skills/activation/SKILL.md` 安装到独立
  `skills/agentmail-activation/`（或 host 等价目录）；
- 不覆盖邮件 SKILL；
- 工具名 `aimail_address` 作为 SKILL 触发；
- board SKILL 预留独立目录。

---

## 6. Phase 4 — 三宿主

### DSH

- workspace/Session 已具备；
- hub 改为 AgentSystemId 聚合；
- 删除 `main` 路径上的 system 工具；
- 启动读 binding + arm；
- 测试多地址、多 Gateway、混合。

### OpenClaw

- agentId 作为 workspace；
- `registerTool` 工厂返回数组时必须提供 `names` 元数据；
- 插件入口自包含，不依赖镜像解析 `openclaw`；
- manifest `contracts.tools` 必须是实际 16 个；
- sessionKey 仅在使用 `hooks.allowRequestSessionKey` 时发送；
- 测试 inspect runtime → 会话 tools → bind → turn。

### Pi

- 单地址；
- 无 `aimail_system`；
- 拒绝第二地址；
- 地址绑定 cwd；
- 测试单地址激活、welcome、身份校准。

---

## 7. Phase 5 — 测试/发布

### 7.1 夹具升级

`tests/host-acceptance/run-l2-local-gateway.sh`：

- 支持无 webhook 地址；
- 支持 AgentSystemId 注册；
- 两个 Gateway 夹具（多 Gateway）；
- 验证聚合只返回该 AgentSystemId 的地址；
- 不再用外部预写 binding 作为激活；
- 安装 activation SKILL（或模拟 host）。

### 7.2 L2 必测

| 用例 | 目标 |
|---|---|
| L2-1 | 无 webhook 地址申请/兑换 |
| L2-2 | 单 Gateway 多地址共享一条 SSE |
| L2-3 | 多 Gateway 各自 SSE |
| L2-4 | Agent A/B 隔离 |
| L2-5 | DSH/OpenClaw/Pi 工具面 |
| L2-6 | binding 目录按 GatewaySystemId |
| L2-7 | unbind/move |
| L2-8 | activation SKILL 安装与触发 |

### 7.3 L3 回归

- 真实宿主 prompt → 安装 → `aimail_address` → identity calibration；
- stub LLM turn 含唯一串；
- ack 在 turn 之后；
- 多 Gateway/多地址核心用例；
- 不复用旧 CLI/self-setup 路径。

### 7.4 发布顺序

1. base gateway（pull 地址激活 + AgentSystemId）；
2. advanced gateway；
3. tssdk（0.5.x）；
4. CLI 不参与 V2 主路径，仅 legacy；
5. L2/L3。

---

## 8. Phase 6 — CLI/pysdk 断开

目标：不阻塞 V2。

实施：

- `docs/agent-self-setup.md` 标记 legacy，不再被 V2 引用；
- `pysdk`/CLI 安装路径不进入 V2 验收；
- CLI 仍可服务旧宿主/旧版本；
- V2 宿主安装通过 host 插件/扩展 + activation prompt；
- 测试矩阵分开：
  - V2：tssdk + gateway fixture + prompt；
  - legacy：CLI/pysdk 旧测试，不阻塞 V2 发布。

禁止：

- 在 V2 主路径调用 `aimail install`；
- 用 CLI 写 binding；
- 用 `docs/agent-self-setup.md` 作为 V2 激活入口。

---

## 9. 任务清单（可直接建 issue）

| ID | 组件 | 任务 | 依赖 | 验收 |
|---|---|---|---|---|
| GW-01 | base | 允许无 webhook 地址申请 code | — | pull 地址 code 生成 |
| GW-02 | base | 修复 fresh bind 误 already_bound | GW-01 | gateway_calls≥1 |
| GW-03 | base | AgentSystemId 注册/聚合授权 | 协议定稿 | A/B 隔离 |
| AD-01 | advanced | 无 webhook 地址激活 | GW-01 | 同 base |
| AD-02 | advanced | AgentSystemId 聚合 | GW-03 | 同 base |
| AD-03 | advanced | 邮件 prompt 对齐 | — | 逐字一致 |
| TS-01 | tssdk | 移除 aimail_system | — | 三宿主无 |
| TS-02 | tssdk | agent_system_id binding | TS-01 | binding 含 |
| TS-03 | tssdk | 聚合 SSE | GW-03 | 一条/Gateway |
| TS-04 | tssdk | 生命周期 | TS-02 | pause/unbind/move |
| TS-05 | tssdk | 安装 activation SKILL | — | 独立 skill |
| H-DSH | host | hub 聚合 | TS-03 | 多地址/多 GW |
| H-OC | host | names/manifest/自包含 | — | runtime tools 16 |
| H-PI | host | 单地址拒绝 | — | 第二地址失败 |
| QA-01 | test | 夹具 pull/AgentSystemId | GW/AD | L2 |
| QA-02 | test | L3 in-host prompt | all | turn+ack |

---

## 10. 风险

- AgentSystemId 认证协议若设计过重，可能拖慢网关；建议先做最小注册/聚合表；
- 无 webhook 激活会改变旧安全假设，需要权限测试；
- 多 Gateway 聚合可能导致重复 delivery，需要幂等；
- Pi 单地址限制必须在 UI/工具层同时拒绝；
- activation SKILL 安装器尚未覆盖，需 host 适配；
- CLI/pysdk 断开后，旧宿主仍依赖旧包，必须保留 legacy 分支。

---

## 11. 实施步骤（按周/批次）

1. **批次 A**：GW-01/GW-02 + 夹具无 webhook；跑通 `aimail_address` 单地址。
2. **批次 B**：AgentSystemId 协议 + GW-03/AD-02 最小聚合；A/B 隔离测试。
3. **批次 C**：TS-01/TS-02/TS-03；DSH 先接入，保持 L0/L2。
4. **批次 D**：OpenClaw/Pi 适配；工具面/单地址测试。
5. **批次 E**：TS-04/TS-05；解绑迁移/SKILL 安装。
6. **批次 F**：QA-01/QA-02；多 Gateway L3；发版。
7. **批次 G**：CLI/pysdk legacy 断开与文档清理。

每批次结束条件：

```text
L0 PASS
对应 L2 PASS
无新增 raw key/session_id
新契约 C1–C13 子集通过
```
