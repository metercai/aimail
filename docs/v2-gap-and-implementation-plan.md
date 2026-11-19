# AIMail V2 契约差距与分批次实施计划

依据：[`aimail-host-sse-contract-v2.md`](aimail-host-sse-contract-v2.md)  
范围：base gateway、advanced gateway、standalone、tssdk（DSH/OpenClaw/Pi）、测试夹具、发布与 legacy 路径  
目标：按批次可执行、可检查，不让未完成项进入 V2 发版。

---

## 0. 执行约定

### 0.1 固定概念

- 地址激活只有一种：`aimail_address` + activation prompt；
- 不区分系统模式，不由用户选择；
- `GatewaySystemId` 由 Gateway 在地址激活时返回，作为 binding 分组；
- `AgentSystemId` 由本地派生，作为 SSE 聚合身份；
- 每个 `(AgentSystemId, Gateway)` 一条 SSE；
- CLI/pysdk 不在 V2 主路径；只做 legacy 维护。

### 0.2 每个批次的完成定义

```text
1. 代码/文档已合并
2. 对应单测/集成测试通过
3. L0 不回归
4. 该批次验收清单全部勾选
5. 有可复现命令与证据
6. 未完成项明确列出，不隐藏
```

### 0.3 公共门禁

```bash
# 在仓库根目录执行
cd /Users/metercai/Desktop/chains/aimail

# 全量 L0
bash tests/sdk/l0-gate-tests.sh

# 契约单源
python3 tests/contract/check-contract-single-source.py

# 本地真实 gateway L2（当前脚本支持 pi/openclaw；dsh 在批次 F 补齐）
./tests/host-acceptance/run-l2-local-gateway.sh pi
./tests/host-acceptance/run-l2-local-gateway.sh openclaw
```

---

## 1. 当前差距（代码快照）

| 组件 | 现状 | V2 缺口 |
|---|---|---|
| base gateway | `apply-system` 仍存在；地址申请要求 webhook | 无 webhook 地址无法申请/兑换；页面有系统码入口 |
| advanced | `ApplyAddressRequest` 目前以 shared_domain 为主 | 缺统一三类地址申请页；缺独享域/系统标识名表单 |
| standalone | 共享域/独享域边界需按版本收窄 | 需只保留独享域地址申请 |
| gateway/advanced | 无 AgentSystemId | 无法按客户端实例聚合多地址 |
| tssdk | 仍有 `aimail_system`；SSE 按 system/address | 需唯一 `aimail_address`；需按 `(AgentSystemId,Gateway)` 聚合 |
| tssdk binding | `system_id` 已用于目录；无 `agent_system_id` | 需补客户端身份；明确 GatewaySystemId 分组 |
| DSH | hub 按 system 流 | 需 AgentSystemId 聚合 |
| OpenClaw | 工具面/manifest/工厂 names 仍是风险点 | 需自包含入口、16 工具、names 元数据 |
| Pi | 单地址已收窄 | 需拒绝第二地址并有测试 |
| 宿主安装器 | 只安装邮件 SKILL | 需安装 activation SKILL 为独立 skill |
| 测试 | 本地夹具已能跑三宿主 L2 | 缺无 webhook、多 Gateway、AgentSystemId 隔离 |
| CLI/pysdk | 旧 self-setup 仍在 | 需从 V2 主路径断开 |

---

## 2. 批次总览

| 批次 | 主题 | 主要组件 | 阻塞关系 |
|---|---|---|---|
| A | base 地址激活解阻 + 申请页 | base gateway | V2 第一阻断 |
| B | advanced/standalone 地址申请页 | advanced/standalone | 依赖 A 的 prompt/兑换语义 |
| C | AgentSystemId 协议与聚合 | gateway/advanced/tssdk | 依赖 A/B 地址激活 |
| D | tssdk 核心 + DSH | tssdk/DSH | 依赖 C 协议 |
| E | OpenClaw/Pi + SKILL | tssdk/宿主 | 依赖 D 公共层 |
| F | 生命周期 + 多 Gateway + L2 | tssdk/夹具 | 依赖 D/E |
| G | L3、发布、legacy 断开 | 全链路 | 依赖 F |

---

## 3. 批次 A — base gateway 地址激活解阻 + 申请页

### A.1 目标

- 无 webhook 地址也能申请/兑换地址激活码；
- 首次 `/apply` 地址的 `bind(code)` 必须真正兑换；
- base 申请页只保留地址申请；
- 地址 domain 只能从当前 gateway 支持的 domains 选择；
- 下发统一 activation prompt。

