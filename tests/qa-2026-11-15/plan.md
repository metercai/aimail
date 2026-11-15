# 交接件｜激活码格式 / 邮件正文 / 申请页 统一（v1）

出具：Verifier　2026-11-15　状态：待开发评审
对象：aimail（base）· aimail-gateway · aimail-advanced
基线：aimail 35260ea · gateway b4d2955 · advanced 1f48d62

## 0 目标
1 advanced 码前缀统一为 scope 语义（sys- / addr-），不再由标识符截断派生
2 base 码体格式与 advanced 对齐（6×4 hex）
3 base 三封下发邮件与 advanced 保持一致（英文、无 CJK、含粘贴用 prompt）
4 base /apply 网页英文化，视觉与 advanced HTML 页一致

## 1 决策（已由 owner 确认）
- 前缀仅用于区分类别（系统码 / 地址码），可作为 panel 分组筛选键
- 不做迁移、不做兼容；旧码一律作废
- **码有效期保留**（与 advanced 一致）；**产品有效期与续期 base 不引入**
  （两个"有效期"必须分开：前者是码本身，后者是激活后的产品）

## 2 A｜advanced 前缀统一

```
现状  product_code_prefix(id) = id 首段.take(8) + "-"（activation.rs:43-47）
      地址码喂 system_id(shared_addr_*) → shared_a-；系统码喂 product_id → p-defaul- / shared-d-
问题  ① 前缀随标识符变化，不同产品/不同共享域撞前缀（shared_addr_a 与 _b 都成 shared_a）
      ② panel 无法用前缀分组
改法  新增 scope_prefix(kind: CodeScope) -> &'static str { System => "sys-", Address => "addr-" }
调用点（全部替换）
      open_apply.rs:233        地址码 → addr-
      activation.rs:467        地址码 → addr-
      activation.rs:259/1709   系统码 → sys-
      address.rs:596（test seed）→ addr-
      码体不变：generate_activation_code 仍 6×4 hex
连带  · DB code_prefix 字段（activation.rs:289/496/611）改存 sys-/addr-（仅展示，无迁移价值）
      · tool-spec.ts:347/363 的 "shared_a-…" 改为不依赖前缀的描述
      · dsh 文档中的示例码同步
兼容  不做兼容：旧码作废（见 §7）
```

## 3 B｜base 码体格式统一

```
现状  random_code(prefix) = prefix + uuid 去横线后 16 hex（core/api/activation.rs:632-634）→ sys-55c79006d5b44e34
      地址码为 inline 6×4（activation.rs:41-48）→ addr-d41f-e54e-2042-de30-72c9-5409
改法  random_code 改为 6 组 × 4 hex（与 advanced generate_activation_code 同构）
      addr- 生成改为调用 random_code("addr")，删除 inline 重复实现
结果  统一形态：^(sys|addr)-([0-9a-f]{4}-){5}[0-9a-f]{4}$（33 字符）
注意  码有效期逻辑不动（is_expired / expires_at 保留）
```

## 4 C｜base 语义澄清

```
保留  码有效期：与 advanced 一致（系统码 7 天 / 地址码 24h 申请窗），代码与文案都要保留
不引入 产品/地址有效期、续期（无 renew 接口，文案不出现 "renew" / "valid for the period"）
manager 缺省 = 收件人（申请人）地址
      现状 base 在 manager_address 为空时硬报错（activation.rs:275-277）→ 删除该错误，
      改为与 advanced email_notify.rs:125-128 相同的回落：stored_manager.or(applicant)
      下发流程仍把收件人地址预存为 manager（供 whoami/授权使用）
```

## 5 D｜base 三封邮件（对齐 advanced，全英文，无 CJK）

### D1 系统激活码
```
Subject: Your AIMail system activation code

Hello {name},

An AIMail system is ready for your agent:

  system: {domain}

Please activate it within 7 days.

Copy the message below into your agent's conversation and let the agent activate the system:

Activate my AIMail system {domain} using the activation code {code}. AIMail gateway is at {gateway_url}. Follow the step-by-step instructions at {docs_url}

This code is sent only once - please keep this email.
Please do not reply to this email - it is sent automatically.

Best regards,
AIMail Team
```

