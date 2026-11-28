# AIMail Host SSE Contract

状态：现行契约（**v2.6**，2026-11-26；v2.6 = 自举走**一个无 host 参数 URL**，**以 `uname -s` 能否执行为判据**分支（`?shell=sh` / `?shell=ps1`），不靠 agent 猜平台（`/api/v1/agent-setup/bootstrap`；OS/宿主/宿主环境/安装指令全在脚本内判定，prompt 对四者无感）；v2.5 = 锚点拉取改为**硬性**（§13.3，无"未登记放行"档）+ pi 锚点=整个 agent/C17 观测口径 + L2 三缺陷修复；v2.4 = C16 判定口径 + 锚点标识跨仓见证 + 本地端点退场；v2.3 修正 M 的硬性条件；v2.2 §9/§11 重写与正文/实例分离）
读者：Agent 宿主实现者、Gateway 实现者、QA
规范词：MUST / SHOULD / MAY 按 RFC 2119 解释

本文是一份完整、独立的契约。读者实现一个宿主只需本文 + 附 A（Gateway 实发的激活 prompt）即可。
所有断言以**代码**为准，出处标在句末；凡"宿主必须自己决定"的地方明确写出边界，不用含糊措辞掩盖。

**传输模型一句话**：以 SSE 为主、长轮询为回退（`auto` 探测降级），两者投递与 ack 语义相同；文件名里的
"SSE" 是历史名称，不代表只有 SSE。见 §8。

**正文与实例分离**：§1–§17 只写中立规则与义务，不点名任何具体宿主；具体宿主如何满足规则放在
**附 E（参考宿主）**与**附 F（参考实现状态）**。

> 变更记录见 **附 D**（21 条）：v2.1 修正与代码不符的 10 处；v2.2 重写 §9（三条映射边）与 §11
> （三层判定），并把实例移出正文。每类断言只保留一处，其它处改为指针。

---

## 1. 核心模型

```text
Gateway
  └── GatewaySystemId        # 地址在网关侧的系统/域归属（响应字段名：system_id）
        └── Address          # 邮件地址
              └── 空间       # 宿主侧容器：workspace / project / profile / 目录
                    └── Session
```

- 用户只做一件事：**用地址激活码完成地址绑定**；
- 地址属于哪个网关系统/域，由激活码在申请时确定，随激活响应返回；
- 本地不创建"系统管理配置"，不保存 admin/domain/raw key；
- SSE 聚合身份是另一个 ID：`AgentSystemId`（本地实例身份，§5）。

两个 ID 不可混用：

| ID | 归属 | 作用 | 线上字段名 |
|---|---|---|---|
| `GatewaySystemId` | Gateway | 地址归属；`.aimail` 目录分组键；域/manager/审计 | `system_id`（激活响应） |
| `AgentSystemId` | 本地 Agent 实例 | 聚合投递身份（请求头） | `X-Agent-System-Id` |

---

## 2. 用户安装流程

用户只需要理解两个东西：我的邮件地址；Gateway 发给我的**地址激活 prompt**。

```text
/apply 页面
  → 用户填地址（域/系统由部署侧决定）
  → Gateway 确定地址归属（system_id）并生成地址激活码（TTL 24h）
  → 邮件下发 activation prompt（原文见附 A）
  → 用户粘贴到 Agent 对话框
  → Agent 调用 aimail_address(action=bind, email, code, gateway_url)
  → 宿主 POST /api/v1/activate-address-code
  → 拿到 system_id + 地址凭据(raw_key) + expires_at + manager_address
  → 本地写 binding（§7.1）
  → 建立/更新该 (AgentSystemId, Gateway) 的聚合投递（§6、§8）
```

申请页的版本、入口与页面分类属于产品/部署实现，不是本契约的一部分。契约只约束：
最终下发的 prompt 内容（MUST 与附 A 一致）、Agent 收到 prompt 后的工具行为（§3）、绑定结果（§7）。

---

## 3. 地址激活

### 3.1 唯一工具

宿主 MUST 只暴露 `aimail_address`（参数集合由 `pysdk/tool-spec.json` 单一来源定义）：

```ts
{
  action: 'bind' | 'pause' | 'resume' | 'unbind' | 'move'
  email: string
  code?: string          // bind 必填
  gateway_url?: string    // 首次云端激活所需
  workspace_path?: string // move 必填
}
```

- MUST NOT 接受 `system_code`、`manager`、`system_name`、`domain`（`tests/sdk/test_v2_contract_surface.py`
  的参数面棘轮守护）；
- MUST NOT 接受 admin/domain/system 作用域密钥；
- MUST NOT 提供独立 `aimail_system` 工具。

> 网关侧另有一个**系统激活**端点（`POST /api/v1/activate-system`，发行 system 作用域密钥），供云侧部署
> 种码使用。宿主实现 MUST NOT 把它暴露成工具，也不得要求用户提供系统码；地址激活对宿主是唯一入口。

### 3.2 激活请求

`aimail_address(action=bind)` → 宿主 `POST {gateway_url}/api/v1/activate-address-code`：

```json
{ "code": "addr-…", "email_address": "lower-cased@example.com" }
```

- 请求头 MUST 带 `X-Agent-System-Id: asid_…`：网关**只在激活事务里**建立聚合归属，事后没有补报接口；
  漏带 ⇒ 该地址永远不会投给这个实例，唯一补救是重新激活（§5.2）；
- 该端点无鉴权：**激活码本身就是凭据**；
- 响应（成功）：`raw_key`、`system_id`、`email_address`、`expires_at`、`manager_address`、`kind`
  （`new` | `rotation`），另有 `success`。

本地随后 MUST 依次完成：

1. 写 binding（§7.1，字段见该表；`system_id` 即 GatewaySystemId）；
2. 记录本机 AgentSystemId 已随激活上报（网关侧已建立归属，本地不再补报，§5.2）；
3. 建立或复用到该 Gateway 的聚合投递（§6.1）；
4. 返回绑定结果给模型（含 `expires_at`，让 Agent 能提示续期窗口）。

### 3.3 失败

失败 MUST NOT 写 binding、MUST NOT 开始投递、MUST NOT ack：

| 情形 | 判定依据 |
|---|---|
| 码无效/过期/已用 | 410 |
| 码绑定到别的地址 | 403 |
| 地址归属系统未知（缺 `system_id`） | 响应无 `system_id` |
| 缺 `raw_key` | 响应无凭据 |
| 本机无法生成 AgentSystemId | 派生失败（§5.1） |
| 本机已有冲突 binding | 本地检查 |
| 网关限流 | 429（10 次失败/300 秒） |

宿主 MUST 把网关的 `error`/`detail` 原样上抛给模型，不得改写成"成功"或吞掉。

---

## 4. GatewaySystemId

地址在网关侧的系统/域归属，激活响应字段名为 `system_id`。

