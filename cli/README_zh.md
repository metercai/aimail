[English](README.md) | 🇨🇳 中文

# aimail CLI

> 适用对象:`aimail` ——在Agent所在机器上安装与维护 AIMail的命令行工具。运维的范围包括：本机的aimail基础环境的建立、Agent系统实例的aimail对接和配置，以及某个具体Agent的aimail相关配置。

***

## 1. 目标与范围

### CLI 是什么

`aimail` 是 AIMail 体系在**本机的基础运维工具**——只在本机执行,管理运维本机的aimail相关资源，不做远程运维;是agent与aimail对接(激活、域名、绑定、路由)的**唯一写入路径**,保证本机状态健康，与网关状态同步一致。

### 三层运维对象

| 层       | 工具                        | 对象标识            | 职责                                |
| ------- | ------------------------- | --------------- | --------------------------------- |
| 本机基础环境  | bootstrap.sh/bridge命令     | 宿主机系统           | 主目录/网关判定/bridge 就位                |
| Agent平台 | `install/check/repair等命令` | Agent平台的根目录/SID | 激活/复用系统、绑定平台、绑定资源导入、合并 bridge 条目等 |
| Agent   | `address 等命令`             | Agent 标识/地址     | 查看/地址改名/设安全员(manager)     |

### 平台安装对接

平台适配全部由 SDK 承载。CLI 侧每个宿主一份声明(`cli/src/hosts/<name>.rs`:落点、识别特征、注册入口、安装/卸载步骤、体检项);新增平台 = 新增一个宿主文件 + 在 `hosts/mod.rs` 的 `ALL` 里加一行 + SDK 提供适配,命令层零改动。各平台的安装动作、注册器与重启要求:

| 平台       | 平台根            | 运行时           | 安装动作                                                                                                                                                                        | 重启要求                                     |
| -------- | -------------- | ------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------- |
| Hermes   | `~/.hermes`    | Python(pysdk) | 在平台自己的解释器里装 `aimailsdk`（导入名 `aimail`;解释器由 CLI 在平台根内探测,uv → pip → ensurepip);SDK 安装展开 SKILL/toolsets/board 资源并给 webhook.py 注入 `PREPROCESS_REGISTRY`;注册本根的 agent 集合（SDK 安装入口即注册全部 profile） | 重启 hermes gateway                        |
| DeerFlow | `~/.deer-flow` | Python(pysdk) | SDK 安装 → `install-skill.sh` + `install-mcp.sh` → 注册(`manage.py register --all`)                                                                                             | 重启 8001(上游仓由补丁安装)                        |
| OpenClaw | `~/.openclaw`  | TS(tssdk)     | `openclaw plugins install openclaw-aimail --force --accept-capabilities` → 注册(`openclaw aimail register`;全量 `register-all`)                                                 | 重启 openclaw gateway                      |
| DSH      | `~/.dsh`       | TS(tssdk)     | `dsh plugin --profile web add dsh-aimail`(需先有 `dsh` CLI;仅在 npm 设了最小发布年龄限制的环境(如受限镜像)追加 `--config.minimumReleaseAge=0`)                                                                                                                    | 绑定由 dsh session(mail preset)自动 auto-bind |
| Pi       | `~/.pi`        | TS(tssdk)     | `pi install npm:pi-aimail`                                                                                                                                                  | 重启 pi                                    |

通用命令:

