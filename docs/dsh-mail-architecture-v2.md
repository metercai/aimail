# DSH AIMail 接入设计（修订版 v3）：插件 + 网关

> 状态：**待评审**
> 参与仓库：
> - `metercai/aimail`（tssdk dsh 适配层）
> - `metercai/aimail-gateway`（base 网关，public）
> - `metercai/aimail-advanced`（advanced 网关，私有；引用 base）
> 日期：2026-11-15
> 传输前提：**dsh 只使用 SSE pull**；不启用 push，因此没有本地监听器、HMAC、webhook 字段、bridge key、`webhook_host`。

---

## 1. 范围与目标

本设计把 dsh 的 AIMail 接入拆成**两个独立入口**，并补齐 base 网关原先缺失的“申领 → 邮件发码 → agent 对话激活”闭环：

| 入口 | 工具 | 频度 | 目标 |
|---|---|---|---|
| 系统对接 | `aimail_system` | 一次/极少 | 用系统激活码建立 system、写 `aimail_gateway.json`、铸 domain key、创建默认工作员并绑定到 **DSH 默认工作区** |
| 地址绑定 | `aimail_address` | 日常 | 本域地址（B1）或共享域地址（B2）绑定，以及 pause/resume；不做 unbind |

base 与 advanced 是**两套独立实现**，只共享同一份对外 API 契约：

- **base**：单系统、只支持独享域；新增“当前单系统”的系统码/地址码申领页与激活 API，不做共享域、多系统、产品配额。
- **advanced**：多系统、独享域/共享域、产品/配额/许可；保留自己的申领/激活/面板实现；引用 base 时通过 Cargo feature 让 base 的申领/激活路由**不挂载/不可见**，避免同名路由冲突。
- 插件只依赖 API 契约，不依赖任一网关内部实现；同一套工具可对 base 或 advanced。

---

## 2. 术语

- **base**：`aimail-gateway`，**单系统、可多个独享域**；SMTP↔HTTP/激活码基础能力；不含共享域/多系统/产品配额。
- **advanced**：`aimail-advanced`，`Cargo.toml` 以 `aimail-gateway = { path = "../aimail-gateway" }` 引用 base；提供产品、配额、共享域、申请页、面板、许可。
- **system**：AIMail 系统，`system_id` 唯一；base 只有独享域（可有多个 domain），advanced 还支持共享域系统名（`system_name`）。
- **默认工作员**：系统对接后自动创建的 `agent@{domain}`（独立域）或 `agent.{system_name}@{共享域}`。
- **本域地址**：属于已对接 system 的地址（B1）。
- **共享域地址**：没有本地 system、通过地址码绑定的地址（B2）；**仅 advanced 支持**。
- **domain key**：`category=domain`、`scope=system`、`email=domain`；注册地址、铸 agent key、改本域 webhook。
- **agent key**：`category=agent`、`scope=agent`、`email=地址`；只收发自己邮件。

### 2.1 场景口径（1–6，与 README 对齐）

| 场景 | 入口 | 域/网关 | 凭据 | DSH 工具 |
|---|---|---|---|---|
| 1 | 地址级 agent 对话 | 共享域，**无 system** | 地址激活码 | `aimail_address`（B2） |
| 2 | 系统级 CLI | 共享域 | 系统激活码 | `aimail install`（不是 DSH） |
| 3 | 系统级 CLI | 独享域 | 系统激活码 | `aimail install`（不是 DSH） |
| 4 | 系统级 CLI | 独立网关 | 已有 system key | `aimail install`（不是 DSH） |
| 5 | 系统级 agent 对话 | 共享/独享/独立 | 共享/独享：系统码；独立：system key | `aimail_system` |
| 6 | 系统对接后地址级 agent 对话 | 共享/独享/独立 | 地址码可选；无码用 domain key | `aimail_address`（B1） |

- **场景 1 ≠ 场景 6**：场景 1 没有 system，共享域地址是 `name@shared`；场景 6 已有 system，共享域地址是 `name.system_name@shared`，独享/独立是 `name@domain`。
- 场景 2/3/4 是 CLI；DSH 不调用 CLI，场景 5 是它们的 agent 对话等价入口，不要混写。
- 场景 5 的独立网关子形态用 `system_key + system_id + domain`，不消耗产品激活码。
- 地址工具判定：有 system → B1（场景 6）；无 system + `scope=shared` → B2（场景 1）；有 system + shared 码 / 无 system + 非 shared 码均在激活前拒绝。

---

## 3. 网关现状（代码事实）

### 3.1 base（`aimail-gateway`）

- `activation_codes` 表已存在（`src/core/storage.rs:459`），字段含 `code_type(address|product)`、`system_id/domain/email_address`、`code_cipher`、`expires_at`、`claimed`、`is_frozen`、`is_shipped`、`created_by`；**无** `applicant_email`、无 applicants/email_verify、无 `manager_address` 列。
- **base 支持单系统多 domain**：`system_domains` 以 `system_id` 索引、`domain_addr` 全局唯一，同一 `system_id` 可有多行（多个独享域）；`create_system_domain` 不限制每系统一个 domain。
- 已有 `POST /api/v1/activate-address`（`src/core/api/http.rs:160`）和 `activate_address_code()`；**没有** `/api/v1/activate-address-code`（SDK/advanced 用的是 `-code` 路径）。
- **没有** `POST /api/v1/activate-system`；**没有**公共 `apply-system`/`apply-address`/`email-verify`/`preflight` 路由。
- 地址码由管理员/程序路径生成（`register_address?generate_code=true` 或 admin 端点），**没有页面申领流程**。
- 没有共享域配置/`shared_addr_*` 系统引导、没有 applicants、没有邮件验证码、没有产品/配额。
- 有 `RouterHook` 注入点（`create_router(state, router_hook, domain_handler, list_domain_handler, send_handler, whitelist_handler)`），advanced 用它挂路由。

### 3.2 advanced（`aimail-advanced`）

- 已实现 system 激活/申请、address 申请/激活、邮件验证、申请人、产品/配额/许可、共享域、面板、前端页面。
- 关键路由（`src/advanced/strategy.rs:1268+`）：
  - `POST /api/v1/activate-system`、`/api/v1/apply-system`
  - `POST /api/v1/activate-address-code`、`/api/v1/apply-address`
  - `POST /api/v1/public/email-verify/send|check`
  - `POST /api/v1/public/address-state`、`GET /api/v1/public/shared-domains`
  - 面板 `/api/v1/panel/...`（产品、激活码、系统、配额）
- 前端页面（`frontend/dist/.../public/`）：`apply-system`、`apply-address`、`activate`。
- 系统激活是 **product 码**模型：`code_type=product`，绑定 exclusive domain 或 shared `system_id`；有 pickup-binding、renew-only、配额、许可。
- 地址激活（`src/advanced/api/address.rs:209+`）在事务内做 code 查找、email 绑定、bare-domain、**already_active 拒绝**、key INSERT、expiry、code 删除。
- `applicants` 表 + `activation_codes.applicant_email` 是 advanced 初始化的（`ALTER ... .ok()` 幂等）。