### 4.1 作用

- 标识地址属于哪个网关系统/域；
- 本地 `.aimail/systems/<system_id>/<address>/` 的稳定分组键；
- 网关侧审计、manager 解析、域策略；
- 多地址归属校验。

### 4.2 不作用

- 不是投递身份（投递身份是 AgentSystemId，§5）；
- 不是本地路由键，也不是 Session 路由键；
- 不要求本地存在 system 管理配置；
- 不要求本地保存 admin/domain/raw key。

### 4.3 稳定性

- 同一地址重新激活，`system_id` 应稳定；
- 地址迁移到另一系统/域时可变化，变化时 binding MUST 更新、目录 MUST 迁到新分组；
- 它比 AgentSystemId 更稳定：换机器/换 home 不会改变它。

---

## 5. AgentSystemId

### 5.1 定义（精确）

```text
AgentSystemId = "asid_" + hex( sha256("aimail-agent-system-v1\0" + resolve(agent_home) + "\0" + hardware_fingerprint) )[0..32]
```

- 纯函数：同机同 home 稳定可重算（`src/core/agent-system-id.ts`）；
- 硬件指纹取 `/etc/machine-id` 或 `/var/lib/dbus/machine-id`（缺失时宿主可注入）；
- 不含 secret；不是 GatewaySystemId。

### 5.2 作用与上报方式

- 用途：网关按 AgentSystemId 聚合该实例**在激活时上报过**的地址；
- **归属只在激活时建立**：`X-Agent-System-Id` 随激活请求提交，网关写入 `agent_system_addresses`；
  没有独立的注册/补报接口；
- **每次投递都要出示**：pending / stream / ack 请求都带同一个 `X-Agent-System-Id`，网关据此校验
  "你是不是这个聚合身份的主人"（不是则 403）；
- 聚合投递的连接单位是 `(AgentSystemId, Gateway)`，见 §6.1。

### 5.3 变化

agent home 或硬件指纹变化 ⇒ 新 AgentSystemId：

- 旧 binding 不得静默复用（`agent_system_id` 必须更新）；
- 该地址 MUST 用一次新激活把新 AgentSystemId 报上去（没有补报接口），否则网关不会投给新实例；
- 目录仍按 GatewaySystemId 分组，不受影响。

---

## 6. 拓扑：一条聚合投递

### 6.1 连接单位

```text
(AgentSystemId, Gateway) → 一条聚合投递
```

不是每地址一条、不是每 GatewaySystemId 一条、不是每 workspace/Session 一条。

- 聚合端点：`/api/v1/agent-system/pending`、`/api/v1/agent-system/pending/stream`、
  `/api/v1/agent-system/pending/ack`；
- 网关用 AgentSystemId 派生**单槽**：同一 `(AgentSystemId, Gateway)` 的第二个客户端拿到 409
  （`occupied` / `another client holds this AgentSystemId; retry later`），槽位带租约；
- 非聚合端点（`/api/v1/admin/pending*`）按 API key 分槽，是单地址/兼容路径，不读 AgentSystemId 头。

### 6.2 多 Gateway

```text
AgentSystemId
  ├── Gateway A → 聚合投递 A
  ├── Gateway B → 聚合投递 B
  └── Gateway C → 聚合投递 C
```

每个 Gateway 一条独立投递；pending/ack/thread meta 不跨 Gateway 混用。

### 6.3 Gateway 聚合授权

Gateway MUST：

- 认证调用凭据（§8.1），并从 `X-Agent-System-Id` 取聚合身份；
- 只投递该 AgentSystemId 在激活时上报过的地址（`agent_system_addresses` 关联查询）；
- 调用者不是该聚合身份的主人时返回 403（`not the AgentSystemId owner`）；
- 地址新增/暂停/恢复/解绑后更新集合。

### 6.4 本地路由

入站从"收到一笔投递"到"写进 Session"的判定见 §9；本节的拓扑只决定**从哪里收**。

---

## 7. Binding 与数据布局

根目录默认 `~/.aimail`，可配置。

```text
<aimail_home>/
└── systems/
    └── <GatewaySystemId>/
        ├── aimail_gateway.json          # 网关连接事实（agent 作用域，无管理密钥）
        └── <clean_addr>/
            └── agentmail.json           # 地址 binding（§7.1）  <!-- contract-allowed: binding file name, single source = contract/aimail-contract.json -->
    └── mail/<clean_addr>/
        ├── YYYYMM/in-*.json
        ├── YYYYMM/out-*.json
        ├── YYYYMM/attch/<mid>/
        ├── meta/<first2>/<mid>.json
        ├── threads/<first2>/<tid>.json
        └── .search/index.db
```

- 以 `GatewaySystemId` 分组，不是 AgentSystemId；同组可多地址；同一 Gateway 可多组；
- 不要求 `systems/<id>/` 下有 system 管理配置——目录分组不等于系统对接。

### 7.1 Binding 必填与可选字段

| 字段 | 必填 | 含义 |
|---|---|---|
| `email` | MUST | 地址 |
| `gateway_url` | MUST | 所属 Gateway（激活时若未给则取客户端 base URL） |
| `system_id` | MUST | 激活返回的 GatewaySystemId |
| `agent_system_id` | MUST | 本机 AgentSystemId（§5.1） |
| `domain` | MUST | 地址域名（由 email 派生） |
| `api_key` | MUST | 地址凭据（agent 作用域）；只在 binding 里，不写第二份 |
| `agent_id` | SHOULD | 宿主侧的 agent 标识（默认取地址 local-part） |
| `expires_at` | SHOULD | 凭据续期窗口（激活响应回填，供 Agent 提示） |
| `manager_address` | SHOULD | 安全员地址（激活响应回填；网关为权威） |
| `paused` | MAY | 本地暂停标志（存在即暂停） |
| `workspace_id` / `workspace_path` | 多地址宿主 MUST | 绑定空间；`move` 会把两者都写成目标路径。**同时是锚点来源**：拉取时哈希的就是它（§13.3），所以激活 MUST 把它写成"参与哈希的那个值"，pi 取 `~/.pi/agent`（整个 agent，不是 cwd） |

禁止：Session id、一次性 code、admin/domain/system 作用域密钥、用 workspace 之外的地址身份做路由。

### 7.2 权限

目录 0700；binding/pointer 0600；邮件与附件 0600/0700；日志与状态不得输出密钥。

---

## 8. 投递契约

### 8.1 连接与认证

- 认证 MUST 用地址激活签发的 **agent 作用域密钥**，以 HMAC 三头签名：
  `X-Api-Identity`（`system_id` 或地址）、`X-Api-Timestamp`、`X-Api-Signature`；
  时间戳超窗 401，重放 409。**不是 Bearer**（Bearer 只用于 board 路由）。
- 聚合请求 MUST 额外带 `X-Agent-System-Id`；缺失返回 400。
- 端点（每类两套：聚合 / 单键）：