```bash
aimail install --home <平台根>                    # 注册主 agent(python 宿主由 SDK 安装入口全量注册);该 home 有指针时自动复用系统
aimail install --home <平台根> --all-agents        # 平台根下每个 agent 都注册(hermes/openclaw/deerflow)
aimail install --home <平台根> -a <agent> [<agent2>]  # 只注册这些 agent(名字=宿主内部 agent 名;对不上的忽略)
aimail install -s <sid>                            # 由 sid 反查本机已配置的平台根
aimail install --home <平台根> --system-id <sid>   # 显式绑定:指针缺失 / 多系统歧义 / 换平台根
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
│   ├── aimail_gateway.json              # 系统级配置(网关/域名/安全员/system_home)
│   ├── .system_raw_key.key              # 原始系统级 key(仅集成时落盘,0600)
│   ├── board/role_prompt/common.md      # 系统级 A2A 角色 prompt(兜底)
│   └── {agent_addr}/                    # 一地址一层:配置/日志/邮件/角色都在这里
│       ├── agentmail.json               # 九字段绑定
│       ├── agentmail.log                # 该地址的处理日志(JSON 行;ping 三阶段在此)
│       ├── board_creds.json             # 该地址的 board 凭据
│       ├── role_prompt/                 # 地址级角色 prompt(优先于系统级)
│       └── mail/{yyyymm}/               # in-*.json / out-*.json / attch/;meta、threads、.search 同叶
├── bridge/
│   ├── aimail_bridge.toml               # pull 系统列表
│   ├── aimail_routes.toml               # 路由表:email → 本机入站端点
│   ├── aimail-bridge.log                # bridge 运行日志
│   ├── aimail-bridge                    # bridge 二进制
│   └── bridge.pid
├── bin/                                 # 程序根
│   └── aimail                           # CLI 二进制(~/.local/bin/aimail 指向它)
└── .env                                 # 机器级 env(自举安装落盘)
```

按地址隔离:邮件快照、处理日志、绑定、角色 prompt、board 凭据都在 `systems/{sid}/{addr}/`
一层之内,删一个地址目录不会碰到别的地址;sid 解析不出来时收口到 `systems/_unassigned/`。
宿主侧 python 运行时一律取**已安装的 `aimailsdk`**(site-packages),不再复制到程序根。

### 网络模型

- 系统级安装，agent 侧一律用 **push** 模式接受入站邮件。gateway在外网的透传问题则由bridge解决。bridge支持push/pull双模式，根据网络环境进行选择。
- 地址级安装，agent 侧一律用 **pull** 模式拉取入站邮件。无需aimail cli和bridge介入参与。
- 是否需要 bridge，以及bridge选择什么模式与gateway对接，属于本机环境的一部分，在 bootstrap 时一次性判定和设置。

### 三份权威配置文件

| 文件                                                 | 内容                                                                                                                                                        | 写入方                                                                            |
| -------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------ |
| `systems/{sid}/aimail_gateway.json`                | gateway\_url, admin\_key, system\_id, system\_name, manager\_address, system\_home, domain, webhook\_host(另含 save\_raw\_snapshots) | `install` / `reset`;`repair` 只补缺 `system_home`/`webhook_host` |
| `systems/{sid}/{addr}/agentmail.json`              | 9 字段:email, gateway\_url, domain, system\_id, system\_name, manager\_address, api\_key, webhook\_url, webhook\_secret                                     | 注册链(经宿主注册入口/SDK 门)                            |
| `bridge/aimail_bridge.toml` + `aimail_routes.toml` | pull 系统列表 + 路由表                                                                                                                                           | `aimail install`(部署);`aimail bridge --system-id`(重刷)                                  |

`aimail_gateway.json` 系统级配置项:

| 字段                   | 含义                                                                                                                    |
| -------------------- | --------------------------------------------------------------------------------------------------------------------- |
| gateway\_url         | 网关地址;回环地址 = 本机直推,否则入站经 bridge                                                                                         |
| admin\_key           | 系统级凭据:安装时派生受限的 domain 级 key(category `domain`、身份=本系统裸域、scope `system`)落盘,原始系统 key 存 `systems/{sid}/.system_raw_key.key` |
| system\_id           | 系统标识(SID)                                                                                                             |
| system\_name         | 系统名;共享域下是 agent 地址前缀的来源                                                                                               |
| manager\_address     | 系统默认安全员地址                                                                                                             |
| system\_home         | 平台根(如 `~/.hermes`)                                                                                                    |
| domain               | 系统域名(独享裸域或共享域)                                                                                                        |
| webhook\_host        | 网关回调本机的三态开关:`IP:port` = 有 bridge/push;空串 = 有 bridge/pull;字段缺失 = 无 bridge,直连本机端点                                       |
| save\_raw\_snapshots | 是否落每封邮件的原始快照(默认 true)                                                                                                 |

