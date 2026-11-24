# 激活码 / 域名 / 回复激活统一改造方案（待评审）

状态：**Draft — 未批准执行**  
日期：2026-11-24  
说明：上一轮审批属于误触发，不能作为执行依据。本文是供再次评审的实际方案文档；**评审通过前不再继续改代码、不发版**。

---

## 0. 当前工作树状态（重要）

上一轮误触发后，工作树里已有**未提交的 WIP**，不能视为已批准实现：

- base：
  - `src/core/api/activation.rs`
  - `src/core/config.rs`
  - `src/core/storage.rs`
  - `src/server.rs`
  - `src/main.rs`
- advanced：
  - `src/advanced/api/activation.rs`
  - `src/advanced/api/address.rs`
  - `src/advanced/api/open_apply.rs`
  - `src/advanced/config.rs`
  - `src/advanced/storage.rs`
  - `src/advanced/strategy.rs`
  - `frontend/dist/assets/js/pages/public/apply-address.js`

WIP 覆盖了部分 P0–P3 和 P4 的邮件配置/文案，但：

- P4 的 SMTP 回复拦截、服务抽取、幂等/防环未完成；
- P5 standalone 未开始；
- 新 API/前端/回复激活测试未补齐；
- 没有完整 fmt/clippy/test 结论；
- 没有 commit、tag、发布或 pin 更新。

决定：**保留 WIP 修正，不重做**。现有 P0/P1 与部分 P2/P3 符合现有框架（emails/InboundInterceptor/system_domains），可继续使用；P4 旧的 activation_reply_address 配置和内存/独立表写法必须删除并按本方案改写，P5 从零补。所有缺口补齐后重新跑完整测试。

### 0.1 与上一版的确认差异

1. advanced 激活邮箱**固定**为 `activate@{smtp.hostname}`，不再新增配置项。
2. 激活码邮件把该邮箱加入 **Cc**；Cc 自收副本以**邮件形式**写入现有 `emails` 表（不新建表、不进入普通投递链），按 activation code 失效时间（无过期则默认 24h）由现有清理机制回收。
3. 邮箱验证统一为 **name + email + verification code** 三元匹配。
4. **冻结**既有地址激活绑定接口/prompt：`activate-address-code` 与 `aimail_address` bind 行为不变。
5. base config 只填域名数组，其他参数通过 API 修改。
6. standalone 写接口要求 `system_admin` key。
7. 旧 shared_system 只有已激活 identifier 可用；未激活的 activation code 过期后自然回收。
8. **不做历史数据迁移/回填**。

---

## 1. 目标与边界

本方案同时解决四件事：

1. base 增加配置文件多 domain，同时保留现有 domain API。
2. advanced 兼容“系统级共享域 / 独享域激活码申请 → 回复邮件完成系统激活 → 地址申请页可用”。
3. advanced 地址申请页改为“先验证 Your Details，再展示个性化 Address Type & Domain”。
4. advanced standalone 增加类似 base 的地址申请页，domain/address 受 license 限制，domain 可在页面添加。

不在本次范围：

- payment；
- DNS 自动注册；
- 历史数据迁移/回填（本次明确不做）；
- 自动删除历史 domain/activation code。

---

## 2. 当前代码事实

### 2.1 base

- `AdminConfig` 只有 `email` / `manager_default`，没有 domain 列表。
- 地址申请页 `GET /api/v1/public/system/domains` 读取 `current_system_domains()`：
  - 来源是 DB `system_domains` 的 active 裸域；
  - 不是配置文件。
- domain 写入靠 `POST /api/v1/admin/systems/{sid}/domains`（system scope，只接受裸域）。
- base 是单系统，不需要租户过滤；`current_system_domains()` 当前返回全表 active 裸域，在 base 单系统下成立。

### 2.2 advanced

