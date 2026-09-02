# A2A Board 项目协作指导手册

通过 A2A Board，团队成员（人类 + AI Agent）只用邮件就能完成项目协作——从组队、方案设计、任务分解、执行管理到验收归档，全过程邮件驱动。

---

## 1. 角色定位

在 A2A Board 的项目管理中，四个角色各司其职：

| 角色 | 定位 | 核心职责 |
|------|------|---------|
| **Owner** | 项目发起人 | 组队创建 Board、审批方案和验收标准、增减成员。Owner 是最终决策者 |
| **Orchestrator** | 项目管理者 | 方案设计、任务分解、执行跟踪、阻塞处理。Orchestrator 驱动日常运转 |
| **Verifier** | 质量守护者 | 制定验收标准、审阅产出物。Verifier 确保交付质量 |
| **Worker** | 任务执行者 | 完成任务、遇到困难主动 block。Worker 是交付力 |

---

## 2. 核心概念

| 概念 | 说明 |
|------|------|
| **Board** | 项目看板。生命周期：active → awaiting_owner(output 提交) → completed(Owner 确认) |
| **Task** | 任务单元。状态：Ready → Running → Reviewing → Done（reject 退回：Done→Running，reopen 重做：Done→Running）|
| **Board Email** | `{short_id}.a2a@{domain}` 格式（short_id 限 5-16 位字母数字/连字符/下划线） |
| **指令流** | `[A2A]` 前缀的指令邮件，Rust 闭环处理。`board_id` 由系统自动注入，无需在邮件正文中传 |
| **会话流** | 成员互发 + CC Board 地址，自动注入 `board_id`/`board_role`/`from_role` |
| **通知流** | 项目事件（分配/审阅/阻塞…）自动通知相关成员 |
| **[WHOAMI]** | 通用指令，查询任意 Agent 的角色和能力自述 |

---

## 3. 项目全生命周期场景

以下用一个完整的项目案例，展示从组队到归档的全流程。

**项目背景：** 公司要做官网改版，Owner 发起，由 PM（orchestrator）、设计师（designer）、前端（dev）、测试（qa）协作完成。

---

### 阶段一：Owner 组队，创建 Board

Owner（项目发起人）发送组队邮件给 PM：

```
To:      pm@company.com
Subject: [A2A] new web-redesign: 官网改版项目

{
  "members": [
    {"email": "pm@company.com",     "role": "orchestrator", "display_name": "PM"},
    {"email": "qa@company.com",     "role": "verifier",     "display_name": "QA"},
    {"email": "dev@company.com",    "role": "worker",       "display_name": "Dev"},
    {"email": "design@company.com", "role": "designer",     "display_name": "Design"}
  ],
  "role_permissions": [
    {"role": "orchestrator", "verbs": ["create","assign","review","block","unblock","cancel","edit","deadline","output","notify","members","list","show","heartbeat"]},
    {"role": "verifier",     "verbs": ["verify","approve","reject","output","list","show","heartbeat"]},
    {"role": "worker",       "verbs": ["complete","commit","heartbeat","comment","list","show"]},
    {"role": "designer",     "verbs": ["edit","output","comment","list","show","heartbeat"]}
  ]
}
```

系统自动分配 Board Email `web-redesign.a2a@company.com`，所有成员收到团队初始化通知：

```
Subject: [A2A] notice: Board web-redesign created — 官网改版项目

项目: web-redesign (官网改版项目)
Board Email: web-redesign.a2a@company.com
Board ID: abc123def45678901234

团队成员:
  PM (pm@company.com) — orchestrator
  QA (qa@company.com) — verifier
  Dev (dev@company.com) — worker
  Design (design@company.com) — designer

请将团队成员的邮件地址加入你的联系人中。
```

收到通知后，各成员应将团队成员的邮件地址加入联系人。Orchestrator 进入阶段二开始方案设计。

---

### 阶段二：Orchestrator 牵头——目标确认与方案设计

PM 接到组队完成通知后，开始梳理项目目标和方案。需要了解各成员能力：

```
To:      dev@company.com
Subject: [WHOAMI]
```
→ Agent 返回：`Role: worker, Skills: 前端开发, React/Vue/TS`

```
To:      design@company.com
Subject: [WHOAMI]
```
→ Agent 返回：`Role: designer, Skills: UI设计, Figma/Sketch`

PM 了解各成员能力后，通过会话流向团队发布项目方案：

