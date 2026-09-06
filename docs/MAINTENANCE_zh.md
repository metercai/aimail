# AIMail 安装与维护指南

> 适用对象:`aimail` CLI(仓库 `cli/`)——在机器上安装、运维与维护 AIMail
> 对接的唯一本机工具。品牌语义:**aimail** = 对外品牌(CLI、网关、配置文件);
> **agentmail** = agent 内部语义(tools/skills/`agentmail.json`)。

---

## 目录

1. [目标与范围](#1-目标与范围)
2. [架构与本机布局](#2-架构与本机布局)
3. [安装](#3-安装)
4. [维护工作流(stats → check → repair)](#4-维护工作流stats--check--repair)
5. [达到的效果](#5-达到的效果)
6. [速查](#6-速查)
7. [故障排查](#7-故障排查)
8. [机器迁移](#8-机器迁移)
9. [契约与单一真源](#9-契约与单一真源)

---

## 1. 目标与范围

### CLI 是什么

`aimail` 是 AIMail 体系的**本机基础设施工具**——只在本机执行,绝不做远程
运维;是集成资源(激活、域名、绑定、路由)的**唯一写入路径**,保证本机状态
与网关一致。

### 三层运行模型

| 层 | 工具 | 运行时机 | 职责 |
|----|------|---------|------|
| 机器准备(一次) | `aimail init` | 任意机器 | 锁定网关 URL、判定直连 push 还是 bridge、构建 `~/.aimail` |
| 系统集成(每系统可多次) | `aimail install` | 每平台根 | 激活/复用系统、绑定平台、合并 bridge 条目 |
| Agent 运行时 SDK | 平台包 | agent 宿主 | `pysdk`(python,hermes/deerflow)或 `tssdk`(openclaw/pi/dsh);自检 → 自动绑定 |

CLI **自身不带任何运行时资源**:`cli + 任一 SDK + 配置文件 = 完整对接`。
平台补丁由 CLI 委托 SDK 执行(`python -m aimail.install install --type hermes|deerflow`)。

### 维护闭环(本文档主线)

```
aimail stats -a     →  本机对接/健康/断链全景
aimail check        →  全面体检(配置 → 运行时资源 → 链路)
aimail repair       →  按 check 发现执行幂等修复阶梯
```

先用 `stats` 发现问题,`check` 精确定位,`repair` 修复本机可修项,复检直到
只剩真正的宿主侧动作。

---

## 2. 架构与本机布局

### 目录树

```
~/.aimail/
├── systems/{system_id}/
│   ├── aimail_gateway.json     # 网关连接配置(2026-09-04 起正式名;
│   │                           #   旧 agentmail_gateway.json 首次读取自动迁移)
│   ├── board/                  # 系统级 A2A 角色 prompt(回退)
│   └── {agent_addr}/           # 按地址隔离目录(清洗后的邮箱)
│       ├── agentmail.json      # agent 配置——9 个必备字段(见 §9)
│       └── role_prompt/        # 地址级角色 prompt(优先)
├── logs/
│   ├── aimail-bridge.log       # bridge 运行日志
│   └── aimail.{addr}.log       # 每 agent 处理日志(不在 mail/ 下)
├── bridge/
│   ├── aimail_bridge.toml      # bridge 配置(pull.systems 列表)
│   ├── aimail_routes.toml      # 路由表:email → 本地入站端点
│   ├── bin/aimail-bridge       # bridge 二进制
│   └── bridge.pid
├── mail/{addr}/{yyyymm}/in-*.json   # 入站快照(调测用)
├── .system_raw_key/{sid}_admin.key  # 原始 admin key(仅集成时)
└── .env                            # 机器级 env(自举安装)
```

平台根指针(`.agentmail`,内容 `{system_id, email}`):
`~/.hermes/.agentmail` 或 `profiles/*/.agentmail`(hermes)·
`~/.openclaw/.agentmail`(openclaw)· `~/.pi/.agentmail`(pi)·
`~/.dsh/.agentmail`(dsh)· `~/.deer-flow/.agentmail`(deerflow)。

### 网络模型

agent 侧一律 **push**。网关解析为本机(`127.0.0.1`/`localhost`/本机 IP)→
直连 push,无需 bridge;否则本机 `aimail-bridge` 以 pull 模式向网关轮询
待发邮件,再按 `aimail_routes.toml` 投递给本地入站端点。是否需要 bridge
是**机器级一次性判断**,由 `aimail init` 完成。

### 三份权威配置文件

| 文件 | 内容 | 写入方 |
|------|------|--------|
| `systems/{sid}/aimail_gateway.json` | gateway_url, admin_key, system_id, system_name, manager_address, system_home, domain, webhook_host | `install`/`reset` → setup_system.py;`repair` 只补缺 `system_home`/`webhook_host` |
| `systems/{sid}/{addr}/agentmail.json` | 9 字段:email, gateway_url, domain, system_id, system_name, manager_address, api_key, webhook_url, webhook_secret | 注册链(register_profiles/register_agent/bind_agent) |
| `bridge/aimail_bridge.toml` + `aimail_routes.toml` | pull 系统列表 + 路由表 | deploy_bridge.py;`aimail bridge --system-id` |

`aimail_gateway.json` 里的 `system_home` 是 **stats 平台标签的唯一来源**
(按目录特征探测,绝不猜名)。

---

## 3. 安装

### 第 0 步 — 机器环境(5 分钟路径,零文件操作)

```bash
# 宿主已装 → export 环境变量(立即生效,无需建文件):
export AIMAIL_URL=https://aimail.token.tm
export AIMAIL_MANAGER_ADDRESS=you@example.com
export AIMAIL_ADMIN_KEY=<key>          # 复用路径  或
export AIMAIL_PRODUCT_CODE=<code>      # 新系统路径(+ AIMAIL_SYSTEM_NAME)

# 自举(装 toolkit 到 ~/.aimail、symlink ~/.local/bin/aimail,
# 并把上面 export 的 AIMAIL_* 固化进 ~/.aimail/.env):
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

### 第 1 步 — `aimail init`(机器级,一次,零系统可跑)

```bash
aimail init [--gateway-url URL]
```

构建 `~/.aimail/{systems,logs,bridge}`(0700)、磁盘研判(<100 MiB 告警)、
网络结构判定:本地网关 → 直连(无 bridge);远程网关 → 部署 bridge 二进制 +
骨架配置。重复执行安全(全部幂等)。

### 第 2 步 — `aimail install`(系统级,可重复,幂等)

```bash
aimail install --home <平台根> [--system-id <sid>]
              [--product-code <码> | --admin-key <key>]
              [--manager <addr>] [--domain <域名>] [--system-name <标识名>]
```

- **新系统**(`--product-code`):服务端激活(码的服务端 claim 原子;同一码
  重复提交在任何本地写入前即失败)。
- **已有系统**(`--admin-key` 或已存配置):仅重置/固化本机连接配置,**不
  重新激活**——绝不二次消耗激活码。
- 完整链路:激活/复用 → 域名确保 → bridge 部署(按 sid merge 条目,**复用
  已有 bridge key**)→ 平台绑定(hermes:SDK 补丁+profile+skill;openclaw/
  pi:agent 注册+指针;dsh:插件;deerflow:SDK reconcile+补丁)。
- 重复执行安全:每步存在性检查或按 system_id 合并,不会产生孤儿凭证。

### 第 3 步 — 平台侧绑定

hermes/openclaw/pi/deerflow 在 install 内完成绑定。dsh 的 session 惰性
绑定:`dsh-aimail` 首次使用自动绑(一 session ⇔ 一地址,存在性守卫);
手工等价:`python3 cli/dsh/bind_agent.py [--session-id …] [--preset mail]`。

### 第 4 步 — 验证

```bash
aimail check --system-id <sid>     # 全面体检(见 §4)
aimail ping --system-id <sid>      # ping → pong 闭环(权威判据 = agent 侧日志)
aimail welcome --system-id <sid>   # welcome 端到端(API 模式,noreply@{网关域} 发件)
```

env 优先级:**CLI 参数 > shell 环境变量 > `~/.aimail/.env` > 仓库 `.env` >
内置默认**;`.env` 自动加载,常用值只需配置一次。

---

## 4. 维护工作流(stats → check → repair)

### 4.1 `aimail stats` — 本机对接总览(只读)

```bash
aimail stats        # 默认视图:系统 + agent + 邮件统计 + 到期
aimail stats -a     # 全面视图:健康标注 + 断链系统 + 本机平台段
```

`-a` 逐系统健康:`home-ok/home-missing/home-dir-missing` ·
`pointer:…/pointer-none` · `cloud: ok/unlinked/broken-config/unreachable`。
断链纯事实判定(连接字段缺失 = broken-config;网关 403/404 = unlinked;
网络错误 = unreachable,不算断链)。平台段列出五个平台根的对接状态,尾部
给出维护链路提示。

### 4.2 `aimail check` — 全面体检(顺序固定)

维度顺序(用户定调):**配置文件 → 平台运行时资源 → agent 配置 → 链路探测**。

| 维度 | 层 | 检查项 |
|------|----|--------|
| 配置文件 | L0 | `aimail_gateway.json` 完备(gateway_url/admin_key/`system_home`/pointer)· `aimail_bridge.toml` 结构(mode、pull 条目、admin_key 与 gateway.json 比对)· `agentmail.json` 九字段完备 + 内部一致(system_id=sid、gateway_url 同源、domain=email 后缀) |
| 网关/Bridge | L1/L2 | 网关 health + SMTP :25 + whoami scope;bridge 进程 + pull 路径 + 路由覆盖(每个 agent 的 email 必须有路由条目) |
| 平台运行时资源 | L2r | hermes:webhook.py `PREPROCESS_REGISTRY` + profiles.py `AmailGateway` 补丁标记、toolsets、skills、board/role_prompt/common.md · openclaw:插件已装 + skills · deerflow:app.py `aimail_inbound` 双锚点 · pi:指针匹配 |
| agent 配置 | L3 | 各平台适配器:name&api_key / webhook secret / skill / toolset / register |
| 链路 | L4 | 对真实入站端点探测——**404 = 路由未注册 = FAIL**;远端(非回环)目标本机不可探测 → PASS 附注,绝不误报 FAIL |

### 4.3 `aimail repair` — 幂等修复阶梯

```bash
aimail repair [--system-id <sid>] [--home <root>] [--deep] [--dry-run]
```

`--dry-run` 只列计划。阶梯(每步幂等):

1. bridge 存活确保(死了则拉起)— 2. `bridge --system-id` 重刷路由 —
3. 网关 webhook 配对修复(证据驱动)— 4. gateway 配置回填
(`system_home`/`webhook_host`,只补缺、绝不覆盖)— 5. 平台指针重建
(仅当平台根确定且指针缺失)— 6. 运行时资源重部署
(`python -m aimail.install install --type …`,幂等;平台在远端 → 跳过并
提示宿主机执行)— 7. `agentmail.json` 补缺 + `webhook_url` 对齐存活
路由目标(仅本机端点)— 8. routes 缺条目补齐 — 9. bridge pull 条目
admin_key 对齐 gateway.json(权威源)。

`--deep` 额外执行 webhook 配对重写与 stuck pending 清理。

`repair` 结束自动复检。复检后仍 FAIL 的必须是真实宿主侧项(远端平台未跑、
agent 需重注册……)——工具如实报告,不掩盖。

### 4.4 日常操作

| 动作 | 命令 | 说明 |
|------|------|------|
| 添加域名 | `aimail domain -s <sid> -a example.com` | **CLI 是唯一入口**(SPA 添加按钮已收敛);输入小写归一,服务端配额 + UNIQUE 兜底 |
| 查看域名 | `aimail domain -s <sid>` | 非共享系统可持多个裸域;任一个裸域都可承载续期码领取 |
| 续期 | `aimail renew -s <sid> -c <码>` | 叠加式 `max(now,当前)+validity`,配额 max 合并,自动解除挂起 |
| 到期查看 | `aimail renew -s <sid> --status` | 只读,不耗码 |
| 主 agent 名 | `aimail mailname -s <sid> [-d NAME]` | 与已注册地址做冲突检测 |
| 重置配置 | `aimail reset -H <root> -s <sid>` | 只走 admin-key 路径,key 不动 |
| bridge 维护 | `aimail bridge` / `--restart` / `-s <sid>` | 状态 / 单实例重启 / 重刷路由 |
| 卸载 | `aimail uninstall -s <sid> [-H <root>] [-y]` | 网关注销 → 平台清理 → 本机数据;幂等 |
| 端到端 | `aimail ping` / `welcome` / `persona` | 心跳 / welcome / persona 闭环 |

短参数全局一致:`-s` system-id · `-H` home · `-g` gateway-url · `-m`
manager · `-c` code · `-n` system-name(install/reset)或 dry-run(repair)
· `-d` domain(install)或 default(mailname)· `-w` no-wait
(welcome/persona)(或 domain 的 `--webhook-url`)· `-a` all(stats)或
add(domain)· `-t` status(renew)
或 timeout(ping)· `-D` deep · `-r` restart · `-k` admin-key · `-y` yes。
长参数永不改名。

---

## 5. 达到的效果

能力要点:**stats** 按事实标注健康(如缺 `system_home` 显 `[?]`、指针缺失);
**check** 能抓声明 webhook 失效、路由缺失、bridge pull admin_key 漂移等真
问题;**repair** 修净本机可修项并如实保留宿主侧 FAIL;重复 **install** 不产生
孤儿 bridge key、绝不二次激活。净效果:**stats 指方向 → check 精定位 →
repair 修复 → 复检确认**,残余红项均为真实宿主侧动作。

---

## 6. 速查

子命令按场景分组(`aimail --help` 即此布局):

```
setup      init  install  uninstall  reset
operate    stats  renew  version
diagnose   check  repair  ping  welcome  persona
resources  domain  mailname  bridge
```

平台特征探测顺序:`pi`(~/.pi + agent/)→ `dsh`(~/.dsh + profiles/ +
storages/)→ `hermes`(hermes-agent/ 或 profiles/)→ `openclaw`
(openclaw.json)→ `deerflow`(backend/app/gateway/)→ `unknown`。
`--system-id` + 已存 `system_home` 反查优先于自动探测;指针归属为次。

日志:bridge → `~/.aimail/logs/aimail-bridge.log`;每 agent →
`~/.aimail/logs/aimail.{addr}.log`(JSON 行;`dir` = ping_intercepted /
pong_sent / pong_returned / inbound / outbound)。无自动轮转——需要时用
logrotate(模式见仓库历史文档)。

---

## 7. 故障排查

### stats 显示 `[?]` / check FAIL `config/system_home`

**原因:** `aimail_gateway.json` 无 `system_home`(或目录已失效)——平台标签
与所有平台相关检查失去锚点。

**修复**(在平台自身宿主上):
```bash
aimail install --home <平台根> --system-id <sid>   # 只补缺,不覆盖
# 或交给 repair:
aimail repair --system-id <sid> --home <平台根>
```

### check FAIL `hook … 404`

**原因:** 平台入站路由未注册(插件缺失、装插件后网关未重启、端点路径过期)。
404 如今按设计判 FAIL。

**修复:** openclaw:`openclaw plugins install npm-pack:<openclaw-aimail.tgz>
--force` + 重启网关;hermes:重跑 SDK 安装
(`python -m aimail.install install --type hermes --home ~/.hermes`)+ 重启
profile 网关;然后 `aimail repair --system-id <sid>`。

### check FAIL `routes-entry` / `routes-target`

**原因:** bridge 路由表缺该 agent(pull 模式无法投递)或路由目标与声明
webhook 不一致。

**修复:** `aimail repair --system-id <sid>`(阶梯 2/7/8:重刷路由、webhook_url
对齐存活目标)。目标主机是远端(pi/deerflow 在别机)→ 到该机启动其入站。

### ping 卡在 "pong not returned"

查每 agent 日志三阶段:`grep <ping_id> ~/.aimail/logs/aimail.{addr}.log`;
核对 `agentmail.json` 的 email 与 api_key;`aimail reset -H <平台根> -s <sid>` 重新固化。

### bridge 拉不到邮件

`aimail bridge`(进程/配置/路由)→ `curl https://aimail.token.tm/health` →
`tail -20 ~/.aimail/logs/aimail-bridge.log` → `aimail repair -s <sid>`。

### 重复 install 出问题了?

不可能:激活服务端原子、配置写入合并/存在性检查、bridge key 复用。若中途
失败,`aimail check` + `aimail repair` 恢复不变量状态。

---

## 8. 机器迁移

邮件/存储都在网关;机器只留本机配置 + 快照 + bridge。

```bash
# 1. 旧机器——收集凭据:
ls ~/.aimail/.system_raw_key/          # {sid}_admin.key
ls ~/.aimail/systems/{sid}/            # aimail_gateway.json + agents

# 2. 新机器——机器环境一次:
git clone https://github.com/metercai/aimail.git && cd aimail
cp docs/.env.example .env              # AIMAIL_URL + AIMAIL_MANAGER_ADDRESS
aimail init                            # 网关发现 + bridge 骨架

# 3. 恢复凭据:
mkdir -p ~/.aimail/.system_raw_key && cp <旧>/{sid}_admin.key ~/.aimail/.system_raw_key/
export AIMAIL_ADMIN_KEY=$(cat ~/.aimail/.system_raw_key/{sid}_admin.key)

# 4. 复用系统(不重新激活):
aimail install --home <平台根> --system-id <sid>

# 5. 验证:
aimail check --system-id <sid> && aimail welcome --system-id <sid>
```

有 admin key 时 `install` 永不二次激活(reuse 路径),迁移不消耗激活码。

---

## 9. 契约与单一真源

Python CLI 代码引用以下契约;**TS SDK(`tssdk/`)是唯一真相源**——只引用,
不重定义。

**入站端点**(各平台,`POST`):openclaw `:18789/aimail/inbound` · pi
`:9101/aimail/inbound` · dsh `:9099/aimail/inbound` · deerflow
`:8001/aimail/inbound` · hermes `:8646/webhooks/aimail-inbound`(端口取
profile 配置)。本地入站 URL 就是 `agentmail.json` 存的 `webhook_url`,
也是 bridge 路由表的目标。

**`aimail_gateway.json`**(2026-09-04 与网关名对齐而改名;旧名
`agentmail_gateway.json` 首次读取自动迁移):`gateway_url`, `admin_key`,
`system_id`, `system_name`, `manager_address`, `domain`, `system_home`,
`webhook_host`, `save_raw_snapshots`(恒写入,默认 `true`),
`default_agent_name`(可选值字段,`mailname` 写入)。

**`agentmail.json`** 9 必备字段:`email`, `gateway_url`, `domain`,
`system_id`, `system_name`, `manager_address`, `api_key`, `webhook_url`
(本地入站端点——bridge 路由唯一信任源), `webhook_secret`。

**地址语义**(共享域):agent 地址 = `{agent}.{system_name}@{共享域}`
(如 `agent.xianlin@aimail.token.tm`、`pi.xianlin@…`,经 `email_for_agent`
派生);系统标识名(`system_name`)在同一共享域内**全局唯一**(领取占用 +
激活 UNIQUE + 地址注册 UNIQUE 三层防线)——两个不同系统不可能在同一共享
域用同一个标识名。非共享系统地址 = `{agent}@{裸域}`,可持有多个裸域
(任一个都可承载续期码领取)。
