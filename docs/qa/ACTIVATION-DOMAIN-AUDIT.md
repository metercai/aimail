# 地址激活码 / 域名来源审计（base ↔ advanced）

日期：2026-11-23  
范围：`aimail-gateway` base、`aimail-advanced`

---

## 1. base：地址码申请可选域从哪来

结论：**不是 config 文件里的域名列表，而是数据库 `system_domains` 表中的 active 裸域记录。**

链路：

1. `/apply` 页面加载时请求：
   - `GET /api/v1/public/system/domains`
2. 该接口调用 `current_system_domains()`：
   - `SELECT DISTINCT system_id, domain_addr FROM system_domains`
   - 条件：`domain_addr NOT LIKE '%@%'`、`domain_addr != 'admin.relay'`、`is_active = 1`
3. 页面把返回的 `domain` 填进下拉框；提交时把用户选的 `domain` 放到 `POST /api/v1/public/apply-address`。
4. `apply_address_public()` 再查一次 `current_system_domains()`，确认该 domain 仍存在，并取出 `system_id`；不存在则 `400 domain is not offered by this gateway`。

域名的写入方式：

- 管理员/系统级 key 调：
  - `POST /api/v1/admin/systems/{sid}/domains`
  - body：`{ id, domain, webhook_url?, webhook_secret?, manager_address?, agent_signature?, agent_persona? }`
  - `domain` 必须是**裸域**（handler 明确拒绝含 `@` 的值；地址注册走 `/addresses`）。
- 写入后就是 `system_domains` 一行；`is_active=1` 才会出现在 public 列表里。

所以 base 的 domain 是**运营方预先写入 DB 的**，不是 base config 的静态字段，也不是用户申请时创建。

---

## 2. advanced：三种地址类型分别怎么来

### 2.1 shared（共享域地址）

- 域名来自 **advanced config**：`[admin].shared_domains[]`，每项含 `name/domain/validity_days/code_validity_mins/limits`。
- 页面通过 `GET /api/v1/public/shared-domains` 读取配置；用户只选，不输入域名。
- 地址：`agentname@shared_domain`；system id：`shared_addr_{name}`。
- 域本身由运营方在 config 里配置，用户不需要也不应该临时输入。

### 2.2 shared_system（带标识的共享域地址）

- 域名仍来自 `shared_domains` config。
- `system_name`（标识名）是**申请页上的自由文本输入**，提交到 `POST /api/v1/apply-address` 的 `system_name`。
- 当前只做格式校验（1-63 chars、字母数字开头、只含 `[a-z0-9-]`），**没有**：
  - 查询该标识是否已被预注册；
  - 检查 `system_quotas.system_name` / 已有 system；
  - 做唯一性/归属校验。
- 地址：`agentname.system_name@shared_domain`。
- 因此当前“标识名”不是预申请资源，只是地址 local part 的一段命名空间；不同申请人可以使用相同 `system_name`（只要完整地址不同）。

### 2.3 dedicated（独享域地址）

- 页面通过 `GET /api/v1/public/address-domains` 取可用独享域：
  - `current_system_domains()` 的 active 裸域；
  - 排除 `admin.shared_domains` 中已配置的共享域。
- 这些裸域应由管理员提前通过 `POST /api/v1/admin/systems/{sid}/domains` 注册。
- 注意：`current_system_domains()` 当前返回**所有 system 的 active 裸域**，没有按租户/当前 system 过滤；在 advanced 多租户下，`/public/address-domains` 会把其他 system 的域名也暴露给申请人。修 dedicated 时必须同时加 system/tenant 作用域。
- **但当前 apply handler 有一个不一致**：
  - 它用 `lookup_domain_addr(&address)` 查的是 **完整地址** `agentname@domain`；
  - 不是裸域 `domain`；
  - 因此如果只预注册了裸域（页面会列出它），申请仍会报 `domain is not registered on this gateway`；
  - 必须额外预注册 `agentname@domain` 这一行才会通过（e2e fixture 正是同时插入了裸域和 `dave@dedicated...` 两行，掩盖了这个前提）。
- 建议修复：dedicated 分支改为 `lookup_domain_addr(&domain)`（或 `resolve_domain_from_address` 的 domain fallback），验证 `is_active` 后取 `system_id`；这样“提前申请独享域”与“申请具体地址”才是两个独立步骤。

### 2.4 另一个 shared_system 状态检查缺口

`POST /api/v1/public/address-state` 只接收 `{shared_domain, agentname}`，不接收 `system_name`，所以：

- 对 `shared` 类型：检查 `agentname@domain`，正确；
- 对 `shared_system` 类型：页面仍拿 `agentname@domain` 的占用状态，而实际要申请的是 `agentname.system_name@domain`，**状态检查对象错误**；
- 前端 `probeState()` 也没有传 `system_name`。

建议：`AddressStateRequest` 增加可选 `system_name`，按 `shared_system` 拼完整地址；前端在 `shared_system` 下把标识名一起传。

---

## 3. 当前结论

| 类型 | 域名来源 | 标识/独享域是否预申请 |
|---|---|---|
| base 地址码 | DB `system_domains` active 裸域，管理员 API 写入 | 域名预注册；地址码申请时选域 |
| advanced shared | config `admin.shared_domains` | 域名由运营方配置 |
| advanced shared_system | config `admin.shared_domains` + 用户自由输入 `system_name` | **标识名未预申请/未校验归属** |
| advanced dedicated | DB `system_domains` active 裸域（页面列表） | 域名应预注册；**当前 handler 却要求 exact address 行，存在 bug** |

---

## 4. 建议的最小修复顺序

1. dedicated：`lookup_domain_addr(&domain)`，补一个“只有裸域、没有 exact address”也能申请的测试。
2. shared_system：明确产品语义：
   - 若标识名只是地址命名空间：补 `address-state` 的 `system_name` 参数与前端传递，文档写明不预留；
   - 若标识名必须预申请：需要增加 system identifier 注册/查询/唯一性校验，不能只靠自由文本。
3. 把上述域名解析/合法性规则抽到 base 的纯工具函数，advanced 只保留事务与配置策略，避免再次出现 exact-address / bare-domain 这类漂移。