### A.2 任务

| ID | 文件/区域 | 实施 |
|---|---|---|
| A-01 | `aimail-gateway/src/core/api/activation.rs` | 移除“address has no webhook routing configured”作为地址申请/兑换前置；拉取地址允许空 webhook |
| A-02 | 同上 | 确保地址激活返回 `GatewaySystemId`、地址凭据、manager（如适用） |
| A-03 | 同上 | 修复 fresh `/apply` 地址误判 `already_bound`：有 code 且无匹配 active binding 时必须走 Gateway |
| A-04 | `/apply` HTML/JS | 删除 “Claim system code”和 `apply-system` 公网入口 |
| A-05 | `/apply` HTML/JS | 地址 domain 选择器绑定 `current_system_domains`，禁止手填 |
| A-06 | 邮件模板 | 使用统一 activation prompt（见 Appendix A） |
| A-07 | 测试夹具 | 增加无 webhook 地址申请/兑换用例 |

### A.3 实施步骤

1. 先加失败测试：
   - 创建无 webhook 地址 → `/apply-address` 当前返回 412；
   - fresh 地址 → `aimail_address(bind)` 当前可能 `already_bound`。
2. 修改申请/兑换条件：只校验地址存在、域 active、code 归属，不要求 webhook。
3. 修改页面：删除系统码 section；domain 来自 API。
4. 更新邮件模板并断言正文。
5. 跑 L0 + base gateway 单测 + 无 webhook L2。

### A.4 验收

```text
[ ] base /apply 无系统码入口/按钮
[ ] 地址 domain 只能选当前支持域
[ ] 无 webhook 地址可申请 code
[ ] 同一 code 可被 aimail_address 兑换
[ ] fresh 地址 gateway_calls >= 1，不出现 already_bound
[ ] 邮件 prompt 与 Appendix A 一致
[ ] L0 PASS
```

---

## 4. 批次 B — advanced/standalone 统一申请页

### B.1 目标

advanced 一个地址申请页支持三种地址形式；standalone 只保留独享域。

### B.2 三种地址形式

| 类型 | 用户输入 | Gateway 归属 |
|---|---|---|
| 共享域地址 | 共享域 + agentname | 现有 shared domain 的宿主系统 |
| 带系统标识名的共享域地址 | 共享域 + 系统标识名 + agentname | 按 advanced 既有系统标识名规则 |
| 独享域地址 | 独享域 + agentname | 对应独享 system/domain |

### B.3 任务

| ID | 组件 | 实施 |
|---|---|---|
| B-01 | advanced API | 扩展 `ApplyAddressRequest`/页面接口，支持三种类型 |
| B-02 | advanced API | 校验系统标识名、独享域归属、agentname 规则 |
| B-03 | advanced 页面 | 一个表单、类型切换、域/系统名联动 |
| B-04 | advanced 邮件 | 统一 activation prompt；不出现 system_code/manager 参数 |
| B-05 | standalone | 只保留独享域表单；共享域/系统名拒绝 |
| B-06 | 测试 | 三种类型各生成 code，均可用 aimail_address 兑换 |

### B.4 实施步骤

1. 先定义三种请求字段和地址归属结果；
2. 页面按类型显示最小字段，避免用户看到系统模式概念；
3. 统一调用地址申请 API，不在页面生成 system code；
4. 邮件 prompt 走同一模板；
5. 对 standalone 做能力开关/编译/配置收窄。

### B.5 验收

```text
[ ] advanced 只有一个地址申请入口
[ ] 三种类型都能出地址激活码
[ ] 独享域不落到 shared_addr_*
[ ] 带系统标识名的共享域归属正确
[ ] standalone 拒绝共享域/系统名
[ ] 三种邮件 prompt 完全一致
[ ] advanced/standalone 测试通过
```

---

## 5. 批次 C — AgentSystemId 协议与聚合

### C.1 目标

- 本地生成稳定 AgentSystemId；
- Gateway/advanced 注册并授权；
- 只聚合该 AgentSystemId 激活/绑定的地址；
- 多 Gateway 独立；
- SSE 认证/ack 可按 AgentSystemId 隔离。

### C.2 协议最小集（实现前先冻结 ADR）

必须定义：

```text
1. AgentSystemId 的派生算法/测试 seam
2. 注册/刷新接口
3. SSE 认证头或凭据
4. pending 查询的 AgentSystemId 过滤
5. ack 的归属校验
6. 地址 ↔ AgentSystemId 归属表
7. 旧地址/旧 key 的兼容策略
```

