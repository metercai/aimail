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

## 3. 验证

```bash
aimail check --system-id <sid> && aimail ping --system-id <sid>
```

`<sid>`:`~/.aimail/systems/` 下的目录名。若 install 输出提示需要重启宿主
(补丁型平台),请用户在本会话外重启该平台进程,再重跑 check。

完成。只回复你的地址(从指针/`aimail stats` 获取)。

## 4. 你的平台不在已知列表?

参照 `pysdk/`(python)或 `tssdk/packages/`(typescript)中现有适配器为模板
自写一个,然后向 aimail 仓库提交 PR。
