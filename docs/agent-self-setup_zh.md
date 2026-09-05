# 申请你的 AIMail 地址 — 单一 agent 自助

> 写给你——想要自己 AIMail 邮箱地址的那个 agent。第一人称、单一 agent、
> 单一地址。逐条精确执行。
>
> **范围边界**:本文只让一个 agent(你)在既有 AIMail 系统上获得地址。
> 完整平台对接(激活系统、接入平台全部 agent、bridge、运维)是 CLI 的职责
> (`aimail install/check/repair`),需要操作者执行——本文触及该边界时会
> 明说并指过去。深度宿主集成是刻意设计,唯一的用户动作是结尾的宿主重启。

## 第 1 步 — 知道你是谁

你运行在某个平台内,拥有该平台的一个身份:

- **平台**:你的 profile/session/agent id 已隐含——Hermes profile、
  dsh session、OpenClaw/pi agent、deer-flow assistant。不要用目录扫描
  猜测(一台机器可装多平台,你只属于一个)。
- **你在平台中的名字**:profile 名(Hermes)、session id(dsh)、agent id
  (OpenClaw/pi)——它将作为你的地址前缀(`{name}@{domain}`,或共享域
  `{name}.{system_name}@{domain}`)。
- **你的平台根**:`~/.hermes`、`~/.openclaw`、`~/.pi`、`~/.dsh` 或
  deer-flow backend 目录。

不确定时确认:`env | grep -iE 'hermes|openclaw|deepseek|dsh|deer'` 与
`ps -p $PPID -o args=`。

## 第 2 步 — 你已经有地址了吗?

查你的身份指针(注册时写入):

```bash
cat <平台根>/.agentmail 2>/dev/null              # openclaw / pi / dsh / deer-flow 根
cat <你的 profile 目录>/.agentmail 2>/dev/null    # hermes:~/.hermes 或 ~/.hermes/profiles/<name>
# 或直接找你的 agentmail.json:
ls ~/.aimail/systems/*/<清洗后地址>/agentmail.json 2>/dev/null
```

若存在且含你的 system_id 与 email → 你已有地址,直接跳到第 5 步验证。
否则继续。

## 第 3 步 — 确保环境就绪

按序补齐缺失项(每项都是你能自己完成的下载/安装):

1. **CLI 在吗?** 若 `aimail` 缺失,安装它(零 sudo,约 10 秒):

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

2. **机器初始化了吗?** 若 `~/.aimail/systems` 尚不存在:

```bash
aimail init
```

3. **本机已有 AIMail 系统吗?**(存在 `systems/*/aimail_gateway.json`)

   - **有** → 系统已在,你只需自己的地址(第 4 步)。
   - **没有** → 任何地址存在之前必须先创建系统。这是平台对接边界:向
     用户索取激活码(`AIMAIL_PRODUCT_CODE`)与 manager 邮箱
     (`AIMAIL_MANAGER_ADDRESS`),然后执行一次平台级 install,一条命令
     创建系统并接入你的平台:

```bash
export AIMAIL_MANAGER_ADDRESS=<用户邮箱>
export AIMAIL_PRODUCT_CODE=<码>
aimail install --home <你的平台根>
```

   (这也会注册你平台的每个既有 agent——那是 CLI 的全 agent 范围;
   你的地址在同一次执行中产生。)

4. **你的平台运行时接好了吗?** 若上面跳过 install 是因为系统存在但你
   的平台从未接入(无 webhook 补丁/无插件),一次平台级 install 修复:

```bash
aimail install --home <你的平台根> --system-id <sid>
```

   若你无法执行(无凭据),告知用户/操作者:平台对接是 CLI 维度——执行
   完成后回到第 4 步。

## 第 4 步 — 申请你的地址(注册)

- **TS 平台(openclaw / pi / dsh)**:SDK 首次使用时自动绑定(存在性守卫,
  一 session ⇔ 一地址)。触发一次你的邮件能力即可(openclaw 命令 / pi
  入口 / dsh mail-service apply);SDK 注册你并写入你的指针。
- **Hermes**:注册在 install 时按 profile 枚举。若你是对接后新增的
  profile,你的地址需要平台级注册——请操作者执行
  `python -m aimail.install register-profiles --type hermes --home ~/.hermes`
  (或 `aimail install --home ~/.hermes --system-id <sid>`),然后复查你的
  指针。

注册后你的地址为 `{name}@{domain}`(裸域系统)或
`{name}.{system_name}@{domain}`(共享域)——记录在你的 `.agentmail`
指针 / `agentmail.json` 中。

## 第 5 步 — 验证你的地址可用

```bash
aimail check --system-id <sid>     # 你的平台/agent 行全绿
aimail ping --system-id <sid>      # SMTP 回环:你能收到自己的 ping
```

然后把你的地址告诉用户,请 TA 给你发一封真实邮件(或执行
`aimail welcome --system-id <sid>` —— 向 manager 发欢迎邮件)。成功时只回复:

> 我的 AIMail 地址是 <你的地址>。给我发邮件即可联系我——现在就可以
> 试,或让我给你发邮件。

失败时:如实报告输出,**不要静默循环重试**。
