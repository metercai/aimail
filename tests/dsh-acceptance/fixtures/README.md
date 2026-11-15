# Fixtures

- `prompt-system.txt` / `prompt-address.txt`：人工/真实 DSH 测试用的标准提示词。
- `sample-inbound.eml`：SMTP 入站样例；脚本用 Python smtplib 发送等价内容。
- `config/*.toml`：测试栈配置（base/advanced）。
- `harness/*`：fake ctx 驱动 tssdk；Mailpit 取码；admin/whitelist 辅助。
- 所有激活码/验证码动态生成，不写死。