- `shared` 域名来自 config `admin.shared_domains`。
- `shared_system` 的 `system_name` 是申请页自由输入，只做格式校验，不校验是否已激活/已归属。
- `dedicated` 页面从 DB `system_domains` 列 active 裸域，但 `apply_address` 用 `lookup_domain_addr(&address)` 查**完整地址** `agentname@domain`，而不是裸域；只预注册裸域会失败。e2e fixture 同时插入裸域和 exact address，掩盖了这个前提。
- `address-state` 不接收 `system_name`，shared_system 的状态检查对象错误。
- 系统激活 `activate_system` 是 HTTP API；系统码邮件 `email_activation_code` 没有 `Reply-To`，也没有 inbound reply 处理器。
- standalone 构建的 `activation_public` 路由目前是空 Router；license 已有 `max_domains` / `max_addresses`，但 standalone 没有地址申请页/域名添加页。
- `systems` 表没有可靠的 applicant email 字段；`admin_email` 在 product code 路径可能是 `admin@{domain}`，不能作为“申请人拥有的系统”的唯一依据。

---

## 3. 目标架构：共享工具层 + 两套服务策略

### 3.1 共享工具层（base crate）

收敛到 `aimail_base::core::api::activation`（纯函数，不依赖 DB/HTTP）：

- `address_activation_prompt(address, gateway_url, code)`：唯一邮件 prompt。
- `generate_key_material()`：key + sealed hash。
- `generate_activation_code(prefix)`：唯一激活码格式。
- `valid_agentname(raw)`：唯一 agentname 规则。
- `normalize_bare_domain(raw)` / `valid_bare_domain(raw)`：裸域规范化与校验。
- `extract_activation_code(text)`：从回复邮件正文/引用中提取一个 code；多码/无码返回明确错误。
- `ActivationCodeKind` / `ActivationTarget` 纯数据类型：`SystemShared` / `SystemDedicated` / `Address`。
- `activation_code_allowed_for_reply(kind)` 等纯判断。

advanced 通过 `aimail_base` 使用这些工具；**事务体 `act_in_tx` 仍留在 advanced**，因为它是 advanced 的并发/续期/独享域策略，不是工具函数。

### 3.2 服务策略

- base：单系统、无租户；domain 从 config seed + DB API，两者共用 `system_domains`。
- advanced：多租户；系统激活/资源归属/地址申请是服务层策略，必须保留 applicant/system 作用域。
- standalone：单机 license 策略；domain/address 上限来自 license，不使用 shared_domains 产品体系。

### 3.3 邮箱验证码三元匹配

base 与 advanced 的 `email_verify` 都改成：

```rust
send(email, name) -> code
verify(email, name, code) -> Ok/Err
```

- `send` 时把 name 写入验证码条目（与 email 绑定）。
- `verify` 必须 name、email、code 三者同时匹配；name 大小写按现有 applicant 绑定规则处理（建议精确匹配）。
- 所有 `apply-address` / `apply-system` / `address-options` 调用的都是三元 `verify`。
- 旧的两参数调用不再作为业务入口；避免“只知道邮箱和验证码即可申请”的伪造面。

---

## 4. base 多 domain 配置

### 4.1 配置格式

在 `[admin]` 增加**纯域名数组**，不配置 id/webhook/manager 等其他参数：

```toml
[admin]
domains = ["mail-a.example.com", "mail-b.example.com"]
```

对应：

```rust
pub struct AdminConfig {
    // ...
    pub domains: Vec<String>,
}
```

校验：

- 必须是裸域：非空、含 `.`、无 `@`、无空格；
- 小写规范化后去重；
- 不允许 `admin.relay`；
- 失败在 `Config::validate()` fail-fast。

其他 domain 参数（id/webhook/manager/is_active）只能通过现有 API 修改；config 只负责声明“启动时应存在的域名集合”。

### 4.2 启动 seed 语义

在 base 启动、DB schema 和 bootstrap system 就绪后执行 `seed_config_domains()`：

1. 对每个 config domain 规范化为小写。
2. 查 `system_domains` 是否存在同裸域行。
3. 不存在 → 用 bootstrap system id 插入（id 用确定性的 `cfg-{sanitized-domain}` 或 UUID；建议确定性 id）。
4. 已存在 → **不覆盖** API 设置的 `is_active/webhook/manager`。
5. 配置移除 domain → **不删除** DB 行；API 仍是运行期管理入口，避免误删已有地址。

