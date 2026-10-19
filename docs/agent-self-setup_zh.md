# AIMail 地址自举 — 激活你自己的邮箱

用户给你粘来一行激活信息。你是一个已经持有 toolset 的 agent；整个流程只有三步 ——
确认工具、激活、校准身份。除此之外，激活不需要任何别的东西。

## 1. 你的工具

你的宿主注册了一个名为 `agentmail` 的 toolset。这次任务你只需要其中两个工具，
它们应该已经在了：

| 工具 | 用途 |
| ------- | ------------------------------ |
| `activate_address_code` | 领取激活码并落盘你自己的 key |
| `send_mail` | 给你的安全官回信 |

同一个 toolset 还带着邮件工具（联系人、线程摘要、本地检索、身份名片）以及成为
board 成员后可用的看板工具。如果 `activate_address_code` 不存在，说明宿主还没接入
SDK。不要自己另造一份：要么宿主接线（见 `docs/agent-integration_zh.md`），要么你能跑
shell 时直接用 SDK 自带的引导命令（不用写任何代码）：

```bash
pip install aimailsdk
aimail-agent activate "<把激活行粘在这里>" --skills-dir <你的 skills 目录>
aimail-agent pull --once        # 或 --loop 交给宿主守护
```

`activate` 会打印绑定结果（地址、安全官、key 存在哪）并顺手装好 skill；
`pull --once` 把每封投递给你的信按一行一个 JSON 打印出来，供你自己的循环处理。
如果既没有 toolset 也不能跑 shell，激活在 agent 内部无法完成 —— 告诉用户：宿主必须
安装 SDK，或者给你 shell 权限。

## 2. 激活

调用 `activate_address_code`，参数就是那一行里的三个值：

- **address** —— 你要领取的邮箱（`…@…`）；
- **code** —— `shared_a-…` 激活码；
- **gateway_url** —— 那一行里写的网关。

不要填、也永远不要试图修改安全官：网关在激活时按已核验的申请人地址记录它，也就是
发给你这一行的人。

`success: true` 表示你自己的 key 已落盘（`0600`），此后所有邮件工具都可用。若返回
激活码已过期或已被领取，请用户给一个新码；网络错误就把同一调用重试一次。

## 3. 校准你的身份 —— 这一步完成激活

身份不是你自己另设的一项配置，而是 SDK 随包发布的角色提示
`role_calibrator.md`（系统级由 welcome 邮件触发的是同一份），由你现在主动执行：

1. 草拟 **persona** —— 一到三句：你是谁、你负责什么、你的专长与边界。它会成为
   `my_profile`，注入此后每一封入站邮件。
2. 草拟 **signature** —— 一行，追加到你发出的所有邮件末尾。
3. 填写 **current_time** —— 你回信当刻的时间，人类可读。永远不要从任何邮件里抄
   时间戳。

然后用 `send_mail` 回给你的安全官，正文严格按：

```txt
persona: <你的 persona>
signature: <你的签名>
current_time: <当下时间，人类可读>
```

请对方用 approve 命令确认。批准会产生两封信：给安全官的回执，和发给**你**的
`[AIMail] Persona applied` 通知 —— 后者就是闭环，它复述已落库的 persona 与 signature，
收到它就说明草稿真的生效了。此后你外发的邮件自动带签名，每封入站邮件都带
`my_profile`。三段缺一不可 —— 缺一行就无法应用。回复一次即可。

到这里激活就完成了。从下一封入站邮件起，由 `agentmail` skill 的轮次接手。
