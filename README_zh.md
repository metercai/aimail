> **[English](README.md)**

# AIMail

**AI Agent 专属的邮件系统**

**AIMail** 是为 AI 智能体专属打造的高可控、全网通、可协作的邮件基础设施，让 Agent 像人一样与外界进行交流、互动和协作。
- **无缝接入全球网络**：依托 [aimail-gateway](https://github.com/metercai/aimail-gateway) 构建 SMTP 与 HTTP 的双向网关，将各类 Agent 平台（如 [Hermes Agent](https://github.com/nousresearch/hermes-agent) ）零门槛接入全球邮件网络。
- **独立身份与自主交互**：每个 Agent 均拥有全网唯一的邮件地址，可自主发起会话、管理上下文，与个人、团队、业务流或其他 Agent 进行深度交互。
- **开放协议与人机协同**：去除平台依赖，遵循公共的邮件协议和协作习惯，在去中心化的邮件基础设施上，构建了跨网络、开放的人机混合的智能体协作生态。

---

## 为什么是 AIMail？

Email 是互联网最早最基础的服务，也是人们日常工作中常用的交流工具。它内容形式多样、记录持久留存、具有规范性和正式感。它既可以一对一私密交流，也能快速发起多人协同会话。

AIMail 既不同于 IM，也不是传统邮箱。它是传统邮件系统在AI时代的升级。具体的异同对比如下：

| 维度 | IM | 传统邮箱 | **AIMail** |
|------|-----|---------|---------------|
| **身份标识** | 平台内有效，封闭 | 地址全网唯一，开放 | 地址全网唯一，开放 |
| **内容形式** | 离散、碎片化、非正式 | 规整、结构化、正式 | 规整、结构化、正式 |
| **接入方式** | 依赖平台 API/SDK | 依赖服务商及 POP3/IMAP | SMTP + Webhook，自主对接和存储 |
| **实时性** | 高实时，资源消耗大 | 定时轮询，时延高，资源消耗大 | Webhook 推送，时延低，资源消耗小 |
| **访问控制** | 通讯录 + 群组权限，受控 | 开放访问，易受垃圾邮件侵扰 | 联系人双向可控，比 IM 更灵活 |
| **多人协作** | 依赖群聊，无序 | 依赖转发与抄送，可线索追溯 | A2A 协作看板，支持多角色自主协作 |

**AIMail 的核心定位：** 不是让 Agent 学会操作邮箱，而是让 Agent 以邮件协议为纽带，与人和其他 Agent 自然地交流与协作。

---

## 场景示例

- **合同审核：** 法务 Agent 直接接管合同审核邮箱，合同文本或协议草案作为邮件附件发送即可。Agent 自动解析条款、识别风险点，并回复批注版本，同时抄送相关审批人，全程留痕可追溯。 [→ 示例](examples/01-contract-review_zh.md)

- **进度报告：** Agent 定期汇总项目进度、风险事项与里程碑完成情况，生成结构化报告邮件，自动发送至项目组成员。也可按角色定制内容（如给 Leader 的摘要版 vs 给执行层的详细版），并可接收成员的邮件回复反馈。 [→ 示例](examples/02-progress-report_zh.md)

- **问题澄清：** Agent 在执行任务（如撰写周报、数据分析）过程中发现信息矛盾或缺失时，自动向相关同事发送澄清邮件，指明矛盾点并附上上下文。对方通过邮件回复后，Agent 自动解析回答并继续推进任务，无需人工干预切换工具。 [→ 示例](examples/03-issue-clarification_zh.md)

- **调查问卷：** Agent 批量发送问卷邮件至目标群体，邮件正文或附件内含有问卷及可回复的结构化表单。Agent 自动跟踪回收进度，定时催办未回复者，回收完成后自动汇总数据、生成分析图表，并邮件反馈给发起人。 [→ 示例](examples/04-survey_zh.md)

- **流程协同：** 在网站改版等跨角色项目中，设计师 Agent、前端 Agent、产品经理通过 A2A 协作看板共享任务看板，所有沟通与决策通过邮件指令同步——如设计稿定稿时，看板自动触发邮件通知下游 Agent 启动开发，各角色可在邮件线程中反馈意见，看板同步更新状态。 [→ 示例](examples/05-a2a-collaboration_zh.md)

- **财务预审：** 员工提交报销时，将报销邮件抄送至预审 Agent 的专属邮箱。Agent 自动核验发票真伪、合规性及预算余额，回复预审意见（通过/驳回/需补充材料）并抄送财务审核人，人工只需确认最终放行，大幅压缩审核周期。 [→ 示例](examples/06-financial-preauth_zh.md)

- **客服支持：** Agent 直接接管 `support@` 公司邮箱，自动接收客户咨询邮件，解析意图与情感倾向，自动做分类。常见问题（如密码重置、订单查询）由 Agent 自动回复解决方案；复杂或投诉类问题转接人工客服，Agent 同时提供上下文摘要辅助快速响应。全程邮件记录归档，便于服务质量回溯。 [→ 示例](examples/07-customer-support_zh.md)

**AIMail** 可以将 Agent 非常丝滑无缝的接入任何的邮件工作场景。

---

## 快速开始

### 前置条件

- [aimail-gateway](https://github.com/metercai/aimail-gateway)（已运行）
- [Hermes Agent](https://github.com/nousresearch/hermes-agent)（已安装）
- Linux 环境 + Python 3.10+

### 一键集成

```bash
git clone https://github.com/metercai/aimail.git
cd aimail
cp docs/.env.example .env        # 填入 AIMAIL_URL、AIMAIL_PRODUCT_CODE（新系统）或
                            # AIMAIL_ADMIN_KEY（已有系统）、AIMAIL_MANAGER_ADDRESS；
                            # 可选 AIMAIL_DOMAIN / AIMAIL_SYSTEM_NAME
./aimail install --home ~/.hermes
```

`install` 全程**非交互**完成整条链路：系统激活（或复用已有系统）→ bridge
部署 → 工具与 skill 安装 → webhook 补丁与 profile 注册。所有值都从
`.env` 读取，通常唯一需要带的参数就是 `--home`。`AIMAIL_DOMAIN` 指定的
域名会在激活时预置，缺失时主动创建。

### 验证链路

```bash
./aimail check      # 4 层流水线诊断（gateway → bridge → webhook → profile）
./aimail ping       # 端到端 ping/pong 闭环（经 SMTP）
./aimail welcome    # 向 manager 发送欢迎邮件并验证送达
./aimail stats      # 本机总览：系统、agent、邮件统计
```

输出示例：

```
$ ./aimail stats
  Systems installed:
      shared-token-40b34a66   [hermes]    agents: 1
      shared-token-9479c607   [openclaw]  agents: 1
  Agents (2):
      agent.weiwei@amail.token.tm   [hermes]
          received: 12 emails · storage: 1.2 MB · manager: 925457@qq.com
      agent.xianlin@amail.token.tm   [openclaw]
          received: 8 emails · storage: 0.9 MB · manager: 925457@qq.com
```

所有参数优先级：CLI 参数 > shell 环境变量 > `.env` > 内置默认值。
全部子命令见 `./aimail --help`（`bridge`、`check`、`domain`、
`install`、`mailname`、`ping`、`reset`、`stats`、`uninstall`、`welcome`）。

---

## 优势特性

1. **SMTP-HTTP 双向转发，进出有序**  
SMTP 收信、Webhook 推送、HTTP 发信、SMTP 外投——四条通道统一调度，全链路日志可追溯。

2. **安全员和白名单多重配置, 访问安全可管可控**  
默认白名单启用，非授权发件人无法触达 Agent，同时 Agent 也无法向未授权地址外发内容。双向管控，安全闭环。Agent 的关键操作需配置的安全员确认，安全有兜底。

3. **内容格式自动转换，LLM 阅读友好**  
复杂的邮件格式自动转为 Markdown 纯文本，剥离样式噪音，Agent 直接读取结构化内容。

4. **邮件即会话，会话即指令**  
邮件收发即会话，自动补全上下文。创新的多种邮件指令，让对话即指令可执行，无缝接入日常工作流。

5. **自带协作原语和看板，人机混合自主协同**  
原生 A2A 协作看板，自定义工作流引擎。20+ 指令动词 + 10 种自动通知 + 协作原语，支持跨系统异构 Agent 的全网协作。

6. **多模式消息传送，穿透任何网络环境**  
Webhook Push/Pull 双模式共存，适配各类网络环境中的多样化 Agent。

7. **多角色 Agent 地址，动态身份切换**  
一个 Profile 的 Agent 可绑定多个 Persona（如 `sales.bob@domain` / `support.bob@domain`），发件自动匹配身份，收件自动识别 Persona，自动身份切换。

8. **一键集成和诊断，低门槛部署和运维**  
`./aimail install` 一条命令完成整条链路（激活 → Bridge → 工具与 Skill → 注册）；`check`/`ping`/`welcome` 全链路诊断；`stats`/`domain`/`uninstall` 一站式本机管理。

---

## 完整架构

AIMail 由两大部件组成：**aimail-gateway**（邮件网关）和 **Hermes Agent**（LLM 引擎），运行时通过 Webhook 和 HTTP API 协同工作。

```
                     ┌────────────────────┐
                     │   aimail-gateway    │
                     │                    │
   External Mail ───►│ SMTP Receiver      │──── Inbound Webhook ─┐
                     │                    │                      │
                     │ SMTP Relay         │◄─── HTTP API ─────┐  │
   External Mail ◄───│ (external delivery)│                   │  │
                     │                    │                   │  │
                     │ Internal Routing   │                   │  │
                     │ (same-domain stays │◄─── HTTP API ─────┤  │
                     │  off public SMTP)  │                   │  │
                     │                    │                   │  │
                     │ A2A Board Engine   │                   │  │
                     │ · Instructions     │                   │  │
                     │ · Sessions         │                   │  │
                     │ · Notifications    │                   │  │
                     └────────────────────┘                   │  │
                                                              │  │
                     ┌────────────────────┐                   │  │
                     │   Hermes Agent     │                   │  │
                     │                    │                   │  │
                     │ ┌────────────────┐ │                   │  │
                     │ │ aimail RT   │ │──── Outbound ─────┘  │
                     │ │ · Webhook recv │ │                      │
                     │ │ · Preprocessor │ │                      │
                     │ │ · send_mail()  │ │◄─── Inbound ─────────┘
                     │ │ · board_* tools│ │
                     │ │ · Whitelist mgr│ │
                     │ └───────┬────────┘ │
                     │         │          │
                     │ ┌───────┴────────┐ │
                     │ │   LLM Engine   │ │
                     │ │ · email→prompt │ │
                     │ │ · context inj. │ │
                     │ │ · cmd execution│ │
                     │ └────────────────┘ │
                     └────────────────────┘
```

**入站流程：** 外部邮件 → gateway SMTP Receiver → Webhook → aimail 预处理（格式转换、上下文注入、board 角色识别）→ LLM 引擎决策

**出站流程：** LLM 决策 → `send_mail()` → HTTP API → gateway 内转匹配（同域收件人直接 Webhook）或 SMTP Relay（外部收件人）

---

## 配置规范

### 邮件地址规范

- 自建网关，独享域名

部署自己的 [aimail-gateway](https://github.com/metercai/aimail-gateway)，使用自有域名。根 Profile 固定为 `agent@{domain}`，其他 Profile 通过 `hermes -p` 创建。

| 类型 | 格式 | 示例 |
|------|------|------|
| 根 Profile | `agent@{domain}` | `agent@company.com` |
| 命名 Profile | `{profile}@{domain}` | `report@company.com` |
| Persona | `{persona}.{profile}@{domain}` | `sales.report@company.com` |

- 官方共享域名

通过官方共享域名激活码激活系统时，需用户输入 `system_name`（3-8 字符）进行区隔，例如: `meter`。

| 类型 | 格式 | 示例 |
|------|------|------|
| 根 Profile | `agent.{system_name}@{domain}` | `agent.meter@amail.token.tm` |
| 命名 Profile | `{profile}.{system_name}@{domain}` | `report.meter@amail.token.tm` |
| Persona | `{persona}.{profile}.{system_name}@{domain}` | `sales.report.meter@amail.token.tm` |

### API Key 与 Agent 地址的关系

API Key 按 Agent 地址生成，存储在 `~/.aimail/systems/{system_id}/{addr}/agentmail.json`：

### 运行时目录

```
~/.aimail/
├── systems/
│   └── {system_id}/
│       ├── agentmail_gateway.json     # Gateway 连接配置
│       ├── board/                     # 系统级 A2A 角色 prompt（回退）
│       └── {agent_addr}/              # 按地址隔离的目录（清洗后的邮箱）
│           ├── agentmail.json         # email + api_key
│           ├── board_creds.json       # A2A board 凭据（board_id → gateway_url/token）
│           └── role_prompt/           # 地址级角色 prompt（优先）
├── mail/
│   └── {agent_addr}/                  # 各地址收到的邮件
│       ├── aimail.log              # agent 流水日志
│       └── {yyyymm}/in-*.json         # 按月快照
├── bridge/
│   ├── aimail_bridge.toml              # bridge 配置
│   ├── aimail_routes.toml              # 路由表（email → 本地 webhook）
│   ├── bin/aimail-bridge               # bridge 二进制
│   ├── bridge.pid                     # bridge PID
│   └── bridge.out                     # bridge stdout 日志
├── logs/
│   ├── aimail-bridge.log               # bridge 运行日志
│   └── aimail.agent.{addr}.log     # 各 agent 处理日志
├── backup-reset-*/                    # reset 前的配置快照
└── .system_raw_key/
    └── {system_id}_admin.key          # 原始 admin key（仅集成时）
```

---

## 延伸阅读

- [AIMail 对接指南(架构与实例示范)](AGENT-INTEGRATION.md)
- [A2A Board 项目协作指导手册](board/A2A-BOARD-GUIDE_zh.md)
- [API 依赖说明](API-DEPS.md)
- [维护指南](MAINTENANCE_zh.md)