结果：

- config 负责“初始可用 domain 集合”；
- API 继续负责运行期新增/修改/停用；
- 两者写同一张 `system_domains`，地址申请页自然看到全部 active 裸域；
- base 单系统不做 tenant 过滤。

### 4.3 兼容

- 现有 API 完全不变，已有 DB domain 不被覆盖/删除。
- 只读/写入路径不变；`current_system_domains()` 不需要改。
- 增加配置解析、seed 幂等、API 与 config 共存测试。

---

## 5. advanced 系统级激活：回复邮件完成激活

### 5.1 固定激活邮箱

激活邮箱固定为：

```text
activate@{smtp.hostname}
```

- `smtp.hostname` 是已有必填配置（EHLO/PTR），不新增配置项。
- 激活邮箱不通过 config 开关；advanced 始终启用该固定地址。
- 该地址必须能在 SMTP 层接收；RCPT 阶段作为特殊地址接受，不要求存在真实投递地址。
- 部署前提：`smtp.hostname` 的域必须能回投到本 gateway（本机 MX/relay 或可路由到本 gateway），否则 Cc 副本无法形成确认源。

### 5.2 邮件侧

系统码邮件（`email_activation_code`）：

- 收件人保持申请人邮箱。
- **Cc 加入 `activate@{smtp.hostname}`**；同时设置 `Reply-To: activate@{smtp.hostname}`，让用户的回复进入激活邮箱。
- 发送时生成/记录 `Message-ID`（outbound headers），并写入 `x-aimail-activation-code-hash`、`x-aimail-applicant-email`；Cc 副本继承这些 headers，回复靠 Message-ID 关联。
- 正文明确：回复本邮件即可激活；保留 code/identifier/domain 信息。
- 地址码邮件不变（继续由 agent 用 `aimail_address` 激活）。
- 只对 system/product code 增加回复说明；address code 不提示回复。

### 5.3 使用现有 InboundInterceptor 处理特殊邮件

不新增 SMTP 旁路，也不新增独立投递容器。系统激活邮件和 Cc 副本都走现有链路：

1. `ConnectionHandler::process_data_end_post` 解析 MIME、构造 `EmailRecord`、写入 `emails`（`direction=inbound`）。
2. 现有 `EmailFactory` 的 `InboundInterceptor` 链在 `process_email_webhook` 中、**webhook/pending 投递之前**执行。
3. advanced 注册一个 `ActivationMailInterceptor`（实现现有 `InboundInterceptor` trait），识别收件人是否为 `activate@{smtp.hostname}`。
4. 命中后返回 `InterceptorDecision::Handled`，跳过 webhook/pending 投递；不产生转发，不产生待投递记录。

为了能通过 RCPT/收件人解析，启动时在现有 `system_domains` 中幂等补一条特殊路由记录：

```text
domain_addr = activate@{smtp.hostname}
system_id   = 现有 bootstrap system id
is_active   = 1
```

这只是现有收件人解析所需的 domain/address 记录；该路由记录本身不保存用户邮件。

### 5.4 Cc 副本作为确认源：保留在现有 emails 记录中

系统激活码邮件：

- 收件人：申请人；
- Cc：`activate@{smtp.hostname}`；
- Reply-To：`activate@{smtp.hostname}`；
- 使用现有 outbound `emails` 记录和 `headers.message_id`。

Cc 副本进入 inbound 后：

- `ActivationMailInterceptor` 识别为“本 gateway 发出的激活邮件副本”：
  - `record.sender == system_sender`
  - `record.recipients` 含 `activate@{smtp.hostname}`
  - `headers.message_id` 存在
- 在 `emails.headers` 写入确认标记（复用现有 headers 字段，不新增字段/表）：
  - `x-aimail-activation-confirm: 1`
  - `x-aimail-activation-code-hash: <sha256>`
  - `x-aimail-applicant-email: <email>`
- 返回 `Handled`，跳过转发/投递。
- **保留这条 `emails` 记录**作为确认源；不在 interceptor 中删除。

保留与清理使用现有机制：

