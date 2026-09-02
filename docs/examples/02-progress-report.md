# Progress Report — AgentMail Implementation

## Scenario
Agent periodically summarizes project progress, generates structured reports, and sends role-tailored content.

## Infrastructure

| Capability | AgentMail |
|-----------|:--:|
| Scheduled trigger | Hermes cron job |
| Group delivery | `send_mail(to=list, ...)` |
| Receive feedback | Inbound webhook + thread tracking |

## Flow

```
1. cron job triggers Agent on schedule
2. Agent retrieves progress since last report
3. Generates structured report in Markdown
4. send_mail to team with role-specific content
```

## Key Steps

**Scheduling:** Hermes cron job fires daily at 9:00 AM with context injection.

**Role-tailored delivery:**
```python
send_mail(to="leader@company.com", subject="Weekly Summary", body=summary)
send_mail(to=["dev@company.com","qa@company.com"], body=full_report)
```

**Feedback loop:** Member replies → Webhook inbound → LLM context → next report.