| 用途 | 聚合 | 单键 |
|---|---|---|
| 长轮询 | `POST /api/v1/agent-system/pending` | `POST /api/v1/admin/pending` |
| SSE | `GET /api/v1/agent-system/pending/stream` | `GET /api/v1/admin/pending/stream` |
| ack | `POST /api/v1/agent-system/pending/ack` | `POST /api/v1/admin/pending/ack` |

- SSE 服务端行为：`event: mail`，data 形如 `{batches:[{body, deliveries:[{id,email,headers}]}]}`；
  心跳 `: ping` 每 15 秒；**单条流最长 300 秒**即关闭（重连是常态，不是异常）；每连接维护水位，
  重连会重发未 ack 的投递。
- 单槽：同 `(AgentSystemId, Gateway)` 第二个客户端 409；SSE 租约与长轮询租约不同（见附 C），
  退出 MUST 释放，崩溃由租约过期兜底。

### 8.2 两种传输模式

宿主 SHOULD 支持 `stream`（SSE）与 `poll`（长轮询），并按 `auto` 策略选择：

- `auto`：先探测 stream 可用性（`GET …/stream` 是否 200 + `text/event-stream`），失败降级到 poll；
- `poll`：显式只用长轮询（**插件沙箱/只能整包响应的 HTTP 客户端必须用它**）；
- 降级/升级必须连续多次失败才切换，避免抖动；
- **聚合长轮询没有服务端 hold**（`timeout_ms` 被忽略），因此 poll 模式 MUST 自带间隔
  （默认 30 秒）；单键长轮询才有最长 50 秒的服务端 hold。
- 模式可由环境变量覆盖（`AIMAIL_PULL_MODE=auto|stream|poll`），供宿主与验收夹具固定行为。

### 8.3 Ack

- 只有**已投递进 Session** 的投递才 ack；流本身从不 ack；
- "已投递"的**可证性取决于宿主**：能探测会话存在性/投递结果的宿主按本条严格判；只能确认"宿主已接管
  （入队/接受）"的宿主属 `unconfirmed`，其 C17 可证性按 §16 C17 处理，不得自我声明已证明；
- **风险接受**：宿主若不提供落地确认（rollout 方不为我们开放接口），owner 可**书面接受**"可能 ack 而未落地、
  系统不再重投"的风险。此时该宿主的 C17 记 **`RISK-ACCEPTED`**：必须写明签署引用与实测证据，**打印但不计入
  绿/红**（既不是通过，也不是缺陷）。未签署的 `unconfirmed` 宿主仍按 GAP/RED 判。
- ack body：`{"ids":[int]}`，一次最多 500 个（超出 400）；
- 响应 `{"acked": N}`（N 可小于请求数：不属于调用者的 id **静默 0 行**，不报错）；
- binding miss / paused / 空间 miss / Session 建不出来 ⇒ 不 ack，留 pending 下一轮重试；
- at-least-once：重复投递 MUST 由消息/thread 幂等吸收（§9.1）。

### 8.4 状态

宿主 MUST 能报告（供诊断，不含密钥/正文）：

```json
{
  "mode": "stream|poll",
  "agentSystemId": "asid_…",
  "gateways": [
    { "gatewayUrl": "…", "connected": true, "lastEventAt": "…", "lastError": "" }
  ]
}
```

---

## 9. 入站邮件到 Session 的映射

入站只需决定一件事：**这封邮件落到哪个 Session**。三个维度各管一段：

| 维度 | 管什么 | 来源 |
|---|---|---|
| 地址 | 收件人是谁 → 绑定到哪个**锚点** | binding（§7.1） |
| thread | 这是哪条对话（邮件层血缘） | 引用头（`References` / `In-Reply-To`） |
| Session | 在哪执行（锚点内的容器） | 本节的三条边 |

映射由三条边构成，命中即停：

| 边 | 键 | 强度 | 命中动作 | 未命中 |
|---|---|---|---|---|
| **E1 直属** | 立即父消息 = `In-Reply-To`，缺失时取 `References` **末项**（不是 `[0]`） | **硬**：锚点内必须回原 Session | 投递到该 Session | 转 E2；**不**沿引用链回溯（父消息存在但未命中 = 回复的是别人的信） |
| **E2 thread** | thread root = `references[0]` → `in_reply_to` → `message_id` | **软**：thread 自带上下文，允许跨 Session | 投递到该 thread 的 active Session（同锚点） | 转 E3；允许把 thread 重挂到新 Session |
| **E3 分配** | 绑定的锚点 | 偏好 | **优先新建 Session**；不能新建或创建失败 ⇒ 用该锚点内的当前 Session | 无可用 Session ⇒ Refusal(`no-target`) ⇒ 不 ack |

一句话：**E1 > E2 > E3；命中即终点；只有 E3 允许用"当前 Session"。**

### 9.1 出站索引（E1 的键表）

`send_mail` 成功时 MUST 记录
`outbound[anchor][message_id] = { address, gateway, session_id, root }`。

没有它，"直接回复回原 Session"无法判定，宿主只能按 thread 猜测。该索引同时是 at-least-once 的幂等锚点
之一（另一个是消息 id 本身）。

### 9.2 两条不变量

**I1（硬性，锚点内绝对）**：E1 命中且目标 Session 与 binding 同锚点 ⇒ MUST 投递到该 Session，禁止改投。
理由：Agent 以邮件向 owner 申请权限/等待审批时，回复必须回到发起请求的会话，否则上下文错位。

**I2（结构性，优先于 I1 的跨锚点情形）**：任何投递 MUST NOT 跨锚点调用 Session。

- 实现方式：三条边的键 MUST 含锚点标识与 Gateway（`outbound[anchor]`、`thread[anchor][root]`），
  跨锚点命中在构造上不存在，不依赖宿主权限或调用方自觉。
- 因此 E1 的硬性只在其目标 Session 与 binding 同锚点时成立；地址已 move 而 E1 目标在别的锚点时，
  MUST 放弃 E1、按 E2/E3 在当前锚点处理，并记账 `anchor-moved`。
- **记账义务**：`anchor-moved` MUST 出现在投递日志（运维可读），且 MUST 指明**被弃的边**与**外来锚点**。
  仅存在于内部返回值、日志里与"根本没有 E1"不可区分的实现，不算满足 C16。

### 9.3 降级

| 情形 | 处理 | 记账 |
|---|---|---|
| E1 未命中（父消息非我方/已不可解析） | 转 E2 | — |
| E1/E2 目标在**别的锚点** | 放弃该边，在当前锚点重排 | `anchor-moved` |
| E1/E2 目标 Session **已不存在**（被弃用/删除） | 新建 Session 并把 thread 指针重挂过去，投递成功后 ack | `session-gone` |
| E2 命中但 thread 记录损坏/不可解析 | 转 E3 | `thread-corrupt` |
| E3 无法新建（能力或失败） | 用该锚点内当前 Session | `space-fallback` |

