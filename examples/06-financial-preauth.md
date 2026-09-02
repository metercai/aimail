# Financial Pre-Audit — AgentMail Implementation

## Scenario
Employees CC the pre-audit Agent on expense claims. Agent auto-verifies invoices, checks compliance, and replies with pre-audit opinion.

## Infrastructure

| Capability | AgentMail |
|-----------|:--:|
| CC inbound detection | agentmail preprocessor parses CC |
| Attachment parsing | Webhook inbound attachment extraction |
| Invoice verification | LLM recognizes invoice details |
| Budget check | LLM queries/compares budget data |
| Pre-audit reply | `send_mail(to, cc=finance)` |

## Flow

```
1. Employee sends expense email, CC: preaudit@company.com
2. Webhook → preprocessing → LLM
3. LLM parses invoice, checks compliance and budget
4. send_mail replies with pre-audit result
5. Finance reviewer sees CC, does final approval
```

## Key Steps

**CC-triggered:**
```
To:    finance@company.com
CC:    preaudit@company.com
Subject: Expense Claim — Travel June 2026
Attachment: invoice.pdf
```

**Pre-audit reply:**
```python
result = "Approved" if valid else f"Rejected: {reason}"
send_mail(
    to=sender,
    subject="Re: Expense Claim — Travel June 2026",
    body=f"Pre-audit: {result}

{detail}",
    cc="finance-reviewer@company.com",
    message_id=inbound_msg_id
)
```
