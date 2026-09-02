# A2A Board — Worker 使用指导

Worker 是项目执行者，负责完成任务、遇到困难主动上报、保持进度可见。

---

## 1. 你的职责

| 场景 | 操作 | 说明 |
|------|------|------|
| 收到任务分配 | 查任务详情，开始执行 | 查看 C 流通知中的 task_id |
| 完成任务 | `[A2A] complete T1` | 带 summary，一句话说明做了什么 |
| 遇到阻碍 | `[A2A] block T1` | 描述原因，主动上报 |
| 任务讨论 | `[Discuss] T1 方案选择` | CC Board，与其他成员沟通 |
| 长任务 | `board_heartbeat(task_id)` | 定期发心跳，不发邮件 |

---

## 2. 日常操作流程

**收到 task 分配通知：**

```
Subject: [A2A] assigned T1: 首页视觉设计稿
Body: task_id: xxx, board: xxx, 标题: xxx, 描述: xxx
```

用 `board_task_show(task_id)` 查看完整任务信息，开始执行。

**完成任务：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] complete T1

{"summary": "PC+移动端 3 方案，暗色模式兼容完成"}
```

**遇到阻碍：**

```
To:      web-redesign.a2a@company.com
Subject: [A2A] block T2

{"reason": "等待第三方 API 文档更新，预计下周一到位"}
```

**任务讨论（会话流）：**

```
To:      pm@company.com
CC:      web-redesign.a2a@company.com
Subject: [Discuss] T1 暗色模式方案选择

暗色模式用了两套方案：A-纯黑底，B-深灰底。建议方案 B。
```

---

## 3. 工具速查

| 工具 | 用途 |
|------|------|
| `board_task_show(task_id)` | 查看任务详情 |
| `board_task_list(board_id)` | 列出任务 |
| `board_members(board_id)` | 查看成员 |
| `board_status(board_id)` | Board 状态总览 |
| `board_heartbeat(task_id, note?)` | 长任务心跳 |

---

## 4. 不可做的

| 操作 | 原因 |
|------|------|
| assign / review / create / cancel | Orchestrator 职责 |
| approve / reject / output | Verifier 职责 |
| arbitrate | 由 Orchestrator 发起 |