### C.3 任务

| ID | 组件 | 实施 |
|---|---|---|
| C-01 | tssdk core | AgentSystemId 派生函数、稳定性测试、home/硬件变化测试 |
| C-02 | base gateway | 注册/归属/聚合查询/ack 校验 |
| C-03 | advanced | 同 C-02，复用 base 存储/协议 |
| C-04 | tssdk | binding 增加 `agent_system_id`；生成/复用 |
| C-05 | 测试 | 两个 AgentSystemId 隔离；同 Agent 多地址聚合；多 Gateway 独立 |
| C-06 | 迁移 | 旧 binding 无 agent_system_id 的补写规则（不调用 Gateway） |

### C.4 验收

```text
[ ] 同机同 home 两次生成相同；换 home/指纹不同
[ ] Agent A 不能拉 Agent B 的 pending
[ ] 同 Agent 多地址出现在同一聚合 batch
[ ] 两个 Gateway 的 pending/ack 不混
[ ] 旧 binding 启动可补字段且不误路由
[ ] 单测 + 集成测试通过
```

---

## 6. 批次 D — tssdk 核心 + DSH

### D.1 目标

- 唯一 `aimail_address`；
- binding 目录按 GatewaySystemId；
- SSE 按 `(AgentSystemId,Gateway)` 聚合；
- DSH 多地址/多 Gateway 路由可用；
- 无 workspace miss ack。

### D.2 任务

| ID | 组件 | 实施 |
|---|---|---|
| D-01 | tssdk | 移除独立 `aimail_system` 工具和 groups |
| D-02 | tssdk | `aimail_address` 只接受 address code；无 system_code/manager |
| D-03 | tssdk | binding 必填 `agent_system_id`；目录按 `GatewaySystemId` |
| D-04 | tssdk | SSE 聚合层：一个 `(AgentSystemId,Gateway)` 一条连接；按 recipient 路由 |
| D-05 | DSH | hub 接入聚合层；workspace/session 路由；启动 read-on-boot |
| D-06 | DSH | workspace miss 不 ack；pause/unbind 从聚合移除 |
| D-07 | 测试 | DSH 单地址/多地址/多 Gateway/重启 |

### D.3 实施步骤

1. 先移除工具面上的 system，跑现有 host tests；
2. 给 binding 加字段，旧 binding 补写；
3. 实现聚合 SSE，保留 per-address fallback 仅作为过渡；
4. DSH hub 切到聚合层；
5. 用本地夹具验证；
6. 删除旧 per-system 分支。

### D.4 验收

```text
[ ] DSH 工具面无 aimail_system
[ ] binding 含有 agent_system_id
[ ] 目录路径 systems/<GatewaySystemId>/<address>
[ ] 同 Gateway 多地址只有一条 SSE
[ ] 两个 Gateway 两条 SSE
[ ] workspace miss 不 ack/留 pending
[ ] L0 + DSH host tests + DSH L2 PASS
```

---

## 7. 批次 E — OpenClaw / Pi / SKILL 安装

### E.1 OpenClaw

| ID | 实施 |
|---|---|
| E-01 | 插件入口自包含，不依赖镜像解析 `openclaw` |
| E-02 | manifest `contracts.tools` 等于实际工具集合（含 aimail_address） |
| E-03 | `registerTool` 工厂返回数组时提供 `names` 元数据 |
| E-04 | agentId 作为 workspace；绑定 workspace_path |
| E-05 | sessionKey 仅在 `hooks.allowRequestSessionKey=true` 且前缀匹配时发送 |
| E-06 | 测试：runtime names / 会话 tools / bind / turn |

### E.2 Pi

| ID | 实施 |
|---|---|
| E-07 | 单地址：第二地址拒绝 |
| E-08 | 无 `aimail_system` |
| E-09 | 地址绑定 cwd；pull 按当前地址 |
| E-10 | 测试：单地址激活、welcome、identity calibration |

### E.3 SKILL 安装

| ID | 实施 |
|---|---|
| E-11 | 安装器把 activation SKILL 安装为独立 `agentmail-activation` |
| E-12 | 不覆盖邮件 SKILL；board SKILL 预留 |
| E-13 | 测试：skill 目录存在、触发词 `aimail_address` |

### E.4 验收

```text
[ ] OpenClaw inspect runtime names 含 aimail_address
[ ] 会话 tools 含 aimail_address
[ ] 插件加载无需 host package link
[ ] Pi 第二地址失败
[ ] activation SKILL 独立安装
[ ] 三宿主 L0/host tests PASS
```