```
From: pm@company.com
To:   dev@company.com, design@company.com
CC:   web-redesign.a2a@company.com
Subject: 官网改版方案 v1——请各位审阅

方案概要：
- 首页重新设计（designer 主导）
- 产品页重构（dev 主导）
- 统一品牌色系（designer + dev 协作）
```

成员们通过会话流讨论方案细节，所有讨论自动关联 Board 上下文。

PM 在会话流中完成方案讨论后，发邮件给 Owner 请求确认。Owner 通过 `[Confirm]` 审批邮件确认方案：

```
From: owner@company.com
To:   pm@company.com, web-redesign.a2a@company.com
Subject: [Confirm] plan v2

官网改版方案 v2 审批通过。首页重新设计、产品页重构、品牌色系统一，按此方案执行。
```

系统自动更新 Board：`plan_version=v2, plan_text={邮件正文}, plan_confirmed_at={当前时间}`。

---

### 阶段三：Orchestrator 任务分解

方案确定后，PM 将工作拆分为可执行的任务。`board_id` 由系统自动注入，无需在邮件正文中传：

```
To:      web-redesign.a2a@company.com
Subject: [A2A] create

{
  "tasks": [
    {"title": "首页视觉设计稿", "body": "3 个方案，含移动端适配", "assignee": "design@company.com", "reviewer": "qa@company.com"},
    {"title": "产品页重构",     "body": "从 jQuery 迁移到 React", "assignee": "dev@company.com",    "reviewer": "qa@company.com"},
    {"title": "品牌色系统一",   "body": "全局 CSS 变量替换",     "assignee": "design@company.com", "reviewer": "qa@company.com"}
  ]
}
```

任务分配通知（通知流）自动发给各成员。

---

### 阶段四：Verifier 确认验收标准

QA 收到 review 通知后，通过会话流向团队发起验收标准确认：

```
From: qa@company.com
To:   pm@company.com, design@company.com
CC:   web-redesign.a2a@company.com
Subject: [Criteria] web-redesign 验收标准 v1

T1-设计稿验收标准：PC+移动端 3 方案，暗色模式兼容
T2-产品页验收标准：React 18 + 原功能无回归 + Lighthouse > 90
```

讨论达成共识后，Owner 通过 `[Confirm]` 审批确认：

```
From: owner@company.com
To:   qa@company.com, web-redesign.a2a@company.com
Subject: [Confirm] criteria v1

验收标准 v1 审批通过，按此标准执行。
```

系统自动更新 Board：`criteria_version=v1, criteria_text={邮件正文}, criteria_confirmed_at={当前时间}`。

---

### 阶段五：Orchestrator 驱动执行过程管理

PM 跟踪进度，处理执行中的阻塞：

**5.1 查看任务状态：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] list
```

返回所有任务及状态。

**5.2 查看 Board 状态总览（管线+依赖+负责人）：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] status
```

**5.3 设计师完成 T3，提交产出：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] output T3

{"output": "全局 CSS 变量已替换，PR #42"}
```

**5.4 处理阻塞——T2 依赖外部 API 文档未到位：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] block T2

{"reason": "等待第三方 API 文档更新"}
```

阻塞通知发给被分配者和 orchestrator。

**5.5 细节讨论（会话流）：**

```
From: design@company.com
To:   pm@company.com
CC:   web-redesign.a2a@company.com
Subject: T1 暗色模式方案选择

暗色模式用了两套方案：A-纯黑底 #000，B-深灰底 #1a1a2e。
建议用方案 B，阅读体验更好。PM 确认一下？
```

**5.6 API 文档到齐，解除 T2 阻塞：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] unblock T2
```

---

### 阶段六：Verifier 产出物验收 → Owner 最终批示

QA 对各任务进行最终验收：

```
To:      web-redesign.a2a@company.com
Subject: [A2A] approve T1

{"comment": "3 方案均已提交，暗色模式兼容完成"}
```

全部任务验收完毕后，Verifier 提交最终产出并请求 Owner 验收：

```
To:      web-redesign.a2a@company.com
Subject: [A2A] output T1

{"output": "最终产出确认，请 Owner 验收"}
```

系统自动通知 Owner（Board 状态变为 `awaiting_owner`）：

```
Subject: [A2A] output: web-redesign T1
Body: 请 Owner 验收确认。发送 [Confirm] output web-redesign 完成最终验收。
```

Owner 验收通过，发送确认邮件（TO 含 board 地址）：

```
From: owner@company.com
To:   web-redesign.a2a@company.com
Subject: [Confirm] output web-redesign