### 3.3 scope / SSE（base 与 advanced 共用）

- `agent` key 只看到自己地址；`system`/`bridge` key 看整个 system；`domain` 类 key 的 scope 是 system。
- 每个 key 一个流槽位；`409 occupied`；at-least-once；流不 ack，ack 走 `/admin/pending/ack`。
- **多地址共享域地址没有多地址 agent key**：每个地址一个 agent key、一条流；不能合并（见 §12）。

---

## 4. 总体架构

```
┌──────────── dsh 插件 ────────────┐      ┌──────── base 网关 ────────┐
│ aimail_system / aimail_address   │      │ 申领页面/API              │
│  ├─ preflight code               │─────▶│  apply-system/address     │
│  ├─ activate-system/address-code │      │  email-verify             │
│  ├─ config/pointer               │      │  activation-codes         │
│  └─ mail-hub（system/地址流）     │      │  activate-system/address  │
└──────────────────────────────────┘      │  preflight/renew/rotate   │
                                          └──────────┬───────────────┘
                                                     │ path crate
                                          ┌──────────▼───────────────┐
                                          │ advanced：产品/配额/许可/  │
                                          │ 面板/共享域策略（复用 base）│
                                          └──────────────────────────┘
```

- 插件不依赖 CLI；base/advanced 各自实现同一份公共 API 契约。
- advanced 引用 base 时，base 的申领/激活路由通过 Cargo feature 关闭（不挂载），由 advanced 自己的路由提供；两边不共享运行时激活逻辑，避免冲突。
- 插件只持 domain key（system 模式）或 agent key（B2），不持 bridge key。

---

## 5. Case A：系统对接（`aimail_system`）

### 5.1 标准 prompt

```text
【AIMail 系统接入】
请在本机 dsh 内完成 AIMail 系统对接，不要调用 aimail CLI。

gateway_url: https://aimail.token.tm
系统激活码: <一次性系统激活码>
系统名称: <共享域时填；与激活码绑定一致；base 没有此项>
域名: <base 多 domain 时可选其一；与激活码绑定一致；代码已绑定时可不填>
（激活码已绑定 system_name/domain 时可不填；未绑定时二选一，必须有一个）
manager: <可选；整个系统的默认安全员，不是收码邮箱；不填则用收码邮箱>
```

独立网关（场景 5 的独立子形态，无产品码）：

```text
gateway_url: <自建网关>
system_key: <网关启动时打印的 system key>
system_id: <网关启动时打印的 system id，如 system-xxxx>
domain: <独享域>
manager: <可选>
```

### 5.2 参数

| 参数 | 必填 | 说明 |
|---|---|---|
| `action` | 是 | `bind` / `status` |
| `gateway_url` | 首次 | 激活信息；已有 config 时可省略 |
| `code` | 与 `system_key` 二选一 | 一次性系统激活码（共享/独享云网关） |
| `system_key` | 与 `code` 二选一 | 独立网关已有的 system-level key |
| `system_id` | `system_key` 时必填 | 独立网关启动打印的 system id |
| `system_name` | 绑定未定才填 | 共享域系统名；必须与激活码绑定一致；base 无此项 |
| `domain` | 绑定未定才填 | 独享域；base 多 domain 时选其一；必须与激活码绑定一致 |
| `manager` | 可选 | **仅系统对接**；整个系统的默认安全员，**不是收码邮箱**；网关校验/白名单 |

- 不接受 `email`。
- 激活码已绑定 `system_name`/`domain` 时以 preflight 为准，不用填；未绑定时 `system_name` XOR `domain`，同时/都无 → `bad_args`。
- 系统申领页/API 没有 manager；manager 只在**激活**时作为系统默认值覆盖（`aimail_system` tool 参数）。

### 5.3 校验（preflight 先行）

0. `code` 与 `system_key` 必须且只能有一个；独立网关用 `system_key + system_id + domain`。
1. 有 code：`POST /api/v1/public/activation-code/preflight {code}`：
   - 返回 `code_type/scope/system_id/domain/system_name/email_address/expires_at/claimed/is_shipped`；
   - 不消费 code。
2. `code_type` 必须是 `product`/system；address code → `wrong-tool`。
3. `system_name`/`domain` 必须与 code 的绑定一致（preflight 字段）；不一致 → `system_mismatch`，**不激活**。
4. 已有 config：
   - 同 gateway：带 code → 走激活（续期/轮换）；不带 code → `already_provisioned`（status）。
   - 不同 gateway → `gateway_mismatch`。
   - 已有 B2 地址 binding、无 system → `binding_conflict`（一个 profile 不同时跑两种模式；提示删除该共享地址 binding 或换 profile——工具不提供 unbind）。
5. `manager` 可选；不传则网关默认 = 收码邮箱（申请人邮箱）；传了则作为整个系统的默认安全员。**不需要管理员/收码邮箱确认**，插件直接传、网关直接写。申领页本身不写 manager。
6. 独立网关：`whoami` 校验 `system_key` 与 `system_id`；地址级 key 拒绝；domain 必填。

### 5.4 流程

| # | 动作 |
|---|---|
| 1 | preflight code；校验 type/绑定/已有 system |
| 2 | `POST /api/v1/activate-system {code, system_name?, domain?, manager?}`（公开；code 即凭证） |
| 3 | 响应 `raw_key/system_id/domain/system_name/manager_address/kind/expires_at` |
| 4 | 写 `~/.aimail/systems/<sid>/` 0700；`aimail_gateway.json` 0600（raw 兜底；字段见 §9） |
| 5 | 写 `.system_raw_key.key` 0600 |
| 6 | 读响应 `domains`（base 多 domain）或 `domain`；选中 domain |
| 7 | **为每个 domain 铸 domain key**（`POST /admin/api-keys` category=domain, email=domain, scopes=["system"]）；`admin_key`=选中 domain 的 key，其余存 `domain_keys` |
| 8 | `/whoami` 校验选中 domain key；删 raw key |
| 9 | **默认工作员**：`emailForAgent('agent', 选中 domain, system_name)` → 注册（pull，无 webhook）+ 铸 agent key + 写 `agentmail.json` |
| 10 | **绑定到 DSH 默认工作区**（不是当前工作区；见 §5.5） |
| 11 | pointer/status；Hub 开 1 条 domain key system 流 |

### 5.5 默认工作员与默认工作区