---

## 8. 批次 F — 生命周期 + 多 Gateway + L2

### F.1 生命周期任务

| ID | 行为 | 验收 |
|---|---|---|
| F-01 | pause | 从聚合移除，不 ack |
| F-02 | resume | 重新纳入并 drain |
| F-03 | unbind | 停路由/聚合/pointer；远端 key 不自动删 |
| F-04 | move workspace | 同 AgentSystemId 不换 code/不新建 SSE |
| F-05 | 跨 Gateway/AgentSystemId | unbind + 重新激活 |
| F-06 | home/硬件变化 | 新 AgentSystemId，旧 binding 失效 |

### F.2 夹具/测试任务

| ID | 内容 |
|---|---|
| F-07 | 夹具支持无 webhook 地址 |
| F-08 | 夹具支持 AgentSystemId 注册/隔离 |
| F-09 | 夹具支持两个 Gateway |
| F-10 | L2 覆盖 S0–S10 混合态 |
| F-11 | 安全：raw key/code 不进日志；外部预写不算激活 |

### F.3 必测用例

```text
[ ] 单 Gateway 多地址共享一条 SSE
[ ] 两 Gateway 各自 SSE，不串 pending/ack
[ ] Agent A/B 隔离
[ ] 同 thread followup / 新 thread 新 session
[ ] workspace miss 不 ack
[ ] pause/resume/unbind/move
[ ] 重启 read-on-boot
[ ] 混合地址/系统
```

---

## 9. 批次 G — L3、发布、legacy 断开

### G.1 L3

- 真实宿主内 prompt：安装/确认 `aimail_address`；
- 绑定 → identity calibration；
- welcome/manager 回复；
- stub/真实 turn 含唯一串；
- ack 在 turn 之后；
- 三宿主核心用例；
- 多 Gateway 核心用例。

### G.2 发布

```text
1. base gateway
2. advanced gateway
3. standalone（如独立发布）
4. tssdk 新版本
5. 确认 activation SKILL 随包
6. 公告：CLI/pysdk 为 legacy，不在 V2 主路径
```

### G.3 CLI/pysdk 断开

- V2 主路径禁止 `aimail install`、`agent-install.sh`、旧 self-setup；
- pysdk/CLI 旧测试单独标记 legacy；
- 旧宿主固定旧版本；
- 不删除旧代码，但不再被新流程引用；
- 文档只保留 V2 activation prompt + activation SKILL。

### G.4 发布前检查清单

```text
[ ] C1–C13（V2 契约）覆盖
[ ] L0 PASS
[ ] 三宿主 L2 PASS
[ ] L3 核心 PASS
[ ] 无 raw key/code 日志
[ ] binding 无 session_id/无旧 system 工具
[ ] activation SKILL 独立存在
[ ] 版本/变更记录/回滚方案
```

---

## 9.1 执行证据（滚动）

### 批次 A — 部分完成

- base gateway 去掉无 webhook 申请前置；
- `/apply` 删除系统码入口/按钮，地址域选择当前 domains；
- 地址邮件 prompt 与 V2 一致；
- 地址-only 本地 L2 通过。

证据：

```text
gateway: bda201c feat(activation): allow pull addresses and remove system claim UI
gateway: 4252839 fix(activation): silence unused manager warning
aimail:  38aa1eb test(v2): exercise address-only pull activation
aimail:  6f039c1 test(l2): address-only fixture for pi/openclaw
gateway: 3f8c44a refactor(activation): drop dead system apply handler
cargo test --lib: 401 passed
L2 pi: PASS
L2 openclaw: PASS
L0: PASS
```

未完成：

- fresh `/apply` 的 `already_bound` 场景仍需复现与回归；
- advanced/standalone 三类地址申请页未完成。

### 批次 C/D/E — 部分完成

- 新增本地 AgentSystemId 派生与测试；
- binding 写入 `agent_system_id`；
- DSH/OpenClaw 工具面收敛为 15 个，移除 `aimail_system`；
- activation SKILL 在 DSH/Pi/OpenClaw 启动时独立安装；
- 测试通过。

证据：

```text
aimail: 74ae2ee feat(v2): add AgentSystemId and address-only tool surface
aimail: 3779e8e test(skill): verify separate activation skill install
vitest agent-aimail: 461 passed | 1 skipped
```

未完成：

