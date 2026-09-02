# Issue Clarification — AgentMail Implementation

## Scenario
Agent detects information conflicts or gaps during tasks and auto-sends clarification emails, resuming after reply.

## Infrastructure

| Capability | AgentMail |
|-----------|:--:|
| Send clarification | `send_mail(to, subject, body)` |
| Receive and resume | Inbound webhook + `In-Reply-To` threading |
| Context preservation | `set_email_summary` stores thread state |

## Flow

```
1. LLM detects issue during task execution
2. send_mail(to=colleague, subject="[Clarify] ...", body=context+question)
3. Colleague replies → Webhook → preprocessing
4. LLM parses reply, extracts answer
5. Continues original task
```

## Key Steps

**Sending clarification:**
```python
send_mail(
    to="colleague@company.com",
    subject="[Clarify] Q3 Revenue Reporting Basis",
    body="Two versions of Q3 data exist:
"
         "A. Finance: $12M
B. Business: $13.5M
"
         "Which should be used?"
)
```

**Thread tracking:** `set_email_summary(msg_id, "Waiting for Alice to confirm Q3 data")`.
