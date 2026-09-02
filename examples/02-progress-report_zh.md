# 进度报告 — AgentMail 实现方案

## 场景
Agent 定期汇总项目进度、生成结构化报告，按角色定制内容发送。

## 基础设施

| 能力 | AgentMail 支持 |
|------|:--:|
| 定时触发 | ✅ Hermes cron job |
| 分组发送 | ✅ `send_mail(to=roles, ...)` 批量 |
| 接收反馈 | ✅ 入站 Webhook + 线程关联 |

## 流程

```
1. cron job 定时触发 Agent
2. Agent 通过 cron context 获取上次报告以来的进展
3. 生成结构化报告（Markdown 格式）
4. send_mail 群发，按角色定制
```

## 关键步骤

**定时任务：** Hermes cron job 每天 9:00 触发，注入上下文。

**角色定制：**
```python
# 给 Leader 摘要版
send_mail(to="leader@company.com", subject="周报摘要", body=summary)

# 给执行层详细版
send_mail(to=["dev@company.com","qa@company.com"], body=full_report)
```

**接收反馈：** 成员回复邮件 → Webhook 入站 → LLM 上下文关联 → 下次报告纳入。