重挂（`session-gone`）是唯一允许修改 thread 指针的时机；它不要求原 Session 存在，因为 thread 的上下文
随邮件本身携带（引用链 + 引用正文）。

### 9.4 写回

- `outbound[...]`：出站发送成功时写（§9.1）；
- `thread[anchor][root] = { session_id, updatedAt }`：入站**投递成功后**写；
- 同一 root 只有**一个 active 指针**；历史 Session 保留但新消息不再进入（与 §16 C8 一致）。

### 9.5 失败与 ack

`route(...) → Target | Refusal`；**Target 且投递成功 ⇒ ack；Refusal ⇒ 不 ack**（传输机制见 §8.3）。

| reason | 触发 |
|---|---|
| `no-binding` | 地址无 binding |
| `paused` | 本地暂停 |
| `anchor-moved` | 目标在别的锚点，已在当前锚点重排（最终仍可成功） |
| `session-gone` | 命中记录指向的 Session 已不存在，已重挂（最终仍可成功） |
| `host-refused` | 宿主拒绝写入（权限/载荷越界） |
| `no-target` | E3 也拿不到 Session |

### 9.6 粒度

一个投递批次内每条 delivery **独立**路由、独立 ack；一条失败不影响同批其余。

---

## 10. Welcome / manager

- Welcome 由**客户端主动触发**：`POST /api/v1/system/welcome`（宿主侧由 `role_calibrator` 调用）；
- 主题个性化（`Welcome to AIMail World, {agent}, since {date}!`），正文占位 `{domain}`/`{timestamp}`；
- cc 由网关按 `manager_address` 自动补齐（内/外部地址分别处理）；
- Welcome 的**回信**是普通 inbound，走 pending/SSE，与其它邮件同一条链路；
- 重放按 message id 去重；无 binding 不自动回复、不 ack；
- manager 由网关决定（申请页/域配置），本地只回填，不手工指定。

---

## 11. 锚定能力与单/多地址判定

多地址的本质是两个问题：**实例之下是否存在比实例更细的稳定锚定点**，以及**该锚定点能否管住处理邮件的
Session**。单地址不是另一种产品形态，而是这些条件不满足时的退化结果。

### 11.1 三层判定

| 层 | 问题 | 取值 | 性质 |
|---|---|---|---|
| L1 锚定点 | 是否存在比"实例"更细、可持久绑定、可枚举的容器？ | `stable` / `none` | 硬性 |
| L2 隔离 | 该容器能否保证处理邮件的 Session **只在本容器内**被调用？ | `yes` / `no` | 硬性 |
| L3 唤起 | 能否把消息写进**指定的既有** Session？ | `stable` / `none` | **硬性** |
| L3 创建 | 入站路径能否**新建** Session？ | `native` / `import` / `none` | **偏好** |

- **L1 `stable`**：标识跨重启稳定、可枚举，地址可绑定到它；"当前目录/当前进程"不算容器。
- **L2 `yes`**：存在不会跨容器解析会话的寻址入口。若宿主 API 技术上能触达全部会话，则 MUST 由映射键
  （键含容器标识）在源头排除跨容器调用，而不是依赖调用方自觉。
- **L3 唤起 `stable`**：能把消息写进指定的既有 Session，且三问全真：
  1. **可定位**：能指定既有 Session 写入；
  2. **并发安全**：与用户/其它写者冲突时排队或明确失败，不静默覆盖；
  3. **幂等**：重试不双写。
  这是多地址的**关键能力**：没有它，回信就不可能落到原会话（§9.2 I1），多地址必然串话。
- **L3 创建 `native` / `import` / `none`**（偏好项）：能新建 Session 只是让新 thread 有独立容器，
  **不是多地址的前提**。`none`（只能由用户手工创建）时 §9 的 E3 退化为"该锚点内的当前 Session"——
  按契约这是**可接受**的降级，不是单地址化理由。

### 11.2 判定与声明

```text
M ⟺ L1 == stable ∧ L2 == yes ∧ 唤起 == stable ∧ 入站可达
否则 S，failed[] = 第一个不满足的项（顺序：L1 → L2 → 唤起 → 可达性）
```

| 能力 | 性质 | 缺失时的后果 |
|---|---|---|
| 锚点 / 隔离 / 唤醒 | 硬性 | 落 S（无法把回信送回原会话或会串话） |
| 创建（`native` / `import`） | 偏好 | **仍为 M**；E3 退化到该锚点内的当前 Session |

- **可达性**：上述能力必须能被**入站路径**触发（宿主 API / 插件 API / 协议）；不可达按 S 处理且原因记为可达性。
- **声明 ≠ 判定**：宿主入口的地址模型声明是 claim，MUST 由三层探测得出，并在每次地址激活前复验。
- **拒绝第二个地址** MUST 发生在任何网关调用之前，MUST 带 `failed[]`，MUST NOT 消耗激活码。

### 11.3 三种退化（落 S）与一种偏好降级（仍 M）

**落 S 的三种**：

| 退化 | 现象 | 后果 |
|---|---|---|
| L1 无锚点 | 只有"当前"概念 | 单地址；入站只能用当前会话 |
| L2 不可隔离 | 多容器共用会话寻址 | 单地址（多地址必然串话） |
| 无唤起 | 无法指定既有会话写入 | 单地址；直属回复无法回到原会话 |

在 S 下 §9 的三条边退化为：E1 由"唯一会话"恒满足、E2 无意义、E3 只落到当前会话。

**仍 M 的偏好降级**：

| 降级 | 现象 | 后果 |
|---|---|---|
| 无创建 | 只能由用户手工建会话 | E3 用该锚点内当前 Session；锚点内尚无会话时 `no-target`（不 ack，等用户建会话） |

### 11.4 判定 → 可用映射边

| 判定 | E1 直属 | E2 thread | E3 分配 |
|---|---|---|---|
| M（有创建） | 可用（锚点内，硬性） | 可用（锚点内，可重挂） | 新建（`native` / `import`）；失败降级见 §9.3 |
| M（无创建） | 可用 | 可用 | 该锚点内当前 Session |
| S | 当前会话（唯一） | 退化为当前会话 | 当前会话 |

### 11.5 创建能力的等价形式：导入

若容器内没有原生"新建会话"接口，但能把一段 transcript 导入为新会话，则该能力**等价于** `native`
（即把 §11.3 的"无创建"偏好降级提升回"有创建"）。它是**偏好项的实现方式**，不参与 M/S 判定，也不是
§11.2 的豁免。使用该形式 MUST 满足：

1. **一次性完成**：新 thread 的第一封信用导入一次完成"建会话 + 投递首信"，不留"先建后投"的窗口。
2. **来源标注**：该 Session 来源为 imported（不是 created）；标题 MUST 携带 thread root（受宿主的标题长度上限），
   保证人可辨。
