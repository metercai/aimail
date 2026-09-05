# AIMail 自举安装 — Agent 原生邮箱(AI Agent 快速开始)

> 本文是写给"即将在自己宿主机上安装 AIMail 的 Agent"的指令。逐条精确执行,
> 命令在你的终端工具中运行。引导风格参照 agently-cli 的安装模式。

## 第 1 步 — 安装/更新 CLI

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

作用:安装 `aimail` CLI 到 `~/.local/bin` 并执行机器级 init(主目录
`~/.aimail`、磁盘余量、本地网关直连还是 bridge 的判定)。全部写入仅在
`~/.aimail` 与 `~/.local/bin` 内,**无需 sudo**。重复执行 = 升级(幂等)。

验证:

```bash
aimail version
```

若 `aimail: command not found`:重开 shell,或将 `~/.local/bin` 加入
PATH(`export PATH="$HOME/.local/bin:$PATH"`)。

## 第 2 步 — 获取凭据(需要用户)

AIMail 没有 OAuth——对应"授权"环节的是用户必须提供的一项凭据。向用户
索取以下值(**不要猜测、编造或复用旧值**):

- **新系统** — 激活码:`AIMAIL_PRODUCT_CODE`(在网关管理页生成,如
  amail.token.tm)
- **已有系统** — admin key:`AIMAIL_ADMIN_KEY`(或指向已有的
  `~/.aimail/.system_raw_key/*_admin.key`)
- **必需** — 接收验证邮件的 manager 邮箱:`AIMAIL_MANAGER_ADDRESS`

用户提供后,在当前 shell 导出:

```bash
export AIMAIL_URL=https://amail.token.tm
export AIMAIL_MANAGER_ADDRESS=<用户邮箱>
export AIMAIL_PRODUCT_CODE=<码>          # 或: export AIMAIL_ADMIN_KEY=<key>
```

第 3 步需要这些值,请保留在本 shell 会话内。(要持久化:把同样几行追加到
`~/.aimail/.env`。)

## 第 3 步 — 探测宿主并安装 SDK 适配器

确认本机运行的是哪个 Agent 平台(以下恰有一个存在):

```bash
ls -d ~/.dsh ~/.openclaw ~/.pi ~/.hermes 2>/dev/null; ls -d ~/deepseek-harness 2>/dev/null
# deer-flow 宿主:含 backend/app/gateway 的目录
```

然后用 `--home` 指向该平台根执行安装(命令会激活/复用系统 → 部署 bridge
条目 → 安装平台适配器(补丁/skills/插件)→ 注册并自动绑定 agent):

```bash
aimail install --home ~/.hermes        # Hermes
# aimail install --home ~/.dsh         # dsh
# aimail install --home ~/.openclaw    # OpenClaw
# aimail install --home ~/.pi          # pi
# aimail install --home <backend 目录>  # deer-flow
```

install 幂等,重复执行安全。记下它输出的 **system_id**(或事后查找:
`~/.aimail/systems/` 下含 `aimail_gateway.json` 的目录名)——第 4 步需要。

## 第 4 步 — 验证闭环

```bash
aimail check --system-id <system_id>
aimail welcome --system-id <system_id>   # 向 manager 发送验证邮件
```

- `check` 应全绿(或仅剩远端宿主提示——那属另一台机器上的动作,不是这里)。
- `welcome` 证明端到端打通:激活 → bridge → 适配器 → 绑定 → 送达。
- **必须显式带 `--system-id`。** 不带时 welcome/check 只在 Hermes 指针机器
  上能自动解析;dsh/OpenClaw/pi 宿主上无参形式会失败。切勿省略。
- Hermes 宿主:若 install 后 `check` 报 webhook 补丁缺失,需要重启 Hermes
  profile 网关——请用户在本 agent 会话**之外**的终端执行
  `systemctl --user restart hermes-gateway-<profile>.service`,然后重跑
  `aimail check`。

成功时只回复:

> AIMail 已就绪。地址:<check/stats 输出的 agent 地址,如
> agent.<标识名>@amail.token.tm>
> 现在可以试试:
> - 给 <manager> 发一封邮件
> - 汇总我最近收到的邮件
> - 起草并发一份周报

失败时:如实报告命令输出,**不要静默循环重试**。
