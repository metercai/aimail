# 财务预审 — AgentMail 实现方案

## 场景
员工提交报销时 CC 预审 Agent，Agent 自动核验并回复预审意见。

## 基础设施

| 能力 | AgentMail 支持 |
|------|:--:|
| CC 入站检测 | ✅ agentmail 预处理器解析 CC |
| 附件解析 | ✅ Webhook 入站附件提取 |
| 发票核验 | ✅ LLM 识别发票信息 |
| 预算校验 | ✅ LLM 查询/对比预算数据 |
| 回复预审 | ✅ `send_mail(to, cc=财务)` |

## 流程

```
1. 员工发报销邮件，CC: preaudit@company.com
2. Webhook → agentmail 预处理 → LLM
3. LLM 解析附件发票、校验合规性、比对预算
4. send_mail 回复预审意见
5. 财务审核人收到 CC，做最终放行
```

## 关键步骤

**CC 触发：** 员工发送邮件时 CC 预审 Agent：
```
To:    finance@company.com
CC:    preaudit@company.com
Subject: 报销申请 — 差旅费 2026.06
附件: invoice.pdf
```

**回复预审：**
```python
result = "通过" if valid else f"驳回：{reason}"
send_mail(
    to=sender,
    subject="Re: 报销申请 — 差旅费 2026.06",
    body=f"预审结果：{result}

{detail}",
    cc="finance-reviewer@company.com",
    message_id=inbound_msg_id
)
```
