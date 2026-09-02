## Identity Declaration

Your email: {{AGENTMAIL_ADDRESS}}

Your SOUL.md:
{{SOUL_MD_CONTENT}}

Loaded skills:
{{SKILLS_LIST}}

Received inquiry from {{INQUIRY_SENDER}} (subject: {{INQUIRY_SUBJECT}}).

Reply with your capability summary using `send_mail()`, format:

```
email: {{AGENTMAIL_ADDRESS}}
role: <role from SOUL.md>
skills_loaded: [<list>]
expertise: [<areas>]
constraints: [<cannot do>]
```

If the inquirer specified a format, use their format instead.
Reply once and end — no further conversation needed.
