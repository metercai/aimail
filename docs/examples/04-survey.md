# Survey — AgentMail Implementation

## Scenario
Agent sends batch surveys, tracks responses, sends reminders, and aggregates results.

## Infrastructure

| Capability | AgentMail |
|-----------|:--:|
| Batch send | `send_mail(to=list, ...)` |
| Reminder | cron job periodic check |
| Response collection | Inbound webhook |
| Thread tracking | Parse reply content for survey correlation |

## Flow

```
1. Agent reads target list from csv/excel
2. Batch send_mail with survey
3. cron job checks response progress
4. Unresponded → send_mail reminder
5. All responded → LLM aggregates + generates charts
6. send_mail(to=initiator) with results
```

## Key Steps

**Batch send:**
```python
for person in target_list:
    send_mail(to=person["email"], subject="2026 H2 Survey", body=template)
```

**Reminder cron:**
```python
unanswered = filter(lambda p: not p["replied"], target_list)
for p in unanswered:
    send_mail(to=p["email"], subject="Reminder: Please complete survey")
```
