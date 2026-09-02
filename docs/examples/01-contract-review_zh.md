# 合同审核 — AgentMail 实现方案

## 场景
法务 Agent 接管合同审核邮箱，自动解析附件条款、识别风险点、回复批注版本。

## 基础设施

| 能力 | AgentMail 支持 |
|------|:--:|
| 接管 `legal@` 邮箱 | ✅ 注册 agent 收件地址 |
| 收附件 | ✅ Webhook 入站，`send_mail` 入参含 `attachments` |
| 发件 | ✅ `send_mail(to, subject, body, cc=..., attachments=...)` |
| 白名单控制 | ✅ 默认白名单，非授权发件人无法触达 |
| 线程追踪 | ✅ `In-Reply-To` / `References` 自动链路 |

## 流程

```
1. 法务 Agent 注册 legal@company.com
2. 业务方发邮件给 legal@，附合同附件
3. Webhook 推送 → agentmail 预处理 → LLM
4. LLM 解析条款、识别风险点
5. send_mail(to=业务方, body=批注版, cc=审批人) → 回复
```

## 关键步骤

**收件：** Webhook 自动接收，预处理引擎将 MIME 转为 Markdown，剥离样式噪声。

**处理：** LLM 根据 prompt 识别条款类型（违约责任、知识产权、保密条款等）、标记风险等级。

**回复：**
```python
send_mail(
    to=sender,
    subject="Re: " + original_subject,
    body=review_report,
    cc=approver_email,
    message_id=inbound_message_id  # 线程关联
)
```