### D2 地址激活码
```
Subject: AIMail address activation code for {address}

Hello {name},

An AIMail mailbox is ready for your agent:

  address: {address}

Please activate it within 24 hours.

Copy the message below into your agent's conversation and let the agent bind the address:

Activate my AIMail mailbox {address} using the activation code {code}. My safety officer (manager) is {manager}. AIMail gateway is at {gateway_url}. Follow the step-by-step instructions at {docs_url}

This code is sent only once - please keep this email.
Please do not reply to this email - it is sent automatically.

Best regards,
AIMail Team
```
（manager 缺省为收件人地址；base 无产品有效期/续期段落）

### D3 邮箱验证码（对齐 advanced 主题与结构）
```
Subject: Your AIMail verification code

Hello,

Your AIMail verification code is:

  {code}

It expires in 10 minutes. If you did not request this mail, please ignore it.

Best regards,
AIMail Team
```

## 6 E｜base /apply 页面英文化

```
目标  全英文、0 中文（含 placeholder / 按钮 / 提示 / 错误文案 / JS 常量）
视觉  采用 advanced HTML 设计语言（licenses_html.rs:20-34）
      bg #0f1117 · card #1a1d27 · border #2d3143 · 主色 #5b6af0（hover #4a58d4）
      文字 #e4e6eb · label #8b8fa3 · 卡片圆角 12px · 输入/按钮 8px · 卡片 max-width 440px
      字体 -apple-system,BlinkMacSystemFont,sans-serif · h1 20px · label 13px
标题  Claim Activation Code - AIMail（沿用 "<页名> - AIMail" 命名，lang="en"）
结构  保持现有三步：1 Email verification · 2 Claim system code · 3 Claim address code
      每步用 label + input + 主按钮，验证码步骤保留"查看模拟收件箱"入口（英文）
不改  API 路径与表单字段名（避免影响脚本与文档）
```

## 7 F｜清表与夹具

```
清表  上线前 DELETE FROM activation_codes（三仓均不做数据迁移）
      理由：不做兼容，旧码作废；显式清表避免"看起来仍可用"
夹具  tests/dsh-acceptance/harness/mailpit_code.py 正则恢复为 (sys|addr)-
      （此前因 shared_a- 才改为通用匹配）⇒ F7 关闭
```

## 8 验收断言

```
V1 码格式      ^(sys|addr)-([0-9a-f]{4}-){5}[0-9a-f]{4}$（base 与 advanced 各 1 单测）
V2 前缀        advanced：共享域地址码 addr-、共享域系统码 sys-、独享域系统码 sys-
               断言不含 shared_a- / p-defaul- 等派生前缀
V3 邮件快照    base 三封 ×（有/无 name）= 6 例；断言：
               无 CJK · 无 "dsh" · 无 "renew" · 无 "valid for the period"
               含 "within 7 days"（系统）/ "within 24 hours"（地址）
               地址邮件含 "My safety officer (manager) is <收件人>"
V4 manager     未配置 manager_address 时地址码可正常签发，邮件 manager = 收件人
V5 页面        GET /apply 200 · lang="en" · 0 CJK · 含 #0f1117/#5b6af0
V6 旧码        清表后旧码激活返回 400/404
V7 端到端      run-base-loop、run-advanced-b2 通过（F7 关闭）
```

## 9 执行顺序（每项独立 commit，可单独回滚）

```
1 advanced 前缀统一 + tool-spec/文档
2 base 码体格式统一（6×4）
3 base manager 缺省（删硬错误）
4 base 三封邮件重写
5 base /apply 页英文化
6 清表 + 夹具正则回退 + 端到端复跑
```

## 10 证据与复现

```
前缀派生链   advanced activation.rs:43-47 → open_apply.rs:233 / activation.rs:467,259,1709
base 现状    gateway activation.rs:632-634（sys 16hex）· :41-48（addr 6×4）· :275-277（manager 硬错）
邮件实测     base 实发两封（Mailpit）：sys-55c79006d5b44e34 / addr-d41f-e54e-2042-de30-72c9-5409
             advanced 实发一封：shared_a-9162-9909-230f-4fb9-434c-a4be（含 manager=申请人）
页面现状     base /apply 为中文、浅色（activation.rs:866-884）
             advanced 无 HTML 申请页，仅 JSON API + licenses_html.rs（深色设计语言）
```