`agentmail.json` 地址级配置项:

| 字段                        | 含义                      |
| ------------------------- | ----------------------- |
| email                     | agent 全地址               |
| gateway\_url              | 网关地址                    |
| domain                    | 地址所属域(= email 后缀)       |
| system\_id / system\_name | 所属系统id/系统标识名            |
| manager\_address          | 该地址的安全员                 |
| api\_key                  | 该地址的服务端 key(注册链签发)      |
| webhook\_url              | 本机入站端点(bridge 路由的唯一真源)  |
| webhook\_secret           | 入站签名密钥(网关签名 → agent 验签) |

***

## 3. 系统安装

### 第 1 步 — 本机环境准备(bootstrap)

- 安装好自己的 aimail-gateway 服务，或去申请共享网关的服务。
- 然后，将系统admin-key/product\_code等相关信息设置环境变量，并执行AIMail的自举安装脚本，完成本地环境的初始化。例如：

```bash
export AIMAIL_GW_URL=<你的网关地址>                # 自主独立安装的网关地址，如 https://mail.example.com
export AIMAIL_ADMIN_KEY=<系统级 key>          # 系统级 key(不是网关自身的 admin key)
export AIMAIL_DOMAIN=<你的域名>                # 独享域名,如 example.com
export AIMAIL_MANAGER_ADDRESS=you@example.com # 管理agent的默认安全员邮件地址，可每个agent不一样
#export AIMAIL_BRIDGE_HOST=<domain/ip:port>        # 指定桥接地址，用于Gateway的Push的透明转发，可选
#export AIMAIL_AGENT_HOST=<domain/ip:port>         # 指定本机Agent地址，用于直接接收Gateway的Push，可选
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

- `AIMAIL_ADMIN_KEY`(或 `aimail install -k`)取的是**系统级 key**:自建网关用启动时打印的 `<storage>/<system id>.system.key`,云端用激活码签发的 key。
- CLI 二进制(`~/.aimail/bin/aimail`,`~/.local/bin` 上有链接)装在 `~/.aimail/bin` 程序根下,自举时幂等刷新(强制重取先 `export AIMAIL_FORCE_UPGRADE=1`)。

### 第 2 步 — `aimail install`(系统级,可重复,幂等)

```bash
export AGENT_HOME=~/.hermes   # Agent的主目录，亦可 ~/.dsh、~/.openclaw、~/.pi、~/.deer-flow
aimail install  
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
| 平台运行时资源   | L2r   | hermes:webhook.py `PREPROCESS_REGISTRY` 补丁 / profiles.py `AimailGateway` 补丁 / skills(含 board/role\_prompt/common.md)/ toolsets · openclaw:插件已装 / skills · deerflow:app.py `aimail_inbound` 补丁 · pi:指针匹配 · dsh:无本地项(绑定由 dsh 会话自建)                          |
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
4. 网关配置回填(system\_home/webhook\_host, 只补缺、绝不覆盖)
5. 平台指针重建(仅当平台根确定且指针缺失)
6. 运行时资源重部署(只有带 SDK 安装入口的平台会自动重装, 其余打印 check 的修复提示;
   平台在远端时跳过并给提示)
7. agentmail.json 补缺 + webhook\_url 对齐存活路由(仅本机)
8. routes 条目补齐
9. bridge pull 条目: 缺失则按 gateway.json 本地创建(aimail\_url/admin\_key/system\_id),
    已存在则把 admin\_key 对齐 gateway.json(权威源)

修复过程有两种类型, 明确收口:

- 可自修(auto): 确定性、只依赖本机、不看服务端/宿主状态。阶梯必须覆盖它;
  修完前提满足却仍 FAIL ⇒ 缺陷(输出 \[D 本机可修·仍未修], 退出码 1), 请连同日志反馈维护者。