3. **幂等锚点取自"这封信"**：导入键 = 消息 id（缺失时正文指纹），**不是** thread root；重试同一封信 MUST 得到
   同一条既存记录并被判定为已投递，而不是第二条会话。
4. **既有会话的写入幂等**：写入 MUST 带确定性消息 id；宿主按"重复 id 即已落库"处理。
5. **ack 语义不变**：能力缺失、载荷越界、宿主拒绝一律不 ack。
6. **容器失效是元数据**：绑定的容器不存在时去掉容器绑定重投一次并记账，不因容器失效丢信。
7. **同 thread 首信单飞**：调用方 MUST 对同一 thread root 的首信串行化（两次导入会得到两条会话）。

## 12. 安装与混合态

状态轴：地址 0/1/多、GatewaySystemId 单/多、Gateway 单/多、active/paused/失败、单/多地址宿主。

| 状态 | 说明 |
|---|---|
| S0 | 空系统，无投递 |
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

- 已完成项不被未完成项阻塞；
- 单地址宿主拒绝第二个地址（且不消耗激活码）；
- 多地址宿主不得混空间；多 Gateway 不混 pending/ack；
- 外部预写 binding 不算激活（没有激活响应就没有凭据与归属）；
- 任何状态都能启动，未就绪项必须可诊断（§8.4）。

---

## 13. 解绑与迁移

### 13.1 pause

- 置 `paused`；从投递范围移除；不 ack；resume 后重新纳入并 drain 积压。

### 13.2 unbind

- 停止该地址路由；从聚合集合移除；该 Gateway 无其它地址时停止投递；
- 归档 binding 与本地凭据；
- 仅当 pointer 指向该地址时清除 pointer；
- 远端凭据不自动删除（远端注销是独立操作）；pending 不 ack，保持可诊断。

### 13.3 换锚点（同 AgentSystemId）：必须重新激活

锚点关系由**网关见证**：激活时登记锚点标识，之后每次拉取校验。因此"就地改锚点"不成立。

- 改锚点 MUST 走**重新激活**（新 address code）：本地改写 `workspace_id`/`workspace_path` 而不重新激活，
  MUST 表现为该地址在拉取时被网关拒绝（403 `anchor_mismatch`），实现 MUST NOT 把它当成成功；
- 不新建投递（同一 AgentSystemId、同一 Gateway）；
- 已投递 Session 不迁移（映射记录按 §9.3 处理）；
- **锚点标识**：锚点是**宿主侧**概念（dsh 为绑定 workspace，openclaw 为 agent workspace，
  **pi 为整个 agent（`~/.pi/agent`）**，hermes 为 profile home），网关不理解它。实现 MUST 以
  `sha256(lowercase(email) + "\0" + 锚点)` 作为**不透明标识**，在激活时登记（`activate-address-code`
  请求体 `anchor_id`，响应回显该值），并在**每次拉取**（SSE 连接 / 长轮询 / ack）以
  `X-Aimail-Anchor: <email>;<hash>[,<email>;<hash>…]` 提交。
- **强制（v2.5，owner 2026-11-25）**：拉取 MUST 带锚点声明，**没有"未登记就放行"这一档**——尚无生产
  部署，不存在需要兼容的合法无锚点客户端。网关 MUST 拒绝且**不投递任何邮件**，三种原因分别返回 403：
  `anchor_required`（请求完全没带声明）、`anchor_unregistered`（该 AgentSystemId 名下某地址从未登记
  锚点，即激活时没带 `anchor_id`）、`anchor_mismatch`（声明与登记值不一致，或漏了该组某个地址）。
  实现 MUST NOT 把 403 当成暂时故障静默退避，MUST 留一行可见日志并指向重新激活。
  **锚点本身永不出宿主**；新增宿主类型无需网关改动，只需定义自己的锚点字符串，且该字符串 MUST 稳定
  （MUST NOT 含 cwd / 会话 id / 会话文件路径），MUST 取 realpath。
- 宿主适配层回填锚点 MUST 只填空值，MUST NOT 覆盖已确立的锚点（覆盖只会让拉取被拒）；激活时 MUST
  把"参与哈希的那个锚点值"一并写进绑定（否则拉取侧算不出同一个 hash）。

### 13.4 move：换 GatewaySystemId / Gateway

- 地址归属变化 ⇒ 必须重新激活；
- 旧 binding 归档；新 binding 用新 code；
- 目录按新 GatewaySystemId 迁移；不同 Gateway 不共享 pending/ack/Session meta。

### 13.5 跨 AgentSystemId / 跨机器

- 旧实例 unbind；新实例生成新 AgentSystemId；
- 地址重新激活/重新签发凭据；不得复制旧 binding 直接使用。

### 13.6 home / 硬件变化

- 新 AgentSystemId；旧 binding 失效；重新激活；不得静默复用旧 ID。

---

## 14. 工具面

一个逻辑 toolset（名字 `agentmail`，单一来源 `contract/aimail-contract.json`）包含 15 个工具：  <!-- contract-allowed: skill/toolset name, single source = contract/aimail-contract.json -->

| 组 | 工具 |
|---|---|
| 邮件 | `send_mail`、`email_summary`、`set_email_summary`、`search_mail`、`manage_contacts`、`contact_profile`、`set_contact_profile` |
| board / A2A | `board_status`、`board_task_list`、`board_task_show`、`board_heartbeat`、`board_members`、`board_roles` |
| 地址与身份 | `aimail_address`、`role_calibrator`（无参） |

不包含：独立 `aimail_system` 工具；系统激活码专用工具；任何接受 raw key 的工具。

---

## 15. 安全

- 激活 prompt 仅含一次性 code、公开 gateway URL 与地址；
- manager 由网关/申请页决定，本地不手工指定；
- AgentSystemId 不是 secret，但**不等于凭据**：投递仍须 HMAC 签名；
- 凭据只落在 binding（0600），网关连接文件不存管理密钥，不写第二份；
- 日志不得含 code / key / 认证头 / 正文；
- 外部写 binding 非法（无激活响应即无凭据）；
- 单地址宿主拒绝多地址；多 Gateway 不交叉授权；越权 ack 静默 0 行，不泄露他人邮件存在性；
- unbind 不残留可路由身份。

---

## 16. 一致性要求

