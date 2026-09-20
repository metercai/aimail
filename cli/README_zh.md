[English](README.md) | 🇨🇳 中文

# aimail CLI

> 适用对象:`aimail` ——在Agent所在机器上安装与维护 AIMail的命令行工具。运维的范围包括：本机的aimail基础环境的建立、Agent系统实例的aimail对接和配置，以及某个具体Agent的aimail相关配置。

***

## 1. 目标与范围

### CLI 是什么

`aimail` 是 AIMail 体系在**本机的基础运维工具**——只在本机执行,管理运维本机的aimail相关资源，不做远程运维;是agent与aimail对接(激活、域名、绑定、路由)的**唯一写入路径**,保证本机状态健康，与网关状态同步一致。

### 三层运维对象

| 层         | 工具                | 对象标识            | 职责                                |
| --------- | ----------------- | --------------- | --------------------------------- |
| 本机基础环境的建立 | bootstrap(自动)     | 宿主机系统           | 主目录/网关判定/bridge 就位(bootstrap 时完成) |
| Agent系统对接 | `aimail install等` | Agent平台的根目录/SID | 激活/复用系统、绑定平台、绑定资源导入、合并 bridge 条目等 |
| Agent参数配置 | `aimail address`  | Agent 标识/地址     | 查看/设默认主地址名/地址改名/设安全员(manager)     |

### 平台安装对接

平台适配全部由 SDK 承载,CLI 只按注册表(`cli/platforms.json`)调度:新增平台 = 注册表登记 + SDK 侧提供适配,不改 CLI 协议。各平台的安装动作、注册器与重启要求:

| 平台       | 平台根            | 运行时           | 安装动作                                                                                                                                                                        | 重启要求                                     |
| -------- | -------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Hermes   | `~/.hermes`    | Python(pysdk) | venv 存在时先在 venv 内 `pip install aimailsdk`（导入名 `aimail`）;SDK 安装展开 SKILL/toolsets/board 资源并给 webhook.py 注入 `PREPROCESS_REGISTRY`;注册本根的 agent 集合（`--all-agents` 时注册全部 profile） | 重启 hermes gateway                        |
| DeerFlow | `~/.deer-flow` | Python(pysdk) | SDK 安装 → `install-skill.sh` + `install-mcp.sh` → 注册(`manage.py register --all`)                                                                                             | 重启 8001(上游仓由补丁安装)                        |
| OpenClaw | `~/.openclaw`  | TS(tssdk)     | `openclaw plugins install openclaw-aimail --force --accept-capabilities` → 注册(`openclaw aimail register`;全量 `register-all`)                                                 | 重启 openclaw gateway                      |
| DSH      | `~/.dsh`       | TS(tssdk)     | `dsh plugin --profile web add dsh-aimail`(需先有 `dsh` CLI)                                                                                                                    | 绑定由 dsh session(mail preset)自动 auto-bind |
| Pi       | `~/.pi`        | TS(tssdk)     | `pi install npm:pi-aimail`                                                                                                                                                  | 重启 pi                                    |

通用命令:

```bash
aimail install --home <平台根>                    # 单 agent
aimail install --home <平台根> --all-agents        # 平台根下全部 agent(多 profile 平台)
aimail install --home <平台根> --system-id <sid>   # 复用已有系统(不重新激活)
```

对接完成的判定: `执行 aimail welcome 后，安全员收到 agent 回复的欢迎邮件`。

### 系统维护闭环

```
aimail stats -a     →  本机对接/健康/断链全景
aimail check        →  全面体检(配置 → 运行时资源 → 链路)
aimail repair       →  按 check 发现执行幂等修复阶梯
```

先用 `stats` 发现问题,`check` 精确定位,`repair` 修复本机可修项,复检直到
只剩真正的宿主侧动作。

***

## 2. 架构与目录树

### 目录树(`~/.aimail`)

```
~/.aimail/
├── systems/{system_id}/
│   ├── aimail_gateway.json     # 网关连接配置(系统级)
│   ├── board/                  # 系统级 A2A 角色 prompt(回退)
│   └── {agent_addr}/           # 按地址隔离目录(清洗后的邮箱)
│       ├── agentmail.json      # agent 配置——9 个必备字段
│       └── role_prompt/        # 地址级角色 prompt(优先)
├── logs/
│   ├── aimail-bridge.log       # bridge 运行日志
│   └── aimail.{addr}.log       # 每 agent 处理日志
├── bridge/
│   ├── aimail_bridge.toml      # bridge 配置(pull.systems 列表)
│   ├── aimail_routes.toml      # 路由表:email → 本地入站端点
│   ├── bin/aimail-bridge       # bridge 二进制
│   └── bridge.pid
├── bin/                            # 程序根(程序副本 + 宿主载荷)
│   ├── aimail-src/                 # bootstrap 安装的 aimail 程序副本
│   └── mcp/                        # 宿主适配运行时载荷(带版本戳)
├── mail/{addr}/{yyyymm}/in-*.json   # 快照:in-*(入站)/out-*(出站)
├── .system_raw_key/{sid}_admin.key  # 原始 admin key(仅集成时)
└── .env                            # 机器级 env(自举安装)
```

