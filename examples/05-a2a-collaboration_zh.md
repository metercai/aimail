# 流程协同 — AgentMail A2A Board 实现方案

## 场景
设计师 Agent、前端 Agent、PM 通过 A2A Board 协作，邮件指令驱动任务流转。

## 基础设施

| 能力 | A2A Board 支持 |
|------|:--:|
| 看板创建 | ✅ `[A2A] new {project}` |
| 任务分配 | ✅ `[A2A] create / assign / review` |
| 会话讨论 | ✅ 会话流，CC Board 注入角色上下文 |
| 阻塞处理 | ✅ `[A2A] block / unblock` |
| 自动化通知 | ✅ 通知流 10 种事件 |
| 审阅验收 | ✅ `[A2A] approve / reject / output` |

## 流程

```
1. Owner: [A2A] new web-redesign → 组队
2. PM: [Proposal] 方案发布 → 会话流讨论
3. Owner: [Confirm] plan v1 → 方案通过
4. PM: [A2A] create → 任务分解（T1/T2/T3）
5. Designer: [A2A] complete T1 → Worker: [A2A] complete T2
6. QA: [A2A] approve → 验收
7. Verifier: [A2A] output → Owner: [Confirm] output
```

## 角色 Prompt

参见 `board/role_prompt_zh/`:
- `orchestrator.md` — PM 执行规则
- `worker.md` — 设计师/前端执行规则
- `verifier.md` — QA 审阅规则

完整指南: `board/A2A-BOARD-GUIDE_zh.md`
