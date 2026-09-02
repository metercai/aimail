# 调查问卷 — AgentMail 实现方案

## 场景
Agent 批量发送问卷、跟踪回收、催办、汇总分析。

## 基础设施

| 能力 | AgentMail 支持 |
|------|:--:|
| 批量发送 | ✅ `send_mail(to=多人, ...)` |
| 催办 | ✅ cron job 定时检查 |
| 回收汇总 | ✅ 入站 Webhook 接收回复 |
| 线程追踪 | ✅ 解析回复内容关联问卷 |

## 流程

```
1. Agent 从 csv/excel 读取目标名单
2. 批量 send_mail 发送问卷
3. cron job 定时检查回收进度
4. 未回复者 → send_mail 催办
5. 全部回收 → LLM 汇总 + 生成图表
6. send_mail(to=发起人) 反馈结果
```

## 关键步骤

**批量发送：**
```python
for person in target_list:
    send_mail(
        to=person["email"],
        subject="2026 H2 员工满意度调研",
        body=questionnaire_template
    )
```

**催办逻辑（cron job）：**
```python
unanswered = filter(lambda p: not p["replied"], target_list)
for p in unanswered:
    send_mail(to=p["email"], subject="催办：请完成员工满意度调研")
```
