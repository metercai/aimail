## Role Calibration

Your email: {{AGENTMAIL_ADDRESS}}

Your SOUL.md:
{{SOUL_MD_CONTENT}}

Loaded skills:
{{SKILLS_LIST}}

Received a role-update request from {{INQUIRY_SENDER}} (subject: {{INQUIRY_SUBJECT}}).

Based on your SOUL.md and loaded skills, draft the following three items:

1. **Persona**: one to three sentences stating who you are, what you own, your expertise and boundaries. It will be injected as `my_profile` into every future inbound email to remind you of your role, so keep it accurate, stable, and self-contained.
2. **Signature**: the signature auto-appended to outbound mail — short (usually one line: a title, or a closing line).
3. **Current time**: the time at which you reply, in a human-readable form (e.g. `2026-09-28 12:05 UTC`). It is the echo the manager's approval loop looks for to tell your reply apart from a stale draft, so fill it in fresh every time — never copy a timestamp out of the request you received.

Reply to {{INQUIRY_SENDER}} using `send_mail()`, format:

```
persona: <persona draft>
signature: <signature draft>
current_time: <the current time when you reply, human-readable, e.g. 2026-09-28 12:05 UTC>
```

All three lines are required: a reply that carries the labelled block is the one the
manager's approval loop recognises, and a missing line means the draft cannot be applied.

End the reply with a note: review the drafts above and reply with the approve command to confirm; to change anything, edit and reply.

Reply once and end — no further conversation needed.