- 地址：base 为 `agent@{选中 domain}`（多 domain 时以激活码/申领选择为准）；advanced 独享域同左，共享域为 `agent.{system_name}@{共享域}`。
- **绑定到 DSH 默认工作区**：`workspace.json` 的 `global.defaultWorkspaceId`（`default-workspace`）。
  - 当前 session 的 cwd 不参与；系统对接不是地址绑定，语义上默认工作员属于默认区。
  - 默认工作区不存在/不可用 → `workspace_invalid`，提示先创建/打开默认工作区；不静默绑到当前区。
- 若默认地址已有 binding：不覆盖其 workspace；结果返回现有 workspace。
- 若默认地址已在网关存在但本地无 key：domain key 铸新 agent key，写 binding。

### 5.6 结果 / 重复

```jsonc
{ "success": true, "scope": "system", "system_id": "…", "domain": "…",
  "system_name": "…", "default_email": "agent@…",
  "workspace": { "id": "…", "path": "/…/default-workspace", "title": "default-workspace" },
  "kind": "new|renewal|rotation", "degraded": false }
```

- 带 code 的重复 bind：preflight 匹配后重新激活；gateway 返回 `kind`；更新 keys/expiry；不静默跳过。
- 不带 code：`already_provisioned`。
- 不同 system/gateway：`system_conflict`/`gateway_mismatch`，不覆盖。

---

## 6. Case B：地址绑定（`aimail_address`）

### 6.1 标准 prompt

```text
【AIMail 地址绑定】
请把下面的邮箱绑定到当前工作区。

邮箱: meterv@aimail.token.tm
地址激活码: <场景 1 必填；场景 6 可选（省略则用 system domain key 注册）>
gateway_url: <场景 1 必填；场景 6 可选，若给必须与已记录一致>
```

### 6.2 参数 / 场景

> 本工具覆盖 **场景 1（无 system + 共享域地址码）** 与 **场景 6（有 system 后绑定）**。
> 场景 6 的共享域地址是 `name.system_name@shared`，独享/独立是 `name@domain`；场景 1 是 `name@shared`。
> 场景 2/3/4 是 CLI，不属于本工具。

| 参数 | 必填 | 说明 |
|---|---|---|
| `action` | 是 | `bind` / `pause` / `resume`（**不做 unbind**） |
| `email` | 是 | 地址；必须与 code 绑定一致 |
| `code` | B2 必填；B1 可选 | 一次性地址码；B1 省略则用 system domain key 注册新地址 |
| `gateway_url` | B2 必填；B1 可选 | 共享网关；B1 若给必须与 config 一致 |

- 不接受 `manager`；地址安全员默认 = 收码地址；变更走事后转移。
- B1：有 system；地址格式 `user@{domain}`（独立域）或 `user.{system_name}@{domain}`（共享域系统）。
- B2：无 system；共享域地址；code + gateway；**仅 advanced 支持**。base 没有共享域，B2 在 base 上不可能，preflight 不会返回 `scope=shared`。

### 6.3 校验（preflight 先行，B2 不依赖共享域列表）

1. **有 code 时**：`POST /public/activation-code/preflight`：
   - `code_type` 必须是 `address`；
   - `scope=system`（system 内地址）或 `scope=shared`（共享域地址）；
   - `email_address` 必须等于用户给的 `email`（网关侧也会校验）；
   - `system_id/domain/system_name` 用于 B1/B2 判定。
2. **B1（已有 system）**：
   - 地址格式匹配 system（独立域无点；共享域系统 `.{system_name}@domain`）；
   - `gateway_url` 若给必须等于 config；
   - preflight 的 `system_id` 必须等于 config 的 `system_id`；
   - **domain key**：地址 domain == config.domain → `admin_key`；否则用 `domain_keys[domain]`；都没有 → `domain_key_missing`（需重新系统激活/新码补 key）；
   - 不匹配 → `system_mismatch`/`gateway_mismatch`，**不激活**。
3. **B2（无 system）**：
   - 必须有 code + gateway；
   - preflight 必须 `code_type=address` 且 `scope=shared`；否则 `needs_system`，**不激活**（不消耗 code）；
   - 本地已有其它 gateway 的 binding → `gateway_conflict`（一个 profile 一个 gateway）。
4. **B1 无 code**：system domain key 注册地址（pull，无 webhook）+ 铸 agent key。
5. **code/email 不一致**：preflight/activate 拒绝；不重试、不改 email。

### 6.4 流程

- **B1 有 code**：preflight/validate → `POST /api/v1/activate-address-code {code,email_address}` → agent key/expiry/kind → 写 binding。
- **B1 无 code**：`POST /admin/systems/<sid>/addresses`（无 webhook）→ `POST /admin/api-keys` category=agent → agent key → 写 binding。
- **B2**：preflight → `POST /api/v1/activate-address-code` → agent key/expiry/kind → 写 binding（含 gateway_url）+ 该地址 agent 流。

### 6.5 结果 / 重复 / 续期

```jsonc
{ "success": true, "scope": "address", "email": "…", "system_id": "…",
  "workspace": { "id": "…", "path": "…", "title": "…" },
  "mode": "code|register|link", "kind": "new|renewal|rotation|already_active",
  "expires_at": "…", "paused": false, "degraded": false }
```

- 带 code 的重复 bind：preflight 匹配后激活；gateway 决定：
  - 共享域：续期语义（extend expiry；active 时可能 `already_active`）；
  - 非共享域：key 轮换语义（新 key 替换 binding，旧 key 失效）；
  - 插件只应用返回的 `kind/expires_at/raw_key`，不自己判断。
- 无 code 的重复 bind：只更新 workspace（`link`），不激活。
- 不做 unbind：到期/终止由网关决定；插件保留 binding；新 code 再 bind。

---

## 7. 生命周期（bind / pause / resume）

| 当前 | 动作 | 结果 | 网关 |
|---|---|---|---|
| unbound | bind（B1/B2） | active | 有 |
| active | bind 同 workspace（无 code） | link | 0 |
| active | bind 异 workspace（无 code） | link（改 workspace） | 0 |
| active | bind + code | 按 gateway `kind` 更新 key/expiry | 有 |
| active | pause | paused | 0 |
| paused | pause | already_paused | 0 |
| paused | resume | active | 0 |
| active | resume | already_resumed | 0 |
| paused | bind + code | 激活后 active（bind 显式激活） | 有 |

- **没有 unbind**：不归档、不删网关 key、不删 config；网关决定终止。
- paused 地址不 ack；resume 时必须主动 `POST /admin/pending` 拉一次补收（SSE 同连接不重发 `id<=watermark`）。
- 生命周期串行队列 + mutex；文件原子写；重复不重铸 key（除 gateway 返回 rotation）。
- 旧的 archived binding 只读兼容，不再产生新归档。

---

## 8. 收信 / 发信 / session

（沿用 v2 的已验证部分）

