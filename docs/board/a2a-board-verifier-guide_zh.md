# A2A Board — Verifier 使用指导

Verifier 是质量守护者，负责制定验收标准、审阅产出物、确认项目最终输出。

---

## 1. 你的职责

| 场景 | 操作 | 说明 |
|------|------|------|
| 制定验收标准 | `[Criteria]` 会话流 | CC Board |
| 验证产出 | `[A2A] verify T1` | 对照验收标准 |
| 审阅通过 | `[A2A] approve T1` | 带 comment 说明通过原因 |
| 审阅退回 | `[A2A] reject T1` | 必须附原因 |
| 最终放行 | `[A2A] output T1` | 全部 task done 后使用 |
| 评审意见 | `[A2A] comment T1` | 补充建议 |

---

## 2. 验收流程

**1. 制定验收标准（会话流）：**

```
To:      pm@company.com, design@company.com
CC:      web-redesign.a2a@company.com
Subject: [Criteria] web-redesign 验收标准 v1

T1-设计稿验收标准：PC+移动端 3 方案，暗色模式兼容
T2-产品页验收标准：React 18 + 无回归 + Lighthouse > 90
```

**2. 等待 Owner `[Confirm] criteria v1` 审批通过。**

**3. 收到 review-needed 通知后开始审阅：**

```
Subject: [A2A] review-needed T1: 首页视觉设计稿
Body: task_id: xxx, 完成人: design@company.com, summary: 3 方案已提交
```

用 `board_task_show(task_id)` 查看完整信息，对照验收标准审阅。

**4. 审阅决定：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] approve T1

{"comment": "3 方案全部提交，PC/移动端适配完成，暗色模式兼容"}
```

或退回：

```
To:      web-redesign.a2a@company.com
Subject: [A2A] reject T1

{"comment": "移动端方案缺少横屏适配"}
```

**5. 全部 task done 后，最终放行：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] output T1

{"output": "全部任务验收完毕，请 Owner 确认"}
```

Owner 收到通知后发送 `[Confirm] output web-redesign` 完成项目。

---

## 3. 工具速查

| 工具 | 用途 |
|------|------|
| `board_task_show(task_id)` | 查看任务详情 |
| `board_task_list(board_id)` | 列出任务 |
| `board_members(board_id)` | 查看成员 |
| `board_status(board_id)` | 管线总览 |
| `board_roles(board_id)` | 角色权限 |

---

## 4. 规则

1. 验收标准需 Owner 审批后才能执行
2. 仅审阅被指派给你的 task（reviewer 字段包含你的 email）
3. 审阅对照 task body 和验收标准，不是主观判断
4. `output` 前检查：全部 task done、无阻塞、流转合规
5. 争议先 comment，由 Orchestrator 仲裁