- Gateway/advanced 尚未按 AgentSystemId 聚合/授权；
- tssdk 仍按 system/address 建流，未接 `(AgentSystemId,Gateway)`；
- 多 Gateway/多地址 L2 未建立；
- DSH L2 仍走旧 system 路径。

### 批次 B — 完成（页面/API 层面）

- advanced 统一地址申请页支持共享域、带系统标识名共享域、独享域；
- 新增 `/api/v1/public/address-domains` 独享域列表；
- standalone 强制 dedicated-only；
- advanced 激活邮件统一 prompt。

证据：

```text
advanced: 328dd74 feat(email): use unified address-only activation prompt
advanced: d9cee59 feat(apply): unify shared, system-name, and dedicated address applications
advanced: 16118c5 feat(app): standalone only applies for dedicated addresses
cargo check: PASS
open_apply tests: 7 passed
email_notify tests: 3 passed
```

未完成：

- 三类地址端到端 L2（需 email verification/fixture）；
- 独享域配额/审计策略仍需产品确认。

### 批次 B/C/F — 本轮新增

- advanced 统一三类地址页面/API、独享域列表、standalone dedicated-only；
- `aimail_address` 增加 `move`，DSH 增加 `unbind`；
- 激活请求携带 `X-Agent-System-Id`，为 Gateway 聚合铺路；
- 工具 spec/DSH schema/测试同步。

证据：

```text
advanced d9cee59 / 16118c5
aimail   badf320 feat(address): add move and DSH unbind lifecycle
aimail   4e6d6be style(spec): preserve compact tool-spec formatting
aimail   5c07c94 test(address): cover move schema and DSH unbind
aimail   ad39e29 feat(activation): carry AgentSystemId on address activation
L0: PASS
```

未完成：

- Gateway/advanced 尚未落库 AgentSystemId 归属；
- 聚合 SSE/ack 未实现；
- 三类地址端到端 L2。

### 批次 C — 起步

- tssdk 在地址激活时发送 `X-Agent-System-Id`；
- base gateway 新增 `agent_system_addresses` 归属表；
- 地址激活成功后写入 `AgentSystemId ↔ address ↔ GatewaySystemId`；
- 为下一步聚合 pending/ack 提供数据基础。

证据：

```text
gateway b8b7e64 feat(agent-system): record address ownership by AgentSystemId
aimail  ad39e29 feat(activation): carry AgentSystemId on address activation
cargo test activation: 11 passed
L0: PASS
```

未完成：

- advanced 归属写入；
- 按 AgentSystemId 的聚合 pending/ack；
- 聚合 SSE；
- A/B 隔离与多 Gateway L2。

### 批次 C — 聚合接口起步

- base gateway 新增：

```text
POST /api/v1/agent-system/pending
POST /api/v1/agent-system/pending/ack
```

- 只允许地址 key 且该地址确属 `X-Agent-System-Id` 时访问；
- storage 提供地址集合 pending/ack；
- advanced 激活也写入 AgentSystemId 归属。

证据：

```text
gateway d4eab17 feat(agent-system): add aggregate pending and ack endpoints
gateway e3589d6 feat(agent-system): add aggregate SSE stream
advanced d780769 feat(agent-system): record advanced address ownership
gateway http tests: 51 passed
L0: PASS
```

未完成：

- ~~SSE 聚合端点~~（已加 `GET /api/v1/agent-system/pending/stream`）；
- tssdk 切换到聚合接口；
- A/B 隔离与多 Gateway L2。

### 批次 C/D — 聚合消费起步

- `GatewayClient` 支持自定义 SSE/pending/ack path 和额外签名头；
- `startSystemPull` 支持 `agentSystemId`，自动使用：
  - `/api/v1/agent-system/pending/stream`
  - `/api/v1/agent-system/pending`
  - `/api/v1/agent-system/pending/ack`
- DSH hub 改为每个 Gateway 一条 AgentSystemId 流。

证据：

```text
aimail 849c3c9 feat(agent-system): wire aggregate transport into tssdk
vitest core+dsh: 401 passed | 1 skipped
L0: PASS
```

已验证：

- 兼容激活路由写入归属（`94f81ce`）；
- Pi 聚合 SSE L2：PASS；
- OpenClaw 聚合 SSE L2：PASS；
- A/B pending/ack 隔离测试：PASS（`614c420`）。

仍待：

- DSH 聚合 L2（当前 L2 脚本未支持 dsh）；
- 多 Gateway 聚合 L2；
- 多地址同一 AgentSystemId 的聚合 L2（当前每宿主单地址）。

