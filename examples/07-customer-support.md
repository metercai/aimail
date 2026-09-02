# Customer Support — AgentMail Implementation

## Scenario
Agent takes over `support@` inbox, auto-classifies intent, auto-replies to FAQs, and escalates complex issues to human agents.

## Infrastructure

| Capability | AgentMail |
|-----------|:--:|
| Take over support@ | Register agent address |
| Intent classification | LLM parses email content |
| FAQ matching | LLM prompt with embedded FAQ library |
| Escalate to human | `send_mail` forward with context summary |
| Record keeping | Thread tracking + email archiving |

## Flow

```
1. Customer emails support@company.com
2. Webhook → preprocessing → LLM
3. LLM classifies: FAQ / Complex / Complaint
4. FAQ → send_mail auto-reply with solution
5. Complex → send_mail escalate + context summary
6. Complaint → priority escalation + urgency flag
```

## Key Steps

**FAQ auto-reply:** Matches "password reset" → replies with reset link and steps.

**Escalate to human:**
```python
send_mail(
    to="human-support@company.com",
    subject="[Escalate] Complaint: Order not shipped",
    body=f"Customer: {sender}
Intent: Complaint
Summary: {summary}

"
         f"Original email:
{original_body}",
    cc=sender
)
```