- 仅提示(hint): 不可靠自修(需要网关/agent 进程、服务端注册或管理员参与)。
  repair 只打印原因与建议动作, 不硬试; 残留 \[H 需管理员/宿主] 属正常。

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
| Agent 地址  | `aimail address -s <sid> [-a agent -n 新地址名\|-m manager]`      | 查看/地址改名(set-name,服务端资源全继承)/设 manager        |
| 重置配置      | `aimail reset -H <root> -s <sid>`                                | 只走 admin-key 路径,key 不动                              |
| bridge 维护 | `aimail bridge` / `--restart` / `-s <sid>`                       | 状态 / 单实例重启 / 重刷路由                                   |
| 卸载        | `aimail uninstall -s <sid> [-H <root>] [-y]`                     | 网关注销 → 平台清理 → 本机数据;幂等                               |
| 端到端       | `aimail ping` / `welcome`                                        | 心跳 / welcome 闭环(persona 已并入 welcome)             |

短参数全局一致:`-s` system-id · `-H` home · `-g` gateway-url · `-m`
manager · `-c` code · `-n` system-name(install/reset)/新地址名(address)/
dry-run(repair)· `-d` domain(install)· `-w` no-wait
(welcome/persona)(或 domain 的 `--webhook-url`)· `-a` all(stats)/
add(domain)/agent 名(address)· `-t` status(renew)或 timeout(ping)·
`-D` deep · `-r` restart · `-k` admin-key · `-y` yes。长参数永不改名。

***

## 5. 命令速查

子命令按场景分组(`aimail --help` 即此布局),每条附一句话说明:

```
setup      机器准备与平台对接
  install         对接平台并激活或复用系统(激活码 / admin key)
  uninstall       从平台移除对接(幂等)
  reset           重设已激活系统的连接配置(不重新激活)

operate    日常状态与生命周期,以及体检与流程
  stats           本机对接状态总览(系统 / 地址 / 邮件量,含健康标注)
  renew           系统续期(续期码),或只读查看到期状态
  version         显示 CLI / 自举版本(判断命令面来自哪份副本)
  check           全面体检:配置文件 → 运行时资源 → 链路探测
  repair          按体检结论执行幂等修复阶梯并复检
  ping            端到端 ping-pong 投递测试(manager ↔ agent)
  welcome         welcome 邮件端到端验收(默认走 API)
  persona         已并入 welcome(仅指针,退出码 2)

resources  系统级资源
  domain          列出 / 新增系统自有域名
  address         查看 / 维护系统内 agent 地址(改名 / 设 manager)
  prompt          维护 agent 提示规则:subject/body/sender/recipient → 角色文件
  bridge          本机桥:状态 / 重刷路由 / 重启 / 升级
  platform        导出宿主静态知识表(+ 给定 home 的探测结果;门禁与夹具的唯一入口)
```

平台判定顺序:`pi`(`~/.pi` 且含 `agent/`)→ `dsh`(`~/.dsh` 且含
`profiles/`)→ `hermes`(`hermes-agent`)→ `openclaw`(`openclaw.json`)→
`deerflow`(`backend/app/gateway/`)→ `unknown`(响亮失败,不猜)。
pi/dsh 的目录名必须精确匹配,其余只看特征。`aimail platform spec --home <R>`
另有一档"入口优先"判定(已装插件入口在位即认该宿主);`repair` 用宽松档
(hermes 额外认 `profiles/`)。`--system-id` + 已存 `system_home` 反查优先于
自动探测;指针归属为次。

日志:bridge → `~/.aimail/bridge/aimail-bridge.log`;每 agent →
`~/.aimail/systems/{sid}/{addr}/agentmail.log`(JSON 行;`dir` = ping\_intercepted /
pong\_sent / pong\_returned)。邮件方向 `inbound`/`outbound` 记在 `mail/` 下的 meta 里,
不是这个日志。无自动轮转——需要时用 logrotate。

***