### 网络模型

- 系统级安装，agent 侧一律用 **push** 模式接受入站邮件。gateway在外网的透传问题则由bridge解决。bridge支持push/pull双模式，根据网络环境进行选择。
- 地址级安装，agent 侧一律用 **pull** 模式拉取入站邮件。无需aimail cli和bridge介入参与。
- 是否需要 bridge，以及bridge选择什么模式与gateway对接，属于本机环境的一部分，在 bootstrap 时一次性判定和设置。

### 三份权威配置文件

| 文件                                                 | 内容                                                                                                                                                        | 写入方                                                                            |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `systems/{sid}/aimail_gateway.json`                | gateway\_url, admin\_key, system\_id, system\_name, manager\_address, system\_home, domain, webhook\_host(另含 save\_raw\_snapshots / default\_agent\_name) | `install`/`reset` → setup\_system.py;`repair` 只补缺 `system_home`/`webhook_host` |
| `systems/{sid}/{addr}/agentmail.json`              | 9 字段:email, gateway\_url, domain, system\_id, system\_name, manager\_address, api\_key, webhook\_url, webhook\_secret                                     | 注册链(register\_profiles/register\_agent/bind\_agent)                            |
| `bridge/aimail_bridge.toml` + `aimail_routes.toml` | pull 系统列表 + 路由表                                                                                                                                           | deploy\_bridge.py;`aimail bridge --system-id`                                  |

`aimail_gateway.json` 系统级配置项:

| 字段                   | 含义                                                                              |
| -------------------- | ------------------------------------------------------------------------------- |
| gateway\_url         | 网关地址;回环地址 = 本机直推,否则入站经 bridge                                                   |
| admin\_key           | 系统级凭据:安装时派生 agent\_admin 受限 key 落盘,原始 key 存 `.system_raw_key/{sid}_admin.key`   |
| system\_id           | 系统标识(SID)                                                                       |
| system\_name         | 系统名;共享域下是 agent 地址前缀的来源                                                         |
| manager\_address     | 系统默认安全员地址                                                                       |
| system\_home         | 平台根(如 `~/.hermes`)                                                              |
| domain               | 系统域名(独享裸域或共享域)                                                                  |
| webhook\_host        | 网关回调本机的三态开关:`IP:port` = 有 bridge/push;空串 = 有 bridge/pull;字段缺失 = 无 bridge,直连本机端点 |
| save\_raw\_snapshots | 是否落每封邮件的原始快照(默认 true)                                                           |
| default\_agent\_name | 默认主 agent 名(`aimail address -d` 写入)                                             |

`agentmail.json` 地址级配置项:

| 字段                        | 含义                        |
| ------------------------- | ------------------------- |
| email                     | agent 全地址               |
| gateway\_url              | 网关地址                      |
| domain                    | 地址所属域(= email 后缀)         |
| system\_id / system\_name | 所属系统id/系统标识名             |
| manager\_address          | 该地址的安全员                   |
| api\_key                  | 该地址的服务端 key(注册链签发)        |
| webhook\_url              | 本机入站端点(bridge 路由的唯一真源)    |
| webhook\_secret           | 入站签名密钥(网关签名 → agent 验签)   |

***

## 3. 系统安装

### 第 1 步 — 本机环境准备(bootstrap)

- 安装好自己的 aimail-gateway 服务，或去申请共享网关的服务。
- 然后，将系统admin-key/product\_code等相关信息设置环境变量，并执行AIMail的自举安装脚本，完成本地环境的初始化。例如：