- `finalize_after_delivery` 对 activation-confirm 记录特殊处理：标记为 `delivered`，不立即 `cleanup_completed_email`。
- 不新增字段；使用现有 `emails.last_sent_at` 作为保留时间：
  - activation code 的 `expires_at` 有值 → `last_sent_at = expires_at`；
  - 无过期 code → `last_sent_at = now + 1440 分钟（默认 24h，现有 `default_code_validity_mins`）`。
- 现有 `expired_delivered` 清理批次按 `status=delivered AND last_sent_at < now - delivery_window` 删除；因此记录在 code 失效后由原有机制回收。
- code 激活成功后直接删除对应 activation-confirm `emails` 记录。
- 不新增 `retention_until`/新表；不会永久占用 DB。

### 5.5 用户回复处理

用户回复到 `activate@{smtp.hostname}`：

- 同样先写入 inbound `emails` 记录，进入 `InboundInterceptor`。
- 识别为回复（不是 Cc 副本），按现有 `headers.in_reply_to` / `headers.references` 找到对应的 activation-confirm `emails` 记录。
- 校验：
  - 确认记录的 `x-aimail-activation-code-hash` 与回复正文中提取的 code hash 一致；
  - 回复 `record.sender` 与确认记录的 `x-aimail-applicant-email` 一致；
  - activation_codes 为 system/product code、未 claimed、未过期、`is_shipped=1`。
- 调用抽取后的 `activate_system_from_code` 完成激活。
- 使用现有自动回复机制（`enqueue_outbound_simple` / 现有 auto-reply helper，`X-AIMail-AutoReply`、`In-Reply-To`）回复处理结果（成功/失败/已激活/已过期），不新建发送路径，不回传 raw key。
- Cc 副本缺失/已清理时，自动回复“激活邮件已失效，请重新申请激活码”，不进入激活。
- 成功后删除确认 `emails` 记录和本条回复 `emails` 记录；返回 `Handled`，不转发、不投递。
- 失败也删除本条回复记录（或按现有失败处理保留审计）；只写审计日志，不长期保留邮件。

防伪造：

- 没有对应的 activation-confirm `emails` 记录 → 拒绝。
- `In-Reply-To`/`References` 不匹配 → 拒绝。
- 确认记录的 sender/code hash 与回复不一致 → 拒绝。
- Cc 副本必须由 gateway 自己接收并建立确认记录；仅伪造正文 code 不能通过。

### 5.6 审计日志

使用现有 tracing，不新增邮件存储：

- 系统级/地址级激活码下发均记录 `activation_code_email_enqueued`：`email_id/message_id/code_prefix/code_hash/applicant/product/kind`
- `activation_code_email_delivered` / `activation_code_email_failed`
- `activation_cc_confirmation_stored` / `activation_cc_confirmation_cleaned`
- `activation_reply_received` / `activation_reply_activated` / `activation_reply_rejected` / `activation_reply_auto_replied`
- 日志不写 raw code、不写邮件正文。

本方案只新增上述结构化审计；不改变系统级激活邮件/地址激活邮件原有的保存与清理机制。

### 5.7 服务抽取

把 `activate_system` 的核心逻辑抽成：

```rust
activate_system_from_code(
    state,
    code_record,
    actor_email,
    requested_target: Option<Target>,
) -> ActivationOutcome
```

- HTTP `POST /activate-system` 仍解析请求、做 rate-limit/lockout，然后调用同一服务。
- Inbound interceptor 用 code 的 bound target 调用同一服务。
- 系统创建时写入 `systems.applicant_email`（见 §6），供后续地址 options 使用。

### 5.8 兼容

- 现有系统码仍可走 API 激活；回复激活是新增路径。
- 已 claimed/expired code：回复只发“已激活/已过期”通知，不重复消费。
- 无 `applicant_email` 的旧 code：回复无法校验申请人 → 拒绝并提示使用原 API/面板，不猜测身份。
- 不改变现有 code 的绑定资源语义。

## 6. advanced 地址申请：先验证，再个性化选项

### 6.1 数据模型

