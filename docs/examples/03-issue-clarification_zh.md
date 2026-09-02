# 问题澄清 — AgentMail 实现方案

## 场景
Agent 发现信息矛盾或缺失时，自动发送澄清邮件并等待回复后继续推进任务。

## 基础设施

| 能力 | AgentMail 支持 |
|------|:--:|
| 发送澄清邮件 | ✅ `send_mail(to, subject, body)` |
| 接收回复并继续 | ✅ 入站 Webhook + `In-Reply-To` 线程追踪 |
| 上下文保持 | ✅ `set_email_summary` 存储线程状态 |

## 流程

```
1. LLM 在任务中发现问题
2. send_mail(to=同事, subject="[澄清] xxx", body=问题描述+上下文)
3. 同事回复 → Webhook → agentmail 预处理
4. LLM 解析回复，提取答案
5. 继续原任务
```

## 关键步骤

**发送澄清：**
```python
send_mail(
    to="colleague@company.com",
    subject="[澄清] Q3 营收数据口径",
    body="在周报中引用的 Q3 数据存在两个版本：
"
         "A. 财务口径：1200 万
"
         "B. 业务口径：1350 万
"
         "请确认以哪个口径为准？"
)
```

**线程追踪：** `set_email_summary(msg_id, "等待 Alice 确认 Q3 数据口径")` — 下次入站自动加载上下文。