| ID | 场景 | 判据 |
|---|---|---|
| C1 | 单地址宿主 | 第二地址被拒绝，且不消耗激活码 |
| C2 | 地址激活 | 仅靠 address code 完成绑定；响应含 `system_id` |
| C3 | 目录分组 | `.aimail/systems/<system_id>/<address>/agentmail.json` |  <!-- contract-allowed: binding file name, single source = contract/aimail-contract.json -->
| C4 | AgentSystemId | 同机稳定；home/硬件变化生成新 ID |
| C5 | 单 Gateway 聚合 | 只投递该 AgentSystemId 激活时上报过的地址；一条投递 |
| C6 | 多 Gateway | 每 Gateway 一条投递；不混 pending/ack |
| C7 | 多地址/多系统 | 同 Gateway 下不同 GatewaySystemId 正确路由 |
| C8 | 映射优先级 | E1 命中必须回原 Session（锚点内，禁止改投）；E2 命中同锚点复用；E3 优先新 Session，可退当前。**证明范围**：会话由宿主 API 管理、入站路径可分配/改挂会话的宿主（M）。会话身份由 CLI 调用决定、插件只持有"当前会话"句柄的宿主（S，附 E）不判 C8 的分配半条；其"邮件进入当前会话并被 ack"仍按 C9 判 |
| C9 | ack | 投递成功后才 ack；miss/paused/建不出 Session 不 ack |
| C10 | 重启 | 已有 binding 启动即可投递；无 binding 不建投递 |
| C11 | 安全 | 无 raw key/code 日志；外部预写不算激活 |
| C12 | 解绑 | 停路由/聚合/pointer；不误删其他地址 |
| C13 | 迁移 | 同系统 move 保持地址；跨系统重新激活 |
| C14 | 传输回退 | stream 不可用时降级 poll 且继续 ack；恢复后可回 stream |
| C15 | 单槽 | 同 (AgentSystemId, Gateway) 第二客户端 409，第一个不受影响 |
| C16 | 锚点隔离 | 任何投递都不跨锚点；E1/E2 目标在别的锚点 ⇒ 放弃该边并在当前锚点重排，投递成功且记账 `anchor-moved`（日志可读、指明被弃边与外来锚点）。**证明范围**：宿主能分配 Session 且能表达"绑定在 A、会话曾在 B"即受判；`HOST_CAN_ALLOCATE_SESSION=0`（会话身份由 CLI 决定，无第二锚点可跨）时义务为空，记 `N/A` |
| C17 | 降级 | E1/E2 指向的 Session 已不存在 ⇒ 重挂 thread 并投递成功（记 `session-gone`），不得永久 pending。**适用范围**：宿主允许用户删除/归档会话时，C17 适用——"删了还能不能收到回信"此时可测；宿主根本没有会话删除概念时义务为空。`admission-only` 只是"证据形态"的标注，**不免除判红**：只要实测出现 ack 而零落地，仍按缺陷处理 |
| C18 | 判定可解释 | 第二地址被拒时给出 `failed[]` 第一项（L1 / L2 / 唤起 / 可达性） |

---

## 17. 宿主实现义务（中立）

任一宿主实现 MUST 满足：正文只写义务，不点名具体产品；具体宿主如何满足见附 E。

1. **锚点可标识**：提供稳定、可枚举、可持久绑定的容器标识（§11.1 L1）。"当前工作目录/当前进程"不构成身份。
2. **锚点内隔离**：处理邮件的 Session 只在本锚点内被调用（§9.2 I2）；跨锚点调用 MUST NOT 发生。
3. **能力如实声明**：创建与唤起能力 MUST 按 §11.1 的三层探测得出，并在每次地址激活前复验；声明与行为不符按缺陷处理。
4. **映射记录**：MUST 持久化 §9 三条边的键（出站索引、thread 指针），且键 MUST 含锚点标识与 Gateway。
5. **拒绝可解释**：拒绝第二个地址时 MUST 给出第一个不满足的层（§11.2），且不得消耗激活码。
6. **状态可诊断**：MUST 提供 §8.4 的状态面。

---

## Appendix A — 真实地址激活邮件与 prompt

以下为 Gateway v1.0.16 实文（`~/aimail-gateway` `src/core/api/activation.rs`
`address_activation_email()` / `address_activation_prompt()`）。花括号变量由网关发送时替换。

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

If your agent does not have aimail_address, it must install the AIMail SDK: if `uname -s` can be executed in the current shell, run `curl -fsSL '{gateway_url}/api/v1/agent-setup/bootstrap?shell=sh' | sh`; otherwise run `powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -Uri '{gateway_url}/api/v1/agent-setup/bootstrap?shell=ps1' -UseBasicParsing | iex"`. That URL carries no host parameter and nothing about the agent: the script works out the OS, which agent host it is running in, that host's own environment and the host-specific install command. Neither the email nor the prompt names a host or an OS; the only decision asked of the agent is whether `uname -s` runs.

Copy the message below into your agent's conversation and let the agent bind the address:

{prompt}

This code is sent only once - please keep this email.
Please do not reply to this email - it is sent automatically.