- 新增 `systems.applicant_email TEXT NULL` + index；只用于新写入，**不做历史数据迁移/回填**。
- 系统激活成功时从 code 的 `applicant_email` 写入；无值则留空，系统不出现在个性化选项中，直到管理员补录。
- 不扫描/改写已有 systems、activation_codes 或 domains。

新增查询：

- `list_owned_systems(email, name)`：按 `applicant_email`/`manager_address` + 活跃状态过滤。
- `list_owned_shared_identifiers`：shared 产品系统的 `system_quotas.system_name` + 共享域。
- `list_owned_dedicated_domains`：`system_domains` 裸域，排除 shared 配置域。
- 每个资源附 `max_addresses` / 已用 address 数 / 可用状态。

### 6.2 验证后选项 API

新增：

```http
POST /api/v1/public/address-options
{
  "applicant_name": "...",
  "applicant_email": "...",
  "email_verify_code": "123456"
}
```

服务端：

1. **name + email + verification code 三元匹配**：`email_verify::verify(email, name, code)`；验证码条目在 `send` 时同时记录 name，三者任一不匹配即拒绝。
2. `upsert_applicant`，name 与已有绑定不一致 → 409。
3. 只返回该 applicant 拥有的资源：

```json
{
  "shared_domains": [{ "domain": "...", "name": "...", "validity_days": 3 }],
  "shared_identifiers": [{ "system_id": "...", "identifier": "sys1", "domain": "...", "addresses_left": 4 }],
  "dedicated_domains": [{ "system_id": "...", "domain": "...", "addresses_left": 2 }]
}
```

- 不返回全站 domain 列表；advanced 多租户必须过滤。
- 配额用尽/过期的资源不返回或标记 `full`。
- base 不需要这个 API（单系统页面直接读 DB domain）。

### 6.3 apply-address 改造

新请求优先字段：

```json
{ "system_id": "...", "address_type": "shared|shared_system|dedicated", "agentname": "..." }
```

- `shared_system`：必须传已激活的 system_id；地址 = `agentname.{identifier}@{domain}`；不再接受自由 system_name 新建命名空间。
- `dedicated`：用 `system_id`/裸域查找 `system_domains` 的 **bare domain**；验证 active、归属、地址配额；修复当前 exact-address bug。
- `shared`：仍从 config shared_domains 选择。
- 兼容旧请求：
  - 旧 `shared_domain/system_name` 若解析到该 applicant 已激活系统则接受；
  - 否则 400，提示先完成系统激活；
  - 绝不把未知 system_name 当作新命名空间。
- `address-state` 增加 `system_id` 或 `system_name`，拼出正确完整地址；修掉 shared_system 查错对象。
- 所有 domain/address 上限用现有 product/license 配额，advanced 默认拒绝超限。

### 6.3.1 地址激活绑定接口冻结

以下既有路径**行为不变**，本次只改“申请/选项”层：

- `POST /api/v1/activate-address-code`
- agent 端 `aimail_address` bind 流程
- 地址激活码 prompt 文本与绑定参数
- 已发出的地址码仍按原方式激活

任何“先验证、个性化选项、回复激活”的改动都不得改写地址码激活接口的请求/响应或绑定语义。

### 6.4 前端顺序

`apply-address.js`：

1. 第一步 **Your Details**：name、email、Send Code、verification code。
2. 验证成功后请求 `/address-options`。
3. 第二步 **Address Type & Domain**：
   - shared domain 下拉；
   - shared system identifier 下拉（来自 owned systems，不再自由输入）；
   - dedicated domain 下拉（来自 owned systems）。
4. 选择后显示 live address suffix / 可申请状态；提交 apply-address。

未验证前不渲染/禁用 Address Type & Domain；无可用资源时显示明确空状态和“先去申请/激活系统”的入口。

---

## 7. advanced standalone 模式

### 7.1 目标

standalone 是单机 license 部署，提供类似 base 的地址申请页：

- 可添加多个 domain；
- domain 数量受 `license.max_domains`；
- 地址数量受 `license.max_addresses`；
- 不使用 advanced 多租户 shared_domains/system 体系。