### 生产 Pi 聚合

- Pi B2（无 system）改用一条 AgentSystemId 聚合流；
- 单地址模式，不 fanout；
- 客户端使用 aggregate SSE/pending/ack path。

证据：

```text
aimail d897736 feat(pi): use aggregate stream for B2 address
vitest pi: 3 passed
Pi/OpenClaw aggregate L2: PASS
```

### 生产 OpenClaw 聚合

- OpenClaw B2（无 system）改用一条 AgentSystemId 聚合流；
- 首个绑定地址作为认证 key，Gateway 按 AgentSystemId 授权其余地址；
- 单地址/多地址均走同一 aggregate 端点。

证据：

```text
aimail b7d436c feat(openclaw): use aggregate stream for B2 address
vitest openclaw: 44 passed
```

### DSH 聚合 L2

- DSH B2 不再要求 shared-domain scope，任意地址码都可绑定；
- DSH 特定激活路径传入 AgentSystemId；
- DSH L2 使用 address-only + 聚合 hub + welcome API；
- 结果：PASS（1 stream，followup 1）。

证据：

```text
aimail 7d8f666 feat(dsh): persist AgentSystemId and add aggregate L2
aimail 0bf9d46 test(dsh): align B2 address-only and L2
L2 dsh: PASS
L2 pi: PASS
L2 openclaw: PASS
L0: PASS
```

### 同 Gateway 双地址聚合 L2

- OpenClaw 在同一 Gateway 激活 alice/bob 两个地址；
- 两个地址属于同一 AgentSystemId；
- 一条聚合 SSE 同时收到两封 welcome；
- 两条 delivery 都路由到 workspace；
- 结果：PASS（seen=2）。

证据：

```text
aimail 14b941d test(l2): two-address aggregate delivery
L0: PASS
```

### DSH 双地址聚合 L2

- DSH 同一 Gateway 激活 alice/bob；
- 同一 AgentSystemId；
- 一条聚合流（`1 gateway stream from 2 bindings`）；
- 两封 welcome 各生成一个 followup；
- 结果：PASS（followups=2）。

证据：

```text
aimail f77e76e test(l2): DSH two-address aggregate delivery
L2 dsh: PASS
L2 pi: PASS
L2 openclaw: PASS
```

### 跨 Gateway 隔离

新增两数据库测试：

```text
agent_system_pending_is_isolated_across_gateway_databases
```

- 同一 AgentSystemId、同一地址，在两个 Gateway DB 中各自 pending；
- A 的聚合查询只返回 A；
- B 的聚合查询只返回 B；
- A ack 自己的行不影响 B。

证据：

```text
gateway 58df8cd test(agent-system): isolate pending across gateways
L0: PASS
```

### 两 Gateway 真实 L2

- 启动两个真实 base gateway；
- 同一 AgentSystemId 在两个 Gateway 各激活一个地址；
- 每个 Gateway 一条 aggregate SSE；
- 两个 Gateway 各自返回自己的 welcome；
- 结果：PASS（seen=2，互不串信）。

运行：

```bash
./tests/host-acceptance/run-l2-two-gateways.sh
```

证据：

```text
aimail <two-gw> test(l2): real two-gateway AgentSystemId delivery
L0: PASS
```

### 已知环境问题

- advanced `cargo test` 在本机有 9 个 machine_code 测试失败，
  原因是 macOS 无 `/etc/machine-id`，与本次邮件改动无关；
  需在 Linux CI 复验。

---

## 10. 依赖与风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| AgentSystemId 协议未定 | C/D 批次无法开工 | 先做 C-ADR，1 页冻结 |
| 无 webhook 激活改动 | 旧推送安全假设变化 | 仅允许拉取地址；保留推送校验 |
| 多 Gateway 重复投递 | 重复 turn/ack | 幂等 key = gateway+message/thread |
| OpenClaw 工具面 | 工具不出现 | manifest/names/自包含入口三件套 |
| Pi 无后台 session | 多地址不可行 | 单地址 profile；拒绝第二地址 |
| CLI/pysdk 断开 | 旧宿主失效 | legacy 分支/版本固定 |
| SKILL 安装器未覆盖 | Agent 不知道激活流程 | E-11/E-12；测试 skill 目录 |

---

## 11. 批次执行顺序（最短路径）

```text
A → B → C → D → E → F → G
```

每批次完成后写一条 evidence：

```text
批次 X:
- commits:
- tests:
- acceptance:
- known gaps:
```

没有 evidence 的批次不进入下一阶段。