```bash
export AIMAIL_URL=<你的网关地址>                # 自主独立安装的网关地址，如 https://mail.example.com
export AIMAIL_ADMIN_KEY=<admin key>           # 网关的管理key
export AIMAIL_DOMAIN=<你的域名>                # 独享域名,如 example.com
export AIMAIL_MANAGER_ADDRESS=you@example.com # 管理agent的默认安全员邮件地址，可每个agent不一样
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

- 程序副本(`~/.aimail/bin/aimail-src`,PATH 的 `aimail` 指向它)与宿主载荷(`~/.aimail/bin/mcp`)都归在 `~/.aimail/bin/` 程序根下,由 bootstrap 一并刷新(强制重下载:先 `export AIMAIL_FORCE_UPGRADE=1`)。

### 第 2 步 — `aimail install`(系统级,可重复,幂等)

```bash
aimail install --home <平台根>  --system-id <sid>
```

### 第 3 步 — 闭环验证

```bash
aimail check --system-id <sid>     # 全面体检(见 §4)
aimail ping --system-id <sid>      # ping → pong 闭环(权威判据 = agent 侧日志)
aimail welcome --system-id <sid>   # welcome 端到端(API 模式,noreply@{网关域} 发件)
```

***

## 4. 日常维护

### 4.1 `aimail stats` — 本机对接状态总览

```bash
aimail stats        # 默认视图:系统 + agent + 邮件统计 + 到期
aimail stats -a     # 全面视图:健康标注 + 断链系统 + 本机平台段
```

`-a` 逐系统健康:`home-ok/home-missing/home-dir-missing` ·
`pointer:…/pointer-none` · `cloud: ok/unlinked/broken-config/unreachable`。
断链纯事实判定(连接字段缺失 = broken-config;网关 403/404 = unlinked;
网络错误 = unreachable,不算断链)。平台段列出五个平台根的对接状态,尾部
给出维护链路提示。

### 4.2 `aimail check` — 全面配置和链路体检

维度顺序(用户定调):**配置文件 → 平台运行时资源 → agent 配置 → 链路探测**。

| 维度        | 层     | 检查项                                                                                                                                                                                                                        |
| --------- | ----- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 配置文件      | L0    | `aimail_gateway.json` 完备(gateway\_url/admin\_key/`system_home`/pointer)· `aimail_bridge.toml` 结构(mode、pull 条目、admin\_key 与 gateway.json 比对)· `agentmail.json` 九字段完备 + 内部一致(system\_id=sid、gateway\_url 同源、domain=email 后缀) |
| 网关/Bridge | L1/L2 | 网关 health + SMTP :25 + whoami scope;bridge 进程 + pull 路径 + 路由覆盖(每个 agent 的 email 必须有路由条目)                                                                                                                                   |
| 平台运行时资源   | L2r   | hermes:webhook.py `PREPROCESS_REGISTRY` + profiles.py `AimailGateway` 补丁标记、toolsets、skills、board/role\_prompt/common.md · openclaw:插件已装 + skills · deerflow:app.py `aimail_inbound` 双锚点 · pi:指针匹配                          |
| agent 配置  | L3    | 各平台适配器:name\&api\_key / webhook secret / skill / toolset / register                                                                                                                                                        |
| 链路        | L4    | 对真实入站端点探测——**404 = 路由未注册 = FAIL**;远端(非回环)目标本机不可探测 → PASS 附注,绝不误报 FAIL                                                                                                                                                      |

### 4.3 `aimail repair` — 配置和链路自动修复

```bash
aimail repair [--system-id <sid>] [--home <root>] [--deep] [--dry-run]
```

`--dry-run` 只打印计划。修复阶梯(每步幂等, 且每步都会打印自己的结论:
✓已修 / ✓无需修 / ⚠跳过+原因):

1. bridge 存活(死了则拉起)
2. 路由重刷(bridge --system-id)
3. 网关 webhook 配对修复
4. 网关配置回填(system_home/webhook_host, 只补缺、绝不覆盖)
5. 平台指针重建(仅当平台根确定且指针缺失)
6. 运行时资源重部署(只有带 SDK 安装入口的平台会自动重装, 其余打印 check 的修复提示;
   平台在远端时跳过并给提示)
7. 运行时载荷刷新(mcp 载荷缺失/陈旧 → 用本机 bundle 幂等重装, 不需要网络)
8. agentmail.json 补缺 + webhook_url 对齐存活路由(仅本机)
9. routes 条目补齐
10. bridge pull 条目: 缺失则按 gateway.json 本地创建(aimail_url/admin_key/system_id),
    已存在则把 admin_key 对齐 gateway.json(权威源)

修复过程有两种类型, 明确收口:

- 可自修(auto): 确定性、只依赖本机、不看服务端/宿主状态。阶梯必须覆盖它;
  修完前提满足却仍 FAIL ⇒ 缺陷(输出 [D 本机可修·仍未修], 退出码 1), 请连同日志反馈维护者。
- 仅提示(hint): 不可靠自修(需要网关/agent 进程、服务端注册或管理员参与)。
  repair 只打印原因与建议动作, 不硬试; 残留 [H 需管理员/宿主] 属正常。

复检结尾固定输出「本机可修缺陷 <n> 项 / 需管理员介入 <m> 项」。未登记的维度按 hint 处理
并给原因。

`--deep` 额外执行 webhook 配对重写与 stuck pending 清理。

### 4.4 日常操作

| 动作        | 命令                                                               | 说明                                                  |
| --------- | ---------------------------------------------------------------- | --------------------------------------------------- |
| 添加域名      | `aimail domain -s <sid> -a example.com`                          | **CLI 是唯一入口**(SPA 添加按钮已收敛);输入小写归一,服务端配额 + UNIQUE 兜底 |
| 查看域名      | `aimail domain -s <sid>`                                         | 非共享系统可持多个裸域;任一个裸域都可承载续期码领取                          |
| 续期        | `aimail renew -s <sid> -c <码>`                                   | 叠加式 `max(now,当前)+validity`,配额 max 合并,自动解除挂起         |
| 到期查看      | `aimail renew -s <sid> --status`                                 | 只读,不耗码                                              |
| Agent 地址  | `aimail address -s <sid> [-d 默认名\|-a agent -n 新地址名\|-m manager]` | 查看/设默认主 agent 名/地址改名(set-name,服务端资源全继承)/设 manager   |
| 重置配置      | `aimail reset -H <root> -s <sid>`                                | 只走 admin-key 路径,key 不动                              |
| bridge 维护 | `aimail bridge` / `--restart` / `-s <sid>`                       | 状态 / 单实例重启 / 重刷路由                                   |
| 卸载        | `aimail uninstall -s <sid> [-H <root>] [-y]`                     | 网关注销 → 平台清理 → 本机数据;幂等                               |
| 端到端       | `aimail ping` / `welcome` / `persona`                            | 心跳 / welcome / persona 闭环                           |

短参数全局一致:`-s` system-id · `-H` home · `-g` gateway-url · `-m`
manager · `-c` code · `-n` system-name(install/reset)或 dry-run(repair)
· `-d` domain(install)或 default(address)· `-w` no-wait
(welcome/persona)(或 domain 的 `--webhook-url`)· `-a` all(stats)或
add(domain)· `-t` status(renew)
或 timeout(ping)· `-D` deep · `-r` restart · `-k` admin-key · `-y` yes。
长参数永不改名。

***

## 5. 命令速查

子命令按场景分组(`aimail --help` 即此布局):

```
setup      install  ensure-system  uninstall  reset
operate    stats  renew  version
diagnose   check  repair  ping  welcome  persona
resources  domain  address  bridge
```

平台特征探测顺序:`pi`(\~/.pi + agent/)→ `dsh`(\~/.dsh + profiles/ +
storages/)→ `hermes`(hermes-agent/ 或 profiles/)→ `openclaw`
(openclaw\.json)→ `deerflow`(backend/app/gateway/)→ `unknown`。
`--system-id` + 已存 `system_home` 反查优先于自动探测;指针归属为次。

日志:bridge → `~/.aimail/logs/aimail-bridge.log`;每 agent →
`~/.aimail/logs/aimail.{addr}.log`(JSON 行;`dir` = ping\_intercepted /
pong\_sent / pong\_returned)。邮件方向 `inbound`/`outbound` 记在本地邮件 meta 里,
不是这个日志。无自动轮转——需要时用
logrotate。

***

## 6. 故障排查

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

**修复:** openclaw:`openclaw plugins install openclaw-aimail --force --accept-capabilities` + 重启网关(离线/本地包改用 `npm-pack:<tgz> --force`);hermes:重跑 SDK 安装
(`python -m aimail.install install --type hermes --home ~/.hermes`)+ 重启
profile 网关;然后 `aimail repair --system-id <sid>`。

### check FAIL `routes-entry` / `routes-target`

**原因:** bridge 路由表缺该 agent(pull 模式无法投递)或路由目标与声明
webhook 不一致。

**修复:** `aimail repair --system-id <sid>`(阶梯 2/8/9:重刷路由、webhook\_url
对齐存活目标、routes 条目补齐)。目标主机是远端(pi/deerflow 在别机)→ 到该机启动其入站。

### ping 卡在 "pong not returned"

查每 agent 日志三阶段:`grep <ping_id> ~/.aimail/logs/aimail.{addr}.log`;
核对 `agentmail.json` 的 email 与 api\_key;`aimail reset -H <平台根> -s <sid>` 重新固化。

### bridge 拉不到邮件

`aimail bridge`(进程/配置/路由)→ `curl https://aimail.token.tm/health` →
`tail -20 ~/.aimail/logs/aimail-bridge.log` → `aimail repair -s <sid>`。

### 重复 install 出问题了?

不可能:激活服务端原子、配置写入合并/存在性检查、bridge key 复用。若中途
失败,`aimail check` + `aimail repair` 恢复不变量状态。

***

