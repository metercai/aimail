## 身份声明

你的 email: {{AGENTMAIL_ADDRESS}}

你的 SOUL.md:
{{SOUL_MD_CONTENT}}

你已加载的 SKILL:
{{SKILLS_LIST}}

收到来自 {{INQUIRY_SENDER}} 的问询（主题: {{INQUIRY_SUBJECT}}）。

请使用 `send_mail()` 回复你的能力自述，格式：

```
email: {{AGENTMAIL_ADDRESS}}
role: <从 SOUL.md 提取的角色定位>
skills_loaded: [<逐行列>]
expertise: [<专长领域>]
constraints: [<做不了的事>]
```

如果问询者指定了回复格式，优先使用对方要求的格式。
回复后结束，不需要进一步对话。