- recipient → binding → workspace；paused → 不 ack；无 binding → 不 ack。
- 线程根 `references[0] || in_reply_to || message_id` → `meta.session_id` → followup；无则工作区新建。
- `LocalMeta` 新增 `session_id/workspace_path`；出站 `send_mail` 已写 `In-Reply-To/References`，不再加自定义头。
- 默认工作区兜底（地址有 binding 但 workspace 失效）；无 binding 不 ack。
- system 模式 1 条 domain key 流；B2 每地址 1 条 agent 流；N 个共享地址 = N 条（§12）。

---

## 9. 配置 / 密钥 / 契约

### 9.1 `aimail_gateway.json`（0600）

```jsonc
{
  "gateway_url": "…",
  "admin_key": "<domain key>",     // degraded 时 raw system key
  "system_id": "…",
  "system_name": "…",
  "domain": "…",
  "manager_address": "…",
  "save_raw_snapshots": true,
  "system_home": "/Users/me/.dsh",
  "domains": ["a.com", "b.com"],             // base 多 domain 时写
  "domain_keys": { "a.com": "<domain key>", "b.com": "<domain key>" }
}
```

- `admin_key` = 选中 domain 的 key；`domain_keys` 保存其余 domain 的 key（base 多 domain）。新 domain 后加需重新系统激活/新码补 key。
- 不写 `webhook_host/bridge_key/webhook_url/webhook_secret`。
- 插件的写操作走契约例外：`file-ownership-allowed: dsh self-provisioning; CLI remains read-only`。
- **试点目标**：dsh 完全在插件内闭环，不依赖 CLI；成功后 dsh 可弃用 CLI 维护（CLI 仅保留给其他宿主/遗留）。

### 9.2 `agentmail.json`（0600）

```jsonc
{ "email": "…", "gateway_url": "…", "system_id": "…", "domain": "…",
  "api_key": "<agent key>", "workspace_id": "…", "workspace_path": "…",
  "workspace_title": "…", "paused": false, "expires_at": "…" }
```

- 不写 webhook/session/preset；旧字段只读忽略。

---

## 10. base 网关改造（单系统、多独享域）

### 10.1 定位

- base **单系统、只支持独享域**；没有共享域、多系统、产品/配额/许可。它是 advanced 的简化版（advanced 的 `standalone` feature 是 advanced 自己的，和 base 无关），安全特性和产品能力也不如 advanced 完整。
- base 的系统码申领只针对**当前这一个 system**；system_id 固定，但 **domain 可在该 system 的多个独享域中选择**（`system_domains` 多行）。
- base 的地址码申领只针对当前 system 的独享域地址；没有 shared domain。
- base 与 advanced **各自一套实现**；不共享运行时激活逻辑，只共享对外 API 契约。

### 10.2 激活码模型

- 复用 `activation_codes`（`code_type=address|product`）。
- 系统码：`code_type=product`、`domain=选中的当前 system domain`、`system_id=当前 system id`；`product_id` 可为空。
- 地址码：`code_type=address`、`email_address=绑定地址`、`domain=选中 domain`、`system_id=当前 system id`。
- 新增列/表（幂等）：
  - `activation_codes.applicant_email`（码发给谁；与 advanced 同名，`.ok()` 容忍）；
  - `applicants(email,name,created_at)`、`email_verify(email,code,expires,attempts)`。
  - **不在激活码上存 manager**：申领页没有 manager；manager 只在 `activate-system` 请求里作为系统默认覆盖。
- 系统 manager：base 的 system 记录增加 `manager_address`；默认 = 收码邮箱；激活时可由 `aimail_system` 的 `manager` 覆盖。

### 10.3 公共申领 API / 页面（base 单系统）

