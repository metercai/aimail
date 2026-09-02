# 客服支持 — AgentMail 实现方案

## 场景
Agent 接管 `support@` 邮箱，自动分类意图、FAQ 自动回复、复杂问题转人工。

## 基础设施

| 能力 | AgentMail 支持 |
|------|:--:|
| 接管 support@ | ✅ 注册 agent 收件地址 |
| 意图分类 | ✅ LLM 解析邮件内容 |
| FAQ 匹配 | ✅ LLM prompt 内置 FAQ 库 |
| 转人工 | ✅ `send_mail` 转发 + 上下文摘要 |
| 归档追溯 | ✅ 线程追踪 + 邮件归档 |

## 流程

```
1. 客户发邮件给 support@company.com
2. Webhook → agentmail 预处理 → LLM
3. LLM 分类：FAQ / 复杂 / 投诉
4. FAQ → send_mail 自动回复解决方案
5. 复杂 → send_mail 转接人工 + 附上下文摘要
6. 投诉 → 优先转接 + 标记紧急
```

## 关键步骤

**FAQ 自动回复：** 匹配到"密码重置" → 回复重置链接 + 步骤。

**转人工：**
```python
send_mail(
    to="human-support@company.com",
    subject="[转接] 客户投诉：订单未发货",
    body=f"客户: {sender}
意图: 投诉
摘要: {summary}

"
         f"原始邮件:
{original_body}",
    cc=sender  # 客户也收到确认
)
```
