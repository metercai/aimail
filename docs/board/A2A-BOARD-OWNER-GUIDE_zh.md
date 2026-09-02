# A2A Board — Owner 指导手册

Owner 是 A2A Board 项目的发起人和最终决策者，通常是人类（不用 prompt 文件）。本手册指导 Owner 通过邮件完成项目管理。

---

## 1. 你的职责

| 阶段 | 指令 | 说明 |
|------|------|------|
| 组队创建 Board | `[A2A] new {项目}: {描述}` | 发给 Orchestrator，声明成员和权限 |
| 审批方案 | `[Confirm] plan v{N}` | TO 含 board 地址，方案通过后自动生效 |
| 审批验收标准 | `[Confirm] criteria v{N}` | TO 含 board 地址，验收标准通过后生效 |
| 审批最终产出 | `[Confirm] output {board}` | TO board 地址，项目归档完成 |
| 增减成员 | `[A2A] refresh` | TO board 地址，更新成员和权限 |

---

## 2. 组队创建 Board

发送邮件给 Orchestrator：

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
    {"role": "orchestrator", "verbs": ["create","assign","review","block","unblock","cancel","edit","deadline","notify","members","list","show","heartbeat"]},
    {"role": "verifier",     "verbs": ["verify","approve","reject","output","comment","list","show","heartbeat"]},
    {"role": "worker",       "verbs": ["complete","commit","heartbeat","comment","list","show"]},
    {"role": "designer",     "verbs": ["edit","output","comment","list","show","heartbeat"]}
  ]
}
```

必含：members 中必须有 orchestrator 和 verifier，sender 必须是 owner。
`role_permissions` 可选，缺省使用安全默认值。

系统自动创建 Board Email，全员收到初始化通知（含项目信息、成员列表）。

---

## 3. 审批方案

Orchestrator 通过会话流发 [Proposal] 讨论方案后，请求你确认。你回复确认邮件：

```
From: owner@company.com
To:   pm@company.com, web-redesign.a2a@company.com
Subject: [Confirm] plan v2

官网改版方案 v2 审批通过。按此执行。
```

系统自动写入 `plan_version=v2, plan_text={邮件正文}, plan_confirmed_at=now`。

---

## 4. 审批验收标准

Verifier 通过会话流发 [Criteria] 后，你确认验收标准：

```
From: owner@company.com
To:   qa@company.com, web-redesign.a2a@company.com
Subject: [Confirm] criteria v1

验收标准 v1 审批通过。
```

系统自动写入 `criteria_version=v1, criteria_text={邮件正文}, criteria_confirmed_at=now`。

---

## 5. 审批最终产出

Verifier 通过 [A2A] output 提交最终产出后，Board 状态变为 awaiting_owner。你收到通知后确认：

```
From: owner@company.com
To:   web-redesign.a2a@company.com
Subject: [Confirm] output web-redesign

所有产出物已验收通过，项目正式完成。
```

系统自动归档：`board.status=completed, board.completed_at=now`，全员收到完成通知。

---

## 6. 增减成员

项目进行中需要调整团队，发送 [A2A] refresh：

```
From: owner@company.com
To:   web-redesign.a2a@company.com
Subject: [A2A] refresh

{
  "members": [
    {"email": "pm@company.com",     "role": "orchestrator", "display_name": "PM"},
    {"email": "qa@company.com",     "role": "verifier",     "display_name": "QA"},
    {"email": "dev@company.com",    "role": "worker",       "display_name": "Dev"},
    {"email": "design@company.com", "role": "designer",     "display_name": "Design"},
    {"email": "design2@company.com","role": "designer",     "display_name": "Design2"}
  ]
}
```

members 为全量替换（不在列表中的成员保留在 Board 中），role_permissions 可选增量覆盖。

---

## 7. 收到的通知

| 通知 | 含义 | 你需要做什么 |
|------|------|------------|
| `[A2A] notice: Board XXX created` | 组队完成 | 不需要操作，等待 Orchestrator 推进 |
| `[A2A] output: XXX XXX` | Verifier 提交最终产出 | 验收确认 → 发送 [Confirm] output |
| `[A2A] notice: XXX XXX` | 全员通知 | 阅读后知悉或回复 |