### 7.2 路由

feature `standalone` 下新增独立 public router（当前是空 Router）：

- `GET /api/v1/public/standalone/address-options`：返回本机 domains、已用/剩余 address 配额。
- `POST /api/v1/standalone/domains`：添加裸域；校验 license 上限、重复、格式；写 `system_domains`（单机 bootstrap system）。
- `POST /api/v1/standalone/apply-address`：按 domain + agentname 生成地址 activation code；绑定裸域，地址数配额在激活/发码两个时点都检查。
- 复用现有 `activate-address-code` 激活路径；不引入 shared_system 概念。

### 7.3 鉴权决策

standalone 页面是 license 持有者的管理面。建议：

- **读接口**（options/domains）license 有效即可访问；
- **写接口**（add domain / apply address）要求 `system_admin` key（现有 standalone admin 认证），页面首次输入一次并保存在 sessionStorage；
- 若评审希望完全无 key，则需要新增一次性 machine-bound admin token；本方案默认采用 `system_admin` key，安全边界更清晰。

### 7.4 配额

- domain 添加：`count_active_system_domains(bootstrap_system) < license.max_domains`；
- 地址发码/激活：active agent keys + unclaimed address codes `< license.max_addresses`；
- 超限返回 403 + 明确剩余/上限，不静默失败。
- license 校验失败继续走现有 503 `license_invalid` fail-closed。

---

## 8. 共享基础设施清单

必须共享（base 唯一实现，advanced import）：

- prompt、key material、activation code format、agentname、bare-domain normalize/validate、code extraction。
- `ActivationTarget`/`ActivationCodeKind` 纯类型。
- 邮件 MIME 解析和 code 提取工具。

不共享（各版服务策略）：

- advanced `act_in_tx` 事务、renewal/rotation、shared-domain bare check、tenant ownership。
- base 单系统 DB seed。
- standalone license 配额策略。

---

## 9. 兼容（无历史迁移）

1. base：
   - 新增 `admin.domains` 纯域名数组；旧配置文件没有该字段时行为不变。
   - seed 只插入缺失 domain，不覆盖/不删除 API 创建的 domain。
2. advanced：
   - 新增 `systems.applicant_email` 只写新数据；**不做历史回填/迁移**。
   - 旧 system code 仍可 API 激活；回复激活只处理有 `applicant_email` 的 code。
   - 旧 `shared_system` 请求只有在匹配已激活 system 的 identifier 时接受；未激活的 identifier 不使用，相关 activation code 在失效期后自然回收。
   - dedicated 改为 bare-domain 查找；已有 exact-address 行仍可命中（先 exact，后 bare fallback）。
   - `address-state` 新增字段向后兼容；前端升级后传 `system_name`/`system_id`。
3. 地址激活绑定接口冻结：`POST /api/v1/activate-address-code`、`aimail_address` bind 流程、地址码 prompt 不变。
4. 所有新 API 响应只增字段，不删旧字段。
5. 不执行历史数据迁移；schema 变更只做 additive、可重复执行。

---

## 10. 实施阶段

| 阶段 | 内容 | 验证 |
|---|---|---|
| P0 | 共享工具层：prompt/key/code/agentname/domain/code-extract + 单测 | base/advanced `cargo test`，无行为变化 |
| P1 | base config domains + startup seed + 兼容测试 | config parse、seed 幂等、API 共存、apply 列表 |
| P2 | advanced `systems.applicant_email`（只写新数据）、owned resources 查询、address-options API | 单测 + 隔离/租户测试；不做历史回填 |
| P3 | advanced UI 顺序、shared_system/dedicated 选择器、apply 改造、address-state 修复 | 前端/契约测试、旧请求兼容 |
| P4 | advanced 固定 `activate@{hostname}`：Cc 自收以 activation-confirm 邮件写入 `emails`（保留至 code 失效，无过期默认 24h）、清理、Reply-To、SMTP 拦截、service 抽取、现有 auto-reply、幂等/防环、审计日志 | MIME/邮件集成、CC/引用校验、按 code 失效清理、auto-reply、权限/过期/重复测试 |
| P5 | standalone 路由/页面/domain 添加/license 配额 | `--features standalone` 构建 + 单测 + 页面 e2e |
| P6 | 全量回归、L2/L3、发布说明（无历史迁移） | L2 三宿主 + L3 4 平台；旧 API 回归 |

