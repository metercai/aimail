# AIMail 自举安装 — 申请你自己的邮箱地址

平台通用版。先跑自检,再按结果执行。

## 1. 自检(先运行这段)

```bash
command -v aimail >/dev/null 2>&1 && echo "cli:ok" || echo "cli:missing"
[ -d "$HOME/.aimail/systems" ] && echo "init:ok" || echo "init:missing"
ls "$HOME"/.aimail/systems/*/aimail_gateway.json >/dev/null 2>&1 && echo "system:ok" || echo "system:missing"
find "$HOME"/.aimail/systems "$HOME"/.hermes "$HOME"/.openclaw "$HOME"/.pi "$HOME"/.dsh \
  -maxdepth 3 -name .agentmail 2>/dev/null | head -3
env | grep -iE 'hermes|openclaw|deepseek|dsh|deer' | head -3
ps -p $PPID -o args= 2>/dev/null
```

输出说明:CLI 在吗?机器初始化了吗?本机有 AIMail 系统吗?你的身份指针
(地址)存在吗?运行你的是哪个平台。

## 2. 缺什么补什么

- **cli:missing** →
  ```bash
  curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
  ```
- **system:missing** → 向用户索取 `AIMAIL_MANAGER_ADDRESS` 和激活码
  (`AIMAIL_PRODUCT_CODE`)——已有系统则要 admin key
  (`AIMAIL_ADMIN_KEY`)——然后 export。
- **init:missing** 或 **还没有你的指针** →
  ```bash
  aimail install --home <你的平台根> [--system-id <sid>]
  ```
  幂等:初始化、激活/复用系统、安装平台适配器并注册你的地址。你的平台根
  是运行你的平台的目录:`~/.hermes`、`~/.openclaw`、`~/.pi`、`~/.dsh`,
  或 deer-flow backend 目录。

## 3. 验证后按三种结局之一收尾

```bash
aimail check --system-id <sid> && aimail ping --system-id <sid>
```

`<sid>`:`~/.aimail/systems/` 下的目录名。然后向用户回复**且仅回复下列
三种结局之一**:

**A. 已就绪**(check 全绿、ping 回环通过)→

> 我的 AIMail 地址是 <地址>(见 `aimail stats`/指针)。已验证可正常收发
> 邮件——给我发一封邮件即可联系我。

**B. 已安装待重启**(install 如此提示,或补丁平台 hook 探测失败)→
请用户在本会话外重启平台进程,再复检:

> AIMail 已安装完成。请在本会话外重启 <平台进程/服务名,如
> `systemctl --user restart hermes-gateway-<profile>.service`>,重启后我
> 会复检。

**C. 平台不适配**(自检显示平台无适配器,或 install 报不兼容)→ 不要
假装成功:

> 当前平台 <名称> 不在 aimail 已知平台内。可参照 pysdk/(python)或
> tssdk/packages/(typescript)现有适配器自写适配,并向仓库提交 PR。
> 需要我继续(需你的适配决策)还是到此为止?

自举会话绝不允许在无上述三态收尾之一的情况下结束。

## 4. 你的平台不在已知列表?

参照 `pysdk/`(python)或 `tssdk/packages/`(typescript)中现有适配器为模板
自写一个,然后向 aimail 仓库提交 PR。