| 路由 | 作用 | 参数 |
|---|---|---|
| `POST /api/v1/public/email-verify/send|check` | 邮箱验证 | email/code |
| `POST /api/v1/apply-system` | 申领**当前 system** 的系统码 | applicant_name/email/verify/**domain（从当前 system 的 domain 列表选）**；无 system_name/manager |
| `POST /api/v1/apply-address` | 申领当前 domain 的地址码 | applicant_name/email/verify/agentname；无 shared_domain/manager |
| `GET /api/v1/public/system/domains` | 申领页展示当前 system 的可选 domain | — |

- 码发给申请人邮箱（收码邮箱）；**不是 manager**。
- 申请人姓名幂等；邮箱验证先行；per-email/IP 限流；码邮件投递失败则销毁码。
- `apply-address` 对已 active 的独享域地址也发**轮换码**（不 409）；激活时旧 key **立即失效，无宽限期**。
- 页面：base 目前无前端。新增 **webapp（SPA）**，参照 advanced 的 `apply-system`/`apply-address`/`activate` 页面结构，按 base 单系统/多 domain 调整；**不是简单静态表单**。页面调用上面的 API，不引入 advanced 的产品/配额/共享域页面。

### 10.4 激活 API

| 路由 | 作用 | 返回 |
|---|---|---|
| `POST /api/v1/activate-system` | 当前 system 码激活/续期/轮换 | `raw_key,system_id,domain,domains,manager_address,kind,expires_at` |
| `POST /api/v1/activate-address-code` | 独享域地址码激活/续期/轮换 | `raw_key,system_id,email_address,kind,expires_at` |
| `POST /api/v1/public/activation-code/preflight` | 不消费的码元数据 | `code_type,scope,system_id,domain,email_address,manager_address,expires_at,claimed,is_shipped` |

- `scope`：base 只有 `system`（独享域），不会出现 `shared`；`domains` 返回当前 system 的全部独享域供选。
- `kind`：`new|renewal|rotation|already_active`，由网关决定；插件只应用。
- **系统 manager**：激活请求可带 `manager` 覆盖系统默认值；不带则用收码邮箱。地址激活不接受 manager。
- **续期/轮换**：
  - 系统码：同一 system/domain 再次激活 → 续期/轮换 system key（`kind`）。
  - 独享域地址：active 时再次激活 → key 轮换（新 key，旧 key 失效）；expired → 续期。
  - 不做 unbind/delete；到期/终止由网关决定。
- 保留旧 `/api/v1/activate-address` 作为 `-code` 的别名。

### 10.5 路由可见性与 advanced

- **base 的申领/激活 handler 放在 base 自己的 server/binary 层**（例如 `src/server.rs`/base-only 模块），**不放进共享库的 `create_router`**。
  - base 单机启动时由 base server 挂载；
  - advanced 依赖 base 的库（`aimail_base`），只调用共享 `create_router`，**不会看到/挂载 base 的激活路由**；advanced 用自己的激活/申领路由；
  - advanced 的 `standalone` feature 是 advanced 自己的，和 base 无关；base 不提供 `standalone` 开关。
- 这样不是运行时“隐藏”，而是结构上不进入 advanced 的路由树；两边完全独立。
- 两边只共享 API 契约（路径、字段、错误码），不共享 handler；schema 迁移幂等，advanced 的 `ALTER ... .ok()` 可共存。
- 这样 base 的这套在 advanced 下“作废/不可见”，不会重复注册路由，也不需要把 advanced 的产品/配额逻辑塞进 base。

---

## 11. 高级版（advanced）改动

advanced 保留自己的申领/激活/产品/配额/共享域/面板实现，不改为 base 的策略层。

### 11.1 必须改的

1. **路由可见性**：base 的申领/激活 handler 在 base 自己的 server/binary 层，不在共享库 `create_router`；advanced 依赖库时天然不会挂载；advanced 自己的同名路由保持唯一，无需 feature 开关。
2. **管理员覆盖**：`activate-system` 接受可选 `manager`，写入系统默认 manager；`apply-system/apply-address` 不写 manager（收码邮箱才是码接收者）。
3. **active 续期/轮换**：
   - `apply-address` 对共享域 active 地址允许发续期码（去掉“renewals open only after expiry”的 409）；`activate-address-code` 对共享域 active 地址 extend expiry，返回 `kind:"renewal"`；
   - 独享域 active 地址发轮换码，激活时旧 key **立即失效（无宽限期）**，返回 `kind:"rotation"`；
   - 不再一律 `already_active`。
4. **响应契约**：`activate-system`/`activate-address-code` 统一返回 `kind`、`expires_at`、`manager_address`（地址可空）；与 base 的字段同名同义。
5. **schema 兼容**：base 新增的 `applicant_email/manager_address/applicants/email_verify` 与 advanced 现有表/列共存；advanced 初始化保持幂等。
6. **前端**：`apply-system/apply-address/activate` 继续调用 advanced 自己的同名 API；不依赖 base 页面。

### 11.2 不改的

- 产品、配额、许可、pickup-binding、renew-only、共享域、面板、`/public/products`、`/apply-license` 等全部保留。
- 不把 advanced 的激活逻辑合并进 base；不做 `ActivationPolicy` 抽象。两套实现、同一契约。

### 11.3 回归

- 现有 advanced e2e（activation/quota/renew/shared-boundary/license）保持通过。
- 新增：共享 active 续期、manager 覆盖、base 路由不可见（404/未挂载）测试。

---

## 12. 多地址 SSE 的技术决定

- base 没有共享域，不涉及多地址单流；系统模式 1 条 domain key 流。
- advanced 共享域多个地址：每个地址一个 agent key，agent key 的 pending/SSE 只覆盖自己地址；**不能合并一条 SSE**（共享域 system key 会看到整个共享 system，隐私不允许）。
- **决定（本次）**：不做 applicant-scoped key；advanced 多地址 = N 条 agent 流，一个 Hub、一个队列（通常 N=1）。
- **后续特性（可选）**：gateway 新增 applicant-scoped 多地址 key/stream；插件 Hub 已按“多条流”设计，届时只需减少连接数，不改路由/session 逻辑。这样本次不被该特性阻塞。

---

## 13. 兼容边界（pysdk / openclaw / pi）

dsh 的改动必须是**增量、可选、宿主隔离**的；pysdk（Hermes/deer-flow）、tssdk openclaw/pi 按原有模式运行，不得改变默认行为。

### 13.1 代码边界

| 改动 | 隔离方式 |
|---|---|
| `aimail_system` 工具 | **dsh 专属**，不进共享 `MAIL_TOOLS`/Python tool spec |
| `aimail_address` 的 dsh 行为（email 可选、preflight、no manager、no unbind） | 只在 dsh 适配层覆盖 schema/分支；共享 registry 与 Python 不变 |
| `AgentConfig` 的 `workspace_*`/`domain_keys` | 全部可选；旧 binding/其他宿主忽略 |
| `LocalMeta.session_id/workspace_path` | 可选字段；无 `send_mail` 新头；其他宿主不写 |
| `GatewayClient`/`streamRound` 的 AbortSignal | 参数可选；不传时行为不变 |
| `startAgentPullEntry*` 的 signal | 可选；openclaw/pi 不传时原行为 |
| dsh 的 no push/no bridge/no unbind | 只在 dsh adapter/工具层；core 保留 webhook/push/unbind 给其他宿主 |
| base 网关新 API | 增量路由；保留 `/activate-address` 旧别名；其他宿主旧路径不变 |
| advanced 网关 | 独立实现；只加 `kind/expires_at/manager` 等字段；不改旧语义 |

### 13.2 必须跑的回归

- `pysdk` 全量（Hermes / deer-flow 适配、tool spec parity、激活/入站/拉流）。
- `tssdk` 全量；openclaw/pi 的 host adapter 测试、webhook/push 路径、旧 `aimail_address` 行为。
- dsh：新双入口 + mail-hub；契约例外；L3。
- base/advanced：各自 e2e（base 单系统多 domain；advanced product/quota/shared/renew/license）。
- 跨语言契约：`pysdk/tool-spec.json` 不变；dsh 覆盖层单独 pin。

### 13.3 版本/发布

- 核心接口只增不改；旧字段/旧路由保留。
- tssdk 0.3.0；base/advanced 各自版本；发布顺序 base → advanced → tssdk。
- 若某项必须改共享语义（例如 `aimail_address` 的 email 必填），只允许在 dsh 覆盖层，不改共享 spec。

---

## 14. 端到端验收

### 13.1 插件入口

| # | 入口 | 工具 | 结果 | 核对 |
|---|---|---|---|---|
| A1 | 系统 prompt（base 独享域） | `aimail_system bind{code,gateway_url,manager?}` | 当前 system + `agent@domain` 绑到**默认工作区** | config 0600；domain key；无 raw；pointer/status |
| A2 | 系统 prompt（advanced 共享域） | `aimail_system bind{code,gateway_url,system_name,manager?}` | `agent.system_name@shared` 绑到默认工作区 | 同上 |
| A3 | 系统重复 bind + code | 同上 | `kind=new/renewal/rotation` | key/expiry 更新；不重复建 system |
| A4 | 激活码未绑定且 system_name/domain 都缺 | 同上 | `bad_args` | 无网关调用 |
| B1 | 有 system + 本域（无 code） | `aimail_address bind{email}` | `mode=register` | binding；system 流收信 |
| B2 | 有 system + 本域（code） | `bind{email,code}` | `mode=code` | code 消费；kind |
| B3 | 无 system + 共享域（code，advanced） | `bind{email,code,gateway_url}` | `mode=code` | agent 流收信 |
| B4 | 无 system + 非共享域（base 独享域码） | `bind{email,code}` | `needs_system`（preflight 拒绝） | code 未消费 |
| B5 | 有 system + 共享域/异 gateway | `bind{email}` | `system_mismatch`/`gateway_mismatch` | 无文件变化 |
| B6 | code/email 不一致 | `bind{email,code}` | `activation_invalid` | 不重试 |
| L1 | 重复 bind（无 code） | `bind{email}` | `mode=link` | 0 网关调用 |
| L2 | pause/resume | `pause/resume` | 幂等 | 流/路由停/恢复；pending 补收 |
| L3 | 新线程/回复 | 入站 | 新/原 session | 线程头 |
| L4 | 默认工作区缺失 | 系统 bind | `workspace_invalid` | 不绑当前工作区 |

### 13.2 网关闭环

| # | 场景 | base | advanced |
|---|---|---|---|
| G1 | 系统申领页 | 当前单系统码：邮箱验证 → 码邮件（无 domain/system_name 选择） | 产品/共享域/独享域选择 → 码邮件 |
| G2 | 地址申领页 | 当前域地址：agentname → 码邮件 | 共享域/产品 → 码邮件 |
| G3 | preflight | type/scope/system/domain/email/expiry；不消费 | 同左（含 shared） |
| G4 | activate-system | 当前 system；`kind`；manager 覆盖；不 unbind | 产品/绑定校验；`kind`；manager 覆盖 |
| G5 | activate-address-code | 独享域地址：new/renewal/rotation；email 必须匹配 | 共享域 active 续期；独享域轮换 |
| G6 | 路由可见性 | base server 挂载自己的申领/激活 | base 路由不在共享库；advanced 唯一 |
| G7 | 回归 | base 单系统 e2e | product/quota/renew/shared-boundary/license e2e |

### 13.3 核对命令

```bash
stat -f '%Sp' ~/.aimail/systems/<sid>/aimail_gateway.json   # -rw-------
jq '{system_id,domain,system_name,admin_key:(.admin_key|length),webhook_host,bridge_key}'   ~/.aimail/systems/<sid>/aimail_gateway.json
test ! -e ~/.aimail/systems/<sid>/.system_raw_key.key && echo raw-key-removed
jq '{email,system_id,workspace_path,webhook_url,paused,expires_at}'   ~/.aimail/systems/<sid>/<addr>/agentmail.json
jq '{mode,connected,streams,routes,paused}' ~/.dsh/profiles/<profile>/aimail/status.json
lsof -nP -iTCP:9099 -sTCP:LISTEN || echo no-inbound-port
```

---

## 15. 实施步骤（按依赖解耦）

### 15.1 接口冻结（W0，所有仓库的前置）

不写运行时逻辑，只冻结契约与夹具：

| 冻结项 | 内容 |
|---|---|
| 公共 API 契约 | `apply-system/apply-address/email-verify/preflight/activate-system/activate-address-code` 的路径、请求/响应字段、错误码、`kind` |
| 工具契约 | `aimail_system`/`aimail_address` 的 schema、prompt、结果 envelope |
| 配置契约 | `aimail_gateway.json`/`agentmail.json` 字段（无 webhook/session） |
| 夹具 | fake gateway（base/advanced 两套响应）+ fake ctx/agents，供插件单测 |
| 契约测试 | 字段/路径/错误码 pin；base/advanced 响应与插件解析的 conformance |

冻结后各工作流可并行，互不依赖代码。

### 15.2 并行工作流

| 流 | 内容 | 依赖 | 产出 |
|---|---|---|---|
| **B（base）** | schema 增列/表 → 当前单系统 **webapp + API（多 domain 选择）** → activate-system/address-code/preflight → per-domain keys → 续期/轮换 → 测试 | W0 | base 单机可用 |
| **A（advanced）** | 确认 base 路由不在共享库 → active 共享续期/manager/响应字段 → 回归 | W0 + base schema（仅幂等列） | advanced 保持并新增能力 |
| **T（tssdk tools）** | `aimail_system`/`aimail_address` + preflight + manager + 默认工作区 + no unbind + 续期/轮换 | W0（fake gateway） | 插件工具可用 |
| **H（mail-hub）** | system/地址流、router、线程/session、队列/ack、默认工作区、pause/resume | W0 + T 的配置 | 收信闭环 |
| **C（契约例外/CLI）** | `aimail_gateway.json` checker 例外；删除 CLI dsh 路径 | 独立 | 无 CLI 闭环 |
| **E（集成）** | T+H 对 base；T+H 对 advanced；桌面/L3 | B/A/T/H/C | 端到端 |

### 15.3 依赖图

```
W0 契约冻结
 ├─ B base ───────────────┐
 ├─ A advanced ───────────┤
 ├─ T tools（fake）── H hub（fake）──┐
 └─ C 契约例外/CLI 删除 ─────────────┤
                                      └─ E 集成（base/advanced/桌面/L3）
```

- T/H 不依赖 base/advanced 代码，只依赖 API 契约与 fake；可先完成。
- A 只依赖 base 的幂等 schema 增列，不依赖 base 的 handler/路由。
- B 与 A 不共享 handler；只共享契约。
- C 独立，可在任意阶段做；若要在 PR 中先合入插件写 config，必须先做 C。

### 15.4 实施顺序与门禁检查点

1. W0 契约/夹具/契约测试（1 周）。
2. 并行：B、A、T、H、C（2–3 周）。
3. **B 门禁**：base CI L0（fmt/clippy/test/windows）+ base L1/L2（gateway + 新 base-only 套件）→ base tag vX。
4. **A 门禁**：advanced 升级 base 到 vX → base clean/二进制新鲜 → advanced L0/L1/L2 → advanced tag vY。
5. **T/H 门禁**：SDK L0（tsc/vitest）+ sdk-e2e + openclaw/pi docker 回归 → tssdk 0.3.0。
6. **C 门禁**：CLI 门禁（route contract/registry/L1/L2）更新；L3 dsh-aimail 移除/替换为 dsh-plugin。
7. **E 集成**：T+H 对 base（单系统多独享域）/advanced（共享域/产品）；桌面；L3（released artifacts）。
8. 文档/版本/试点结论。

---

## 16. 多仓门禁、发布依赖与兼容

这一节是代码查验结论（主要依据 advanced 仓的 `tests/RELEASE-GATES.md`、`tests/test-matrix.json`、`tests/run-e2e.sh`、`tests/advanced/run-advanced-gate.sh`、base 仓 `.github/workflows/release.yml`、aimail 仓 `tests/sdk/l0-gate-tests.sh`、`.github/workflows/l3.yml`）。跨仓改动必须按这些门禁排发布，不能只按代码依赖排。

### 16.1 现有门禁地图

| 产物 | 门禁 | 关键前置/判定 |
|---|---|---|
| **base**（公开） | `.github/workflows/release.yml`：fmt · clippy · `cargo test` · Windows check；tag 前同一套 | Rust 1.98.1；tag 前必须绿 |
| **advanced**（私有） | 三层门禁（本地跑，无 GH Actions）：`tests/advanced/run-advanced-gate.sh [--release]` → `tests/run-e2e.sh --tier l0|l1|l2` | **base 仓 clean（zero-diff）** + **base 二进制新鲜**；L0 = 两仓 fmt/clippy/cargo test；L1 = base API/契约 + panel-smoke + dispatch-contract；L2 = gateway 全套 + advanced category-21..25 + open-apply + 面板 + warning-gate + SDK agent-level |
| **SDK/tssdk** | `tests/sdk/l0-gate-tests.sh`（pyflakes + pytest + tssdk `tsc` + `vitest`）；`SDK/sdk-e2e`；`SDK/docker-regression`（hermes/deer-flow/openclaw/pi）；`check-versions.sh` | 版本闸 + 五包发布批次；tssdk 变更必须过 tsc/vitest |
| **CLI** | `tests/cli/run-cli-gate.sh` L0/L1/L2；`check-route-contract.py`、registry、dsh host 安装 | 删除 CLI dsh 路径会改这些门禁 |
| **L3** | `.github/workflows/l3.yml` / `tests/l3/run-l3.sh`：hermes / dsh-aimail（CLI 入口）/ dsh-plugin（插件入口） | 发布后 released artifacts；当前只测 base admin-key 激活 |
| **测试注册表** | `tests/test-matrix.json` + `check-test-matrix.py`（`run-e2e.sh` 预检；新增一级脚本未登记即红） | 新套件/脚本必须登记 level/scenario |

关键机制：

- **advanced 的 tag 前置**：`run-e2e.sh` 的 `base-zero-diff` 预检要求 base 仓无未提交/未跟踪文件；base 二进制必须比源码新。**base 改动必须先提交/打 tag，再跑 advanced 门禁**。
- **gateway 套件在 advanced 下也要跑**：`run-e2e.sh --release advanced` 会用 advanced 二进制跑 `GATEWAY_SUITE`（base 行为必须在高级构建下同样通过）。因此 **base-only 的测试不能进 `GATEWAY_SUITE`**；用 `BASE_ONLY_SUITES`（现有 `advanced/category-24` 就是范例）标记，否则 advanced 门禁会 404/误红。
- **判定口径**：套件内部 FAIL 计数优先于进程退出码；`rc=2` = 无法判定（缺前置），不是通过。
- **advanced 是跨仓测试总根**：`AIMAIL_REPO` 指向 aimail 仓，SDK/CLI/L3 脚本都在该仓 `tests/`；`test-matrix.json` 是唯一真源。
- **L3 与 L2 分工**：L2 是发布前工作树 + docker 宿主；L3 是发布后 released artifacts + 原生 OS；L3 当前只测 admin-key 激活，不测激活码。

### 16.2 本次改动对各门禁的影响

| 改动 | 受影响门禁 | 必须做 |
|---|---|---|
| base 新激活 API/webapp/schema/多 domain | base CI L0；advanced L1/L2；test-matrix | base `cargo test`/fmt/clippy；新增 base-only 套件并登记；base clean/tag |
| base 申领/激活路由只进 base server/binary | advanced 的 `GATEWAY_SUITE` | 确认 base 路由不在共享 `create_router`；advanced 不 404；base-only 套件加入 `BASE_ONLY_SUITES` |
| advanced active 共享续期/manager/响应字段 | advanced L1/L2（category-24/25/e2e-open-apply） | 更新 advanced 套件断言；不改变 gateway 套件 |
| tssdk `aimail_system`/`aimail_address`/mail-hub | SDK L0（tsc/vitest）、SDK e2e、L3 | 新工具单测；旧 openclaw/pi 用例不回归；版本闸 |
| pysdk/openclaw/pi 兼容 | SDK L0/e2e/docker | 不共享 spec 变更；跑全量 |
| CLI dsh 路径删除 | CLI L0/L1/L2、L3 dsh-aimail | 改 `check-route-contract`/registry/CLI L2；L3 去掉 CLI dsh cell 或替换为 plugin-only |
| base/advanced API 契约 | 三仓 conformance | frozen fixtures；base-only/advanced 各自断言；旧 `/activate-address` 别名 |

### 16.3 必须新增/调整的测试

1. **base-only 套件**（不进 advanced 的 `GATEWAY_SUITE`）：
   - 位置：base 仓 `cargo test`/integration 覆盖 handler；跨仓 e2e 放 advanced 测试根的 `tests/gateway/category-26-base-activation.sh`，并加入 `BASE_ONLY_SUITES`；`test-matrix.json` 登记（scenario 5）。
   - 系统码申领（单系统多 domain 选择）→ 邮件码 → `activate-system` → `domains/domain_keys` → 默认工作员 `agent@{selected}`；
   - 地址码申领（当前 domain）→ `activate-address-code`；独享域 active 轮换（旧 key 立即失效）；
   - `preflight`：不消费、type/scope/domain/manager/expiry；
   - 多 domain：选 A 域、B 域各绑一个地址；`domain_keys` 路由；
   - base webapp smoke（参照 advanced `panel-smoke.py` 的 headless 方式；至少 apply/activate 两个流程）。
2. **advanced 套件**：
   - active 共享域续期（`kind=renewal`，extend expiry）；
   - manager 覆盖（activate-system 写入系统默认，不需确认）；
   - base 路由不可见（base 的 `/apply-*`/`/activate-*` 在 advanced 下 404/未挂载）；
   - 旧 product/quota/shared-boundary/license 回归。
3. **tssdk**：
   - `aimail_system`/`aimail_address` 单测（fake gateway）；preflight 异常；manager；默认工作区；no unbind；多 domain key；
   - mail-hub：system/地址流、线程/session、pause/pending 补收；
   - openclaw/pi 旧适配测试；pysdk 不变。
4. **test-matrix**：
   - 新增 **scenario 5**：dsh 插件内闭环 + base 系统码/地址码申领/激活（系统码 → 默认工作员 → 地址绑定 → 收信；多 domain），level L2/L3；
   - 新套件登记（glob/role/level/scenario）；`check-test-matrix.py` 通过；
   - L3 matrix：`dsh-aimail`（CLI 入口）在 CLI dsh 路径删除后移除/替换；`dsh-plugin` 改为新工具闭环。
5. **CLI**：
   - 删除 dsh 路径后更新 `check-route-contract.py`、registry、CLI L1/L2 的 dsh 断言；
   - 如果 CLI 整体保留给其他宿主，去掉 dsh host 但仍跑其他 host；如果 CLI 整体退役，移除 CLI gate/L3 引用。
6. **契约**：
   - base/advanced/tssdk 三份 conformance fixtures；字段/错误码/`kind`；
   - 旧 `/activate-address`、旧 `/apply-*`（advanced）不回归；
   - pysdk `tool-spec.json` 不变；dsh 覆盖层 pin。

### 16.4 发布依赖与检查点

```text
[1] base 改动完成
    ├─ base CI L0（fmt/clippy/test/windows）
    ├─ base L1/L2（gateway 套件 + 新 base-only 套件）
    └─ base tag vX
          ↓
[2] advanced 升级 base 依赖到 vX
    ├─ base 仓 clean + base 二进制新鲜（zero-diff 预检）
    ├─ advanced L0（两仓 fmt/clippy/test）
    ├─ advanced L1（base API/契约 + panel + dispatch）
    ├─ advanced L2（gateway 全套 + advanced 21..25 + open-apply + SDK agent-level）
    └─ advanced tag vY
          ↓
[3] tssdk（aimail 仓）0.3.0
    ├─ SDK L0（pyflakes/pytest/tsc/vitest）
    ├─ SDK L1（sdk-e2e）
    ├─ openclaw/pi 宿主回归（docker）
    ├─ check-versions（五包批次）
    └─ 发布 SDK
          ↓
[4] L3（released artifacts）
    ├─ hermes / dsh-plugin（plugin-only，新闭环）
    ├─ 去掉/替换 dsh-aimail（CLI 入口）
    └─ base/advanced 的 code 申领/激活闭环（post-release）
```

- **版本/依赖固定**：advanced 当前 `Cargo.toml` 用 `path = "../aimail-gateway"`；发布时必须记录 base 的 **tag/commit**（release notes + 可复现 checkout），否则 advanced tag 无法复现。建议在 advanced 发布流程中固定 base ref（submodule 或 git tag 依赖；本地测试仍可用 path）。
- **顺序不可颠倒**：base 先 tag；advanced 后 tag；tssdk/SDK 最后；L3 最后。base 改动未 tag 时，advanced 的 zero-diff/新鲜度门禁会拒绝。
- **回滚**：base/advanced 各自 tag 回滚；tssdk 只依赖 API 契约，不受 base/advanced 内部实现影响；CLI 删除单独回滚。

### 16.5 兼容红线

- 所有 base/advanced 旧 API 路径与字段保留；新增字段只增不改；旧 `/activate-address` 别名。
- base 的申领/激活 handler 不进入共享 `create_router`；advanced 不挂载 base 路由。
- schema 迁移幂等（`IF NOT EXISTS`/`ALTER ... .ok()`）；base 新列与 advanced 现有表共存。
- pysdk/tssdk 共享 tool spec 不变；dsh 覆盖层单独 pin；openclaw/pi/webhook 旧路径不变。
- 判定“绿”必须看套件内部 FAIL 计数，不看单进程退出码；`rc=2` 不当通过。

---

## 17. 决策与说明

### 17.1 已定

- **base/advanced 分离**：base 单系统、多独享域；advanced 多系统/共享域/产品；各自实现，只共享 API 契约。base 的申领/激活 handler 在 base 自己的 server/binary 层，不放进共享库 `create_router`；advanced 依赖库时天然不挂载。`standalone` 是 advanced 自己的 feature，与 base 无关。
- **base 多 domain**：已确认采用 **per-domain key**：系统激活时读 `domains`，为每个 domain 铸 domain key，`admin_key`=选中 domain，其余存 `domain_keys`；新 domain 后加需重新系统激活/新码补 key。
- **manager**：申领页/API 没有 manager，收码邮箱是码接收者；`aimail_system bind` 的 `manager` 只覆盖系统默认 manager，不需要管理员/收码邮箱确认；地址绑定不接受 manager。
- **续期/轮换**：advanced 共享域 active 允许续期（extend expiry，`kind=renewal`）；独享域 active 走 key 轮换（`kind=rotation`），旧 key **立即失效**；网关决定，插件应用。
- **多地址单流**：本次不做 applicant-scoped key；advanced 多地址 = N 条 agent 流；Hub 已按多流设计。
- **CLI**：dsh 试点完全在插件内闭环；**删除 CLI 的 dsh 路径**（register-cli/default binding/inbound notify/`--system-only` 反向调用），不保留诊断分支；CLI 整体退役时 dsh 路径一并删除。
- **不做 unbind**：插件不提供 unbind；到期/终止由网关决定；新 code 重新 bind = 续期/轮换。
- **发布顺序**：base → advanced → tssdk/SDK → L3（已确认）。

### 17.2 契约例外是什么、有什么影响

仓库的 `tests/contract/check-file-ownership.py` 是一个 **CI 静态门禁**：

- 规则：`aimail_gateway.json`（系统级环境文件）只能由 CLI 写；`tssdk/**` 只读；`agentmail.json` 只能由 SDK 写。
- 影响：dsh 插件要在无 CLI 的情况下写 `aimail_gateway.json`，不改就会 **CI/契约测试失败**，PR 不能合。
- 解决：在写调用处加 `file-ownership-allowed: dsh self-provisioning; CLI remains read-only` 标记（checker 列为 allowed 并单独打印），或 baseline 白名单。**只影响 CI/审计，不影响运行时**；CLI 仍只读。
- 推荐 marker（可见、可审计、无需维护 baseline）。

### 17.3 门禁层面的待办（不是产品决策）

1. **base-only 套件登记**：新 base 激活/webapp/多 domain 套件必须放进 `BASE_ONLY_SUITES`（或等价排除），不能进 advanced 的 `GATEWAY_SUITE`，否则 advanced 门禁 404/误红；同时登记 `test-matrix.json`。
2. **新增 scenario 5**：dsh 插件内闭环（系统码 → 默认工作员 → 地址绑定 → 收信）加入 `test-matrix.json` 与 tests/README；`check-test-matrix.py` 必须通过。
3. **advanced 固定 base ref**：advanced 当前 `path` 依赖；发布时必须记录/固定 base tag 或 commit（submodule/git tag；本地可用 path），并在 `preflight-release.sh`/release notes 校验；否则 advanced tag 不可复现。
4. **CLI 门禁同步**：删 dsh 路径时同步改 `check-route-contract.py`、registry、CLI L1/L2 的 dsh 断言；L3 的 `dsh-aimail`（CLI 入口）移除/替换为 `dsh-plugin`（插件闭环）。
5. **base webapp 测试**：参照 advanced `panel-smoke.py` 的 headless 方式，至少覆盖 apply-system/apply-address/activate 两条流程。
6. **L0 硬门**：base/advanced 的 fmt/clippy/cargo test；tssdk 的 tsc/vitest；不要用 `GATE_LINT=0`/跳过门禁来“通过”。
7. **判定口径**：套件内部 FAIL 计数优先于退出码；`rc=2` 是“无法判定”，不得当绿。

### 17.4 兼容红线

- base/advanced 旧路径/字段保留；新增字段只增不改；旧 `/activate-address` 别名。
- base 激活 handler 不进共享 `create_router`；advanced 不挂载 base 路由。
- schema 迁移幂等；base 新列与 advanced 现有表共存。
- pysdk/tssdk 共享 tool spec 不变；dsh 覆盖层单独 pin；openclaw/pi/webhook 旧路径不变。
