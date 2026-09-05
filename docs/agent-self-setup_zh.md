# AIMail 自举安装 — Agent 原生邮箱(AI Agent 快速开始)

> 本文是写给"即将在自己宿主机上安装 AIMail 的 Agent"的指令。逐条精确执行,
> 命令在你的终端工具中运行。
>
> 分工(刻意设计——AIMail 深度集成进宿主,这正是它区别于"薄 skill 邮件工具"
> 的能力差异):
> - **你(agent)完成全部准备工作**:下载、安装、补丁、绑定、预检——不留半成品。
> - **用户只在最后做一件事:重启宿主进程**,让注入的运行时加载。**绝不在本
>   会话内重启承载你自己的进程**。

## 第 1 步 — 安装/更新 CLI

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

安装 `aimail` CLI 到 `~/.local/bin` 并执行机器级 init(主目录
`~/.aimail`、磁盘余量、本地网关直连还是 bridge 的判定)。写入仅在
`~/.aimail` 与 `~/.local/bin`,**无需 sudo**。重复执行 = 升级。验证:

```bash
aimail version
```

若 `aimail: command not found`:重开 shell 或把 `~/.local/bin` 加入
PATH(`export PATH="$HOME/.local/bin:$PATH"`)。

## 第 2 步 — 获取凭据(用户提供值,你绝不猜测)

AIMail 没有 OAuth——对应的"授权"是用户必须提供的凭据。索取:

- **新系统** — 激活码:`AIMAIL_PRODUCT_CODE`(管理页生成)
- **已有系统** — admin key:`AIMAIL_ADMIN_KEY`(或已有的
  `~/.aimail/.system_raw_key/*_admin.key`)
- **必需** — manager 邮箱:`AIMAIL_MANAGER_ADDRESS`

在 shell 中导出:

```bash
export AIMAIL_URL=https://amail.token.tm
export AIMAIL_MANAGER_ADDRESS=<用户邮箱>
export AIMAIL_PRODUCT_CODE=<码>          # 或: export AIMAIL_ADMIN_KEY=<key>
```

## 第 3 步 — 探测宿主平台

本机恰有一个匹配:

```bash
ls -d ~/.dsh ~/.openclaw ~/.pi ~/.hermes 2>/dev/null; ls -d ~/deepseek-harness 2>/dev/null
# deer-flow 宿主:含 backend/app/gateway 的目录
```

## 第 4 步 — 安装 SDK 适配器(全部准备工作,由你完成)

`aimail install --home <平台根>` 激活(或复用)系统 → 部署 bridge 条目 →
安装平台适配器(补丁/skills/插件)→ 注册并自动绑定 agent:

```bash
aimail install --home ~/.hermes        # Hermes
# aimail install --home ~/.openclaw    # OpenClaw
# aimail install --home ~/.pi          # pi
# aimail install --home ~/.dsh         # dsh
# aimail install --home <backend 目录>  # deer-flow
```

幂等,重复执行安全。记下输出的 **system_id**(或在 `~/.aimail/systems/`
下找含 `aimail_gateway.json` 的目录名)。

然后**预检**——证明一切就绪、只差重启:

```bash
aimail check --system-id <system_id>
```

预期状态:config/runtime 层全绿(补丁已落盘、插件已装、资源就位);hook
探测可能仍 FAIL——这个"进程未重启导致的 FAIL"正是"只差重启"的证据。
向用户报告该状态,进入第 5 步。

## 第 5 步 — 重启宿主(用户唯一动作,在本会话之外执行)

告诉用户其平台对应的精确命令:

| 平台 | 你完成安装后,用户执行 |
|------|------------------------|
| Hermes | `systemctl --user restart hermes-gateway-<profile>.service`(每个绑定 profile 一次) |
| OpenClaw | 重启 OpenClaw gateway 进程 |
| pi | 重启 pi 进程 |
| dsh | 重载 dsh 服务 / 重启承载 mail 插件的会话 |
| deer-flow | 重启后端服务(uvicorn / systemd 单元) |

规则:
- **绝不在本会话内重启承载你自己的进程。**
- 若平台有免整进程重启的插件装载命令,优先用,该平台可跳过第 5 步。

## 第 6 步 — 验证闭环

```bash
aimail check --system-id <system_id>
aimail welcome --system-id <system_id>   # 向 manager 发送验证邮件
```

- `check` 此刻应**全绿**(hook 探测 PASS)。
- `welcome` 证明:激活 → bridge → 适配器 → 绑定 → 送达。
- **必须显式带 `--system-id`**(无参自动解析仅单系统机器可用)。
- 失败时:如实报告输出,**不要静默循环重试**。

成功时只回复:

> AIMail 已就绪。地址:<check/stats 输出的 agent 地址,如
> agent.<标识名>@amail.token.tm>
> 现在可以试试:
> - 给 <manager> 发一封邮件
> - 汇总我最近收到的邮件
> - 起草并发一份周报