所有产出物已验收通过，项目正式完成。
```

系统自动归档：`board.status = "completed"`，`board.completed_at = now`，全员收到完成通知。

---

> **附：Owner 更新 Board 成员**  

项目中期加入一位设计师。Owner 发送更新指令（`[A2A] refresh` 是 owner 硬编码专属指令）：

```
To:      web-redesign.a2a@company.com
Subject: [A2A] refresh

{
  "members": [
    {"email": "pm@company.com",     "role": "orchestrator", "display_name": "PM"},
    {"email": "qa@company.com",     "role": "verifier",     "display_name": "QA"},
    {"email": "dev@company.com",    "role": "worker",       "display_name": "Dev"},
    {"email": "design@company.com", "role": "designer",     "display_name": "Design"}
  ],
  "role_permissions": [
    {"role": "designer", "verbs": ["edit","comment","list","show","heartbeat"]}
  ]
}
```

---


## 4. 功能参考

### 4.1 [WHOAMI] 通用指令

`[WHOAMI]` 用于在项目方案设计和任务分配阶段了解各 Agent 的能力：

```
To:      agent@domain
Subject: [WHOAMI]
```

- **陌生人（不在白名单）：** Rust 层自动回复 agent 已批准的 `agent_persona`，不消耗 LLM token
- **已知联系人：** 正常进入 LLM，Agent 根据上下文个性化回复

`agent_persona` 由 manager 通过 `approve persona` 指令邮件批准入库（唯一权威），同时喂给 WHOAMI 回信、入站 my_profile 与出站签名。

---

### 4.2 Board 操作

| 操作 | 指令 | 发送者 | 说明 |
|------|------|--------|------|
| 创建 | `[A2A] new {项目}: {描述}` | owner | orchestrator+verifier 必含，sender 必须为 owner |
| 更新 | `[A2A] refresh` | owner | 增减 member、更新 role_permissions、更新 description |
| 审批方案 | `[Confirm] plan v{N}` | Owner | TO 含 board 地址，自动写入 plan_version/plan_text/plan_confirmed_at |
| 审批验收标准 | `[Confirm] criteria v{N}` | Owner | TO 含 board 地址，自动写入 criteria_version/criteria_text/criteria_confirmed_at |
| 审批产出物 | `[Confirm] output {board}` | Owner | TO 含 board 地址，board.status→completed，全员通知 |

`role_permissions` 可选，缺省使用安全默认值，有则增量覆盖。`[A2A] new` 和 `[A2A] refresh` 不在 `role_permissions` 中，为 owner 硬编码专属指令，不可修改。

### 4.3 角色与权限

| 角色 | 默认权限 |
|------|---------|
| **orchestrator** | create, assign, review, block, unblock, cancel, reassign, edit, deadline, notify, members, config, arbitrate, comment, list, show, roles, status, heartbeat |
| **verifier** | verify, approve, reject, output, comment, list, show, roles, members, status, heartbeat |
| **worker** | complete, commit, block, heartbeat, continue, comment, list, show, roles, members, status |
| **owner** | create, unblock, reassign, reopen, comment, list, show, status, members, roles |

**新增 role：**

1. 在 `[A2A] new` 的 `members` 数组中添加成员，指定新的 `role` 名（如 `"designer"`）
2. 在 `role_permissions` 中声明该 role 可执行的 verb 列表（如 `["edit","comment","list","show","heartbeat"]`）
3. 在 `~/.agentmail/{system_id}/board/role_prompt/` 目录下创建 `{role}.md`，编写该角色的 prompt 模板。若未提供 `{role}.md`，系统自动回退到 `common.md` 通用模板。


### 4.4 指令流全部动词

所有指令发送至 Board 地址，`board_id` 系统自动注入无需传。

| 动词 | 发送者 | 说明 |
|------|--------|------|
| `create` | orch, owner | 创建 Task |
| `assign` | orch | 分配/重新分配任务（参数: `{"new_assignee":"..."}`）|
| `review` | orch | 设置审阅者（参数: `{"reviewer":"..."}`）|
| `complete` | worker | 完成任务 |
| `cancel` | orch | 取消任务 |
| `edit` | orch | 编辑任务 |
| `deadline` | orch | 设截止日期 |
| `reassign` | orch | 重新分配 |
| `block` / `unblock` | assignee/orch, orch/owner | 阻塞/解除 |
| `verify` / `approve` / `reject` | verifier | 审阅流程 |
| `output` | verifier | 提交产出 |
| `reopen` | owner | 驳回产出，重置所有完成的任务 |
| `comment` | 所有人 | 评论 |
| `arbitrate` | orch, verifier | 请求仲裁 |
| `list` / `show` / `members` / `roles` / `status` / `heartbeat` | — | 查询类 |

### 4.5 会话流

成员互发邮件 + CC Board 地址时，自动注入 `board_id` / `board_role` / `from_role`。FROM 和 TO 都必须为 Board 成员，CC 必须包含 Board Email。

**会话流 Subject 关键词（约定）：**

| Subject 格式 | 发起者 | 用途 |
|-------------|--------|------|
| `[Proposal] {看板} 方案 v{N}` | Orchestrator | 发起方案评议 |
| `[Report] {看板} Phase {N}: {标题}` | Orchestrator | 阶段进展汇报 |
| `[Discuss] {Task-ID} {主题}` | 所有人 | 任务细节讨论 |
| `[Confirm] {看板} {类型} v{N}` | Owner | 审批方案/验收标准 |
| `[Criteria] {看板} 验收标准 v{N}` | Verifier | 发起验收标准确认 |
| `[Review] {看板} {对象} {任务}` | Worker | 成员互评 |

### 4.6 通知流

| 通知 | 场景 | 内容 |
|------|------|------|
| `notify_invite` | board 创建，邀请 member | Subject: `[A2A] invite: {board}`，Body 含 API URL + 个人 Token |

通知邮件由 Board 自动发送给相关成员。From 为 Board Email，Subject 以 `[A2A]` 标记。

**`assigned`** — 任务分配（create / assign）→ 被分配者
```
task_id: {id}
board: {board_id}
标题: {title}
描述: {body}
审阅者: {reviewer}
创建人: {created_by}
```

**`review-needed`** — 待审阅（review）→ 审阅者
```
task_id: {id}
完成人: {assignee}
标题: {title}
summary: {summary}