依赖顺序：P0 → P1/P2 → P3 → P4 → P5 → P6。P4 依赖 P2 的 systems.applicant_email；P3 依赖 P2 的 owned resources。

---

## 11. 测试清单

### base
- 多 domain config 解析/校验/去重。
- 启动 seed 幂等；API 创建的 domain 不被覆盖；删除 config 不删 DB。
- apply 页面列出 config + API domains；旧 API 继续工作。
- email_verify：name+email+code 三元匹配；name 错误即使 code 正确也拒绝。

### shared
- prompt 精确文本；code 格式；agentname 规则；bare-domain 规范化；code 提取（单码/多码/引用邮件）。

### advanced
- 系统码回复激活：正确 sender + 正确 In-Reply-To、错误 sender、过期/已 claim/未 ship、绑定的 shared/dedicated 资源、重复 message_id、现有 auto-reply 结果、不进入普通投递链、无 raw key。
- address-options/apply：name+email+code 三元匹配、验证码错误、name 不匹配、无系统、配额满、跨租户不可见。
- 地址激活冻结：`activate-address-code` 与 `aimail_address` bind 的请求/响应/行为回归不变。
- 系统码 Cc 自收：以 activation-confirm 邮件写入 `emails`、不进入普通投递链；回复缺 In-Reply-To/引用不匹配拒绝；Cc 缺失/过期回“已失效请重新申请”；伪造 From 拒绝；用 last_sent_at + 现有 expired_delivered 清理。
- 下发激活码邮件：enqueued/delivered audit 日志存在；沿用现有清除机制；日志不含 raw code/body。
- 系统激活/回复邮件不进入普通投递链；Cc 确认仅为 activation-confirm 邮件记录 + 审计日志。
- apply：shared/shared_system/dedicated 三种；dedicated 只有裸域也能申请；shared_system 必须 owned system；旧请求兼容；address-state 正确地址。
- UI：先 Details 后 Type&Domain；未验证禁用；无资源空状态。

### standalone
- `--features standalone` 构建。
- license max_domains/max_addresses 边界；添加 domain；重复 domain；申请/激活地址；超限拒绝；license 无效 503。
- 页面 domain 添加控件和配额显示。

### 回归
- L2 s1/s2/s3/s4/s5/neg；L3 4 dsh cells；旧 advanced API 客户端回归。

---

## 12. 已确认决策

1. **base config**：只填域名数组，其他参数只能通过 API 修改。
2. **advanced 激活邮箱**：固定 `activate@{smtp.hostname}`，不新增配置项。
3. **Cc 确认源**：激活码邮件 Cc 激活邮箱；该地址收到的副本以 activation-confirm 邮件写入现有 `emails` 表（不新建表、不进入普通投递链），保留至 code 失效；用户回复必须引用原始 Message-ID，才允许激活。
4. **邮箱验证**：name + email + verification code 三者匹配才通过（所有申请入口统一）。
5. **地址激活接口冻结**：`activate-address-code` / `aimail_address` bind / 地址码 prompt 行为不变。
6. **standalone 写接口**：要求 `system_admin` key；读接口 license 有效即可。
7. **旧 shared_system 标识**：只有已激活 system 的 identifier 可用；未激活的 identifier 不使用，activation code 过期后自然回收。
8. **无历史迁移**：不做数据回填/迁移，只做 additive schema 与新写入。

仍建议评审确认的实现细节：

- activation-confirm 邮件用现有 `last_sent_at` 保留至激活码失效；无过期 code 使用默认 24h；由现有 `expired_delivered` 清理。
- Cc 副本缺失/已过期：自动回复“已失效，请重新申请”，不降级为仅 code 激活。

## 13. 风险与回滚