Best regards,
AIMail Team
```

`{prompt}` 的规范原文（用户复制这一段）：

```text
Use `aimail_address` to activate and bind the dedicated AIMail mailbox {address} for this agent (gateway: {gateway_url}; activation code: {code}). If `aimail_address` is unavailable or unregistered, install the AIMail SDK: if `uname -s` can be executed in the current shell, run `curl -fsSL '{gateway_url}/api/v1/agent-setup/bootstrap?shell=sh' | sh`; otherwise run `powershell -NoProfile -ExecutionPolicy Bypass -Command "iwr -Uri '{gateway_url}/api/v1/agent-setup/bootstrap?shell=ps1' -UseBasicParsing | iex"`. Then try `aimail_address` again for activation and binding. If it is still unavailable, stop and tell the user to restart the agent runtime to load the plugin and retry activation and binding afterward; no reinstallation is needed.
```

激活后的链路：

1. `aimail_address(action="bind", email, code, gateway_url)` → 宿主兑换 → 写 binding；
2. `role_calibrator()` → 触发 welcome（§10）；
3. 用户/Agent 按 welcome 指示回复；
4. 等到 `[AIMail] Persona applied` 才算校准完成；
5. 之后进入常规收发。

SKILL 的触发词就是工具名 `aimail_address`，不得用 URL 加载（附 B）。

---

## Appendix B — SKILL 索引

| SKILL | 名称（frontmatter） | 触发 | 职责 |
|---|---|---|---|
| 地址激活/生命周期 | `agentmail-activation` | `aimail_address` | bind、身份校准、pause/resume/unbind/move |  <!-- contract-allowed: skill/toolset name, single source = contract/aimail-contract.json -->
| 邮件收发 | `agentmail` | 邮件/收发意图 | 收发、回复、联系人、摘要、搜索 |  <!-- contract-allowed: skill/toolset name, single source = contract/aimail-contract.json -->
| board 协作（未发布） | `agentmail-board`（planned） | board/A2A 意图 | board 任务与角色协作 |

包内位置：

- `tssdk/packages/agent-aimail/resources/skills/activation/SKILL.md`
- `tssdk/packages/agent-aimail/resources/skills/SKILL.md`
- 仓库根镜像：`resources/skills/{activation/SKILL.md,SKILL.md}`

两个 SKILL 共享 toolset `agentmail`，但职责与触发条件不同；安装器 MUST 把激活 SKILL 装成独立 skill，  <!-- contract-allowed: toolset name, single source = contract/aimail-contract.json -->
不得覆盖邮件 SKILL。

禁止：用 URL 直接加载 SKILL；用 CLI/外部安装器完成地址激活；手写 binding；传 manager/raw key/system_code；
循环安装；未收到 `[AIMail] Persona applied` 就声称校准完成。

---

## Appendix C — 端点与常量（本版新增，供宿主实现自检）

| 项 | 值 | 出处 |
|---|---|---|
| 激活（地址码） | `POST /api/v1/activate-address-code`，body `{code, email_address}`，头 `X-Agent-System-Id` 可选 | gateway `activation.rs` |
| 激活（旧地址端点） | `POST /api/v1/activate-address`（返回 `api_key_id`，无 manager） | gateway `activation.rs` |
| 激活码 TTL | 24 小时（地址码） | gateway `activation.rs` |
| 系统激活（宿主不得暴露） | `POST /api/v1/activate-system`，发行 system 作用域密钥 | gateway `activation.rs` |
| 长轮询体 | `{limit(默认 20；SDK 上限 200), emails[], filter[], timeout_ms}` | gateway `http.rs`；SDK `pullList` |
| 单键长轮询 hold | 最长 50 秒 | gateway `types.rs` |
| 聚合长轮询 hold | **无**（忽略 `timeout_ms`） | gateway `http.rs` |
| SSE | `event: mail`，心跳 15 秒，单流最长 300 秒 | gateway `types.rs`/`http.rs` |
| ack | `POST …/pending/ack`，body `{ids:[…]}` ≤500，返回 `{acked:N}`，越权 id 静默 0 | gateway `http.rs`/`storage.rs` |
| 单槽冲突 | 409（`occupied` / `another client holds this AgentSystemId; retry later`） | gateway `http.rs` |
| 认证 | HMAC 三头 `X-Api-Identity`/`X-Api-Timestamp`/`X-Api-Signature` | gateway `auth.rs` |
| welcome | `POST /api/v1/system/welcome` | gateway `welcome.rs` |
| AgentSystemId 格式 | `asid_` + sha256(前缀+home+指纹) 前 32 hex | tssdk `agent-system-id.ts` |
| binding 文件名 / pointer | `agentmail.json` / `.agentmail` | `contract/aimail-contract.json` |  <!-- contract-allowed: binding file name, single source = contract/aimail-contract.json -->

---

## Appendix D — 变更记录（v2.1 → v2.3）

| # | 上一版的问题 | 本版处理 |
|---|---|---|
| 1 | 附 A 的 prompt 是旧文案（"Activate and bind my AIMail mailbox…"、"`agent_aimail`"、"from PyPI via pip / from npm"） | 换成网关 v1.0.16 实文（`agent-aimail`、平台插件安装命令、重启提示）；邮件正文补齐 "and let the agent bind the address" |
| 2 | §8 只写"SSE"，未提长轮询与 `auto` 降级；未写端点、TTL、心跳、单流寿命、单槽 409 | 重写 §8 为"投递契约"：两套端点 × 两种模式 + 网关行为常量；新增附 C |
| 3 | §8.1 写"认证使用 Gateway 签发的 AgentSystemId 凭据"，未说明签名方式，易被读成 Bearer | 明确 HMAC 三头 + `X-Agent-System-Id`；指出 Bearer 只用于 board |
| 4 | §7.1 binding 字段缺 `domain`/`agent_id`/`api_key`/`expires_at`，未说 `workspace_id` 由 move 写入 | 列全字段与必填性；`expires_at` 回填要求 |
| 5 | §13.3 要求 move 的目标"必须属于当前 Agent 系统" | 改为代码事实：目标必须是**已存在的目录**，宿主校验即可 |
| 6 | 旧「参考实现状态」表（"AgentSystemId 待实现""多 Gateway SSE 待实现"）早已过期 | 换成按"面"列的实现状态 + 代码证据（现位于附 F） |
| 7 | §5.1 用 `stable_hash(...)` 占位，§5.2 说"每个 Gateway 分别注册/授权"（网关无注册接口） | 给出精确派生；改为"激活请求头 + 聚合请求头"两条上报路径 |
| 8 | §2/§3 未写激活端点与响应字段名，且未说明网关仍有 `activate-system` | 补端点、响应字段、失败码表；显式声明 system 激活存在但宿主不得暴露 |
| 9 | §10 未说 welcome 是客户端触发的 | 补 `POST /api/v1/system/welcome` 与 `role_calibrator` 触发 |
| 10 | 正文多处重复表述（§6.4 与 §9；§8.3 与 §11.4 的 ack 行） | 每类断言只留一处，其它处改为指针 |

### v2.2 / v2.3

| # | 问题 | 处理 |
|---|---|---|
| 11 | §9 是 7 步流水线，把"三个独立解析器 + 优先级"写成顺序步骤；"禁止降级"与"退当前会话"分裂在两处 | 重写为三条边 E1/E2/E3 + 一句话优先级；未命中只允许向下一级 |
| 12 | "退到当前活跃 Session" 从未定义；M 宿主下"当前"无意义 | 定义为"**该锚点内**的当前 Session"，且只有 E3 可用 |
| 13 | "thread 可跨 Session" 与 C8 冲突 | 改为"一个 root 一个 active 指针"，允许在 `session-gone` 时重挂 |
| 14 | ack 规则在 §9 出现三次 | 收敛为一条：Target 且投递成功 ⇒ ack；其余为原因表（§9.5） |
| 15 | §11 用"三轴 + 四项条件"，轴与可达性混在一起；`create` 只有二值，import 被当成豁免 | 改为 L1 锚点 / L2 隔离 / L3 唤起（硬性）+ L3 创建（偏好）；import 是创建的一种实现（§11.5） |
| 16 | `wake stable` 把"可定位/并发安全/幂等"压成一个词 | 拆成三问，全真才 `stable`（§11.1） |
| 17 | 声明与探测关系未写 | 明确"声明是 claim，必须由三层探测得出并每次激活前复验"（§11.2） |
| 18 | S 的后果平铺断言、拒绝无成因 | 三种退化逐条给出；拒绝 MUST 带 `failed[]`（C18） |
| 19 | 正文点名具体宿主（pi / PI-Desktop / dsh / openclaw），规则与实例混排 | 正文只写规则与义务；实例移到 **附 E**，实现状态移到 **附 F** |
| 20 | §11.4 矩阵重复 ack / AgentSystemId 等他人结论 | 矩阵只保留"判定 → 可用映射边" |
| 21 | 上一稿把"创建"写成与"唤起"并列的硬性条件（"缺一不可"），会把"能唤起但不能新建"的宿主误判为 S | 按 owner 指正：**硬性 = 锚点 + 隔离 + 唤起**；创建是偏好，缺失时 E3 退化到该锚点内当前会话（§11.3 单列"仍 M 的偏好降级"） |

相邻资产同步（2026-11-25，三步裁剪）：`contract/aimail-contract.json` 原把 push 时代的
`inbound_path`/`inbound_port`/webhook 三态当通用规则，与本契约的 V2 投递模型冲突。

1. **先按事实分节**：`local_endpoint`（在役但有界：pi/dsh/openclaw 的本地接收端点）与 `legacy_push`
   （v1 专有：hermes 适配器的 webhook path/route），并把"空= pull / 非空 = push"限定为 v1 语义。
2. **随 v1 hermes 退役整组删除**她的两个键与镜像常量（清单 version 3）。
3. **最后连同回环监听一起删除**（owner 2026-11-25 决定）：`inbound_path`/`inbound_port` 从清单消失
   （version 4），`core/listen.ts`、pi 的监听、dsh 的 HTTP handler、openclaw 的网关内路由一并移除。
   理由：V2 只靠拉取投递，本地端点既不构成投递能力，又逼每个适配层维护第二套投递路径——dsh 正是
   因为这一点分裂成两套路由（出货路径没有 E1、没有 dispatch 行）。清单现在只含**命名**值。

守卫：`tests/contract/check-legacy-push-scope.py`（L0）——端点键/常量、监听符号（`listenWithRetry`、
`createInboundHandler`…）、v2 面的端点字面值、`pysdk/hermes/` 目录，任一回潮即红。

---

## Appendix E — 参考宿主（实例，非规范）

本节是**举例**，不构成规则；规则见 §11。同一引擎的不同承载形态可以落在不同判定上。
`投递确认` 列只说该宿主**能以什么形态提供证据**，不改变义务：`probe` = 能查会话是否存在（可直接判）；
`unconfirmed` = 只能确认宿主已接管（入队/接受），**C17 仍受判**——实测出现 ack 而零落地就是缺陷；
`no-session-model` = 宿主没有入站可分配/改挂的会话（会话身份属于 CLI），义务为空，才可记 `N/A`。

| 实例 | L1 锚点 | L2 隔离 | L3 创建 | L3 唤起 | 判定 | 投递确认（`probe` / `unconfirmed` / `no-session-model`） | 备注 |
|---|---|---|---|---|---|---|---|
| dsh | workspace | 宿主按 workspace 起会话 | `native` | `stable` | M | `probe`（会话注册表可查；resume 失败即 `session-gone`，有 inbound 单测） | thread 连续性原生可用 |
| openclaw | agentId | 由宿主路由保证 | `native` | `stable`（三件齐备时） | M（条件） | `unconfirmed`（200＝cron job 入队；插件面没有 run/session 查询）⇒ C17 仍受判 | 宿主侧 `hooks.allowRequestSessionKey=true` + 非空前缀白名单；**调用侧必须同时发 `sessionKey` 与 `sessionMode:"persistent"`**（默认 `isolated`＝每次新开会话） |
| 裸 pi（TUI/CLI） | cwd（有容器） | 否：会话文件无锁，无法发现第三方写者 | `none` | `none`（只能当前会话） | S | **`no-session-model`**（`sendUserMessage` 为 fire-and-forget；会话身份由 CLI 决定） | 地址绑定当前 cwd；第二个地址在网关调用前被拒；**C8 的分配半条与 C17 不适用**：`newSession`/`fork`/`switchSession` 只存在于 `ExtensionCommandContext`（"only safe in user-initiated commands"，pi 1.5.2 `extensions/types.d.ts:303-336`），后台投递只持有当前会话；会话身份由 CLI 调用决定。**E1 因此在 pi 上不成立**：同一进程内回复自然落在那个会话（因为只有一个），但进程重启/换会话后无法回到原会话；发信侧可记录会话身份（`ctx.sessionManager`）供审计，入站侧无上下文句柄，不能比对 |
| hermes（pysdk） | profile | 单 profile 单会话（S） | `none` | `none` | S | `unconfirmed` ⇒ C17 仍受判（已实测 ack-未落地） | 交付由 `_bridge.deliver` 返回 bool，无法确认落地 |
| deerflow（pysdk） | 会话存储（DB thread） | 由宿主 thread 表保证 | `native` | `stable` | M | `probe`（投递后 `GET /api/threads/<id>/runs/<run_id>` 回读：401/403/404 记 `landing-unverified` 并放行；其余非 200 或终态失败（`RunStatus` 的 `error`/`timeout`/`interrupted`）⇒ 不 ack） | 会话可被用户删除，删除后是否重挂需实测 |
| PI-Desktop | project | 由映射键含 project 保证（插件 API 技术上可触达全部会话） | `import` | `stable` | M | `probe`（`session.list`/`session.get` 可查） | 唤醒稳定来自宿主租约 + 排队 + 幂等键；传输只能长轮询 |
| 假设宿主「有锚点 + 能唤起 + 不能新建」 | 有 | 有 | `none` | `stable` | **M** | 取决于实现 | 新 thread 落该锚点内当前会话（§11.3 偏好降级），不降为 S |

## Appendix F — 参考实现状态

以"能力面"为单位；未实现项不得在验收中声明完成，新增证据行 MUST 同时给出代码路径。

| 面 | 状态 | 证据 |
|---|---|---|
| AgentSystemId 派生与上报 | 已实现（ts + py） | `src/core/agent-system-id.ts`；`pysdk/v2/agent_system_id.py` |
| 聚合投递（每 ASID+Gateway 一条） | 已实现 | `startSystemPull`（ts）、`pysdk/v2/system_pull.py` |
| stream / poll / auto 状态机 | 已实现 | `src/core/pull-contract.ts`、`address-code.ts::startPolling` |
| 地址激活仅 code | 已实现 | `address-code.ts::activateAddressCode`、`pysdk/v2` |
| 目录按 GatewaySystemId | 已实现 | `address-code.ts` 写 binding 路径 |
| 映射三条边（E1/E2/E3） | 已实现 | `src/runtime/routing.ts`、`src/runtime/session-router.ts`、`src/desktop/inbox.ts` |
| 出站索引 | 已实现 | `src/runtime/session-router.ts`、`pysdk/v2/outbound.py` |
| 锚定能力三层判定 | 已实现（声明 + 探测） | `src/core/host-capabilities.ts` + 各宿主入口声明 |
| 导入等价创建（§11.5） | 已实现 | `src/desktop/delivery.ts` |
| 解绑/迁移 | 部分（本地归档 + move；远端注销需网关配合） | `address-code.ts::archiveBinding`、`aimailAddress(move)` |
| board SKILL | 未发布（工具已实现） | `resources/skills/` 下无 board 目录 |