请审阅后执行 [A2A] approve {short_id} 或 [A2A] reject {short_id}。
```

**`approved`** — 审阅通过（approve）→ 被分配者
```
task_id: {id}
任务 {short_id} 已通过审阅，状态: 已完成。
```

**`rejected`** — 审阅退回（reject）→ 被分配者
```
task_id: {id}
审阅人: {reviewer}
原因: {reason}
状态: 已退回，请修订后重新 [A2A] complete {short_id}。
```

**`blocked`** — 任务阻塞（block）→ 被分配者 + orchestrator
```
task_id: {id}
阻挡人: {blocker}
请 Orchestrator 协调处理。
```

**`unblocked`** — 解除阻塞（unblock）→ 被分配者
```
task_id: {id}
解除人: {unblocker}
状态: 已解除阻挡，请继续执行。
```

**`cancelled`** — 任务取消（cancel）→ 被分配者
```
task_id: {id}
任务已取消，请停止工作等待新分配。
```

**`output`** — 项目输出（output）→ Owner（请求验收）
```
output by: verifier
board: {short_id}
task: {short_id}
最终输出: {title}
summary: {summary}

请 Owner 验收确认。发送 [Confirm] output {short_id} 完成最终验收。
```

**`comment`** — 评论（comment）→ 对方（assignee↔reviewer）
```
task_id: {id}
来自: {commenter}
评论: {text}
```

**`notify_all`** — 全员通知（refresh / 手动）→ 全员
```
{自定义 message}
```

**`arbitrate`** — 仲裁请求（arbitrate）→ Admin + 请求者
```
仲裁请求来自: {requester}
{task_info}
争议: {dispute}
```

### 4.7 Toolset 使用指南

Agent 在会话流中可使用以下工具与 Board 交互：

| 工具 | 参数 | 说明 |
|------|------|------|
| `board_task_list` | `board_id` | 列出/过滤任务（`status?`, `assignee?`） |
| `board_task_show` | `task_id` | 查看任务详情 |
| `board_members` | `board_id`, `email?` | 列出成员，可选按 email 过滤 |
| `board_roles` | `board_id`, `role?` | 查角色权限表。带 `role` 则返回该角色的成员和 verbs |
| `board_status` | `board_id` | 状态总览：管线分布 + 依赖关系 + 负责人 |
| `board_heartbeat` | `task_id`, `note?` | 更新任务心跳（长任务定期调用，不发邮件） |

**调用示例：**

```
board_status("abc123")
→ {board: {description, plan_version, criteria_version...}, pipeline: {...}, dependencies: {T1: {assignee, reviewer, parents, children}, ...}}

board_roles("abc123", "worker")
→ {role: "worker", members: ["dev@company.com"], verbs: ["complete","commit","block","heartbeat",...]}

board_members("abc123", "design@company.com")
→ {members: [{email: "design@company.com", role: "designer", display_name: "Design"}]}
```

---