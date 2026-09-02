# Contract Review — AgentMail Implementation

## Scenario
A legal AI Agent takes over the `legal@` inbox, auto-parses contract attachments, identifies risks, and replies with annotated versions.

## Infrastructure

| Capability | AgentMail |
|-----------|:--:|
| Take over `legal@` inbox | Register agent address |
| Receive attachments | Webhook inbound + `send_mail` attachment support |
| Send email | `send_mail(to, subject, body, cc=..., attachments=...)` |
| Whitelist | Default whitelist, unauthorized senders blocked |
| Thread tracking | `In-Reply-To` / `References` auto-linking |

## Flow

```
1. Register legal@company.com as agent inbox
2. Business team sends email to legal@ with contract attachment
3. Webhook → agentmail preprocessing → LLM
4. LLM parses clauses, identifies risk levels
5. send_mail(to=sender, body=annotated, cc=approver) → reply
```

## Key Steps

**Inbound:** Webhook receives, preprocessing converts MIME to Markdown.

**Processing:** LLM identifies clause types (liability, IP, confidentiality) and risk levels.

**Reply:**
```python
send_mail(
    to=sender,
    subject="Re: " + original_subject,
    body=review_report,
    cc=approver_email,
    message_id=inbound_message_id
)
```