- 回复邮件是攻击面：必须 sender 匹配 + code hash + rate-limit + 幂等 + 不泄露状态；异常只回通用失败。
- 多租户资源泄露：address-options 只按 applicant 查询，禁止复用全局 `current_system_domains` 列表。
- 无历史迁移；新增列只写新数据，旧系统无 applicant_email 时不进入 options。
- 审计日志不含 raw code/邮件正文；日志 sink 的保留期由现网 logging 配置决定。
- activation-confirm 邮件用 `last_sent_at` 标记保留；现有 expired_delivered 清理不能误删仍在有效期内的记录。
- 配置 seed 误删风险：只插入缺失，不删除/不覆盖已有 domain。
- 回滚：base config 可移除；advanced 回复功能可通过不发送 Cc/不使用回复流程关闭；旧 API 路径始终保留；standalone 路由 feature-gated，可回退构建。

---

## 14. 评审通过后的执行方式

1. 先决定 WIP 保留还是回退。
2. 按 P0–P6 实施；每个阶段单独跑 fmt/clippy/test。
3. 全部通过后再进入 L2/L3 和发版流程。
4. 本文档评审通过前，不继续写代码、不发版。

---

## 12. 实施状态（2026-11-24）

本轮执行结果（全部改动未 commit / 未 tag / 未发布）：

| 项 | 状态 | 说明 |
|---|---|---|
| P0 共享工具层 | 完成 | `aimail_base::core::api::activation`（prompt/code/agentname/bare-domain/code 提取） |
| P1 base 多 domain | 完成 | `AdminConfig.domains: Vec<String>`（纯域名数组，删除 `AdminDomainConfig` 的 id/webhook）；启动 `seed_config_domains` 幂等；README/README_zh/config.toml.example 已更新 |
| P2 advanced applicant 归属 | 完成 | `systems.applicant_email` 仅做 additive schema + 新写入；**历史回填 SQL 已删除**（无数据迁移） |
| P3 advanced 申请页 | 完成 | `apply-address.js`：先验证再出资源；shared / shared_system（owned system，不可自由输入）/ dedicated（owned system 裸域）；`address-state` 传 `system_name`/`system_id` |
| P4 回复激活 | 完成 | 固定 `activate@{smtp.hostname}`；Cc 自收以 `x-aimail-activation-confirm` 记录留在 `emails`（`Handled`，不转发/不投递）；Reply-To 已真正写进 MIME（sender + mx_deliverer）；回复校验 = 发件人 + code hash；结果用现有 auto-reply；清理沿用 `expired_delivered`（优先读 `x-aimail-activation-expires-at`） |
| P5 standalone | 完成 | 读接口 `GET /api/v1/public/standalone/address-options`；写接口 `POST /api/v1/standalone/domains`、`POST /api/v1/standalone/apply-address`（signed `system_admin`）；`/standalone` 许可证控制台页面（解锁 → 配额 → 加域名 → 发地址码）；配额边界抽成纯函数并有单测 |
| 邮箱验证三元匹配 | 完成 | base/advanced `email_verify`：`issue(email,name)` / `verify(email,name,code)`；name 精确匹配，不匹配即消耗一次尝试；`send`/`check` 与所有 apply/address-options 调用点已改；前端 `app.js`/内嵌 apply 页同步 |
| P6 回归 | 部分 | base `cargo test` 427 passed；advanced `cargo test` 154 passed；`cargo test --features standalone` 123 passed；两者 `cargo fmt` + `cargo clippy --all-targets` 干净。**未做**：L2 三宿主 smoke / L3、发版与 `pins.env` 更新 |

新增单测：`core::smtp::mime::parse_mailbox`（Reply-To 解析）、`core::storage::delivered_expired_prefers_activation_expiry_header`（Cc 记录保留到 code 失效）、`email_verify::name_mismatch_rejects_correct_code` / `resend_rebinds_name`、`activation_mail::{classify, reply_matches_confirmation}`（Cc/回复/伪造判定）、`standalone::{check_domain_admission, check_address_admission}`（license 配额边界）。

剩余（需另行确认后执行）：commit、版本号与 tag、发布、`tests/agent-env/pins.env` 更新、L2/L3 验收。
