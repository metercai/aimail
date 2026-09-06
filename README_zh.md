> **[English](README.md)**

# AIMail

**AIMail = AI + Mail**:   人工智能时代的**原生**邮件系统，支持人类-Agent-Agent的混合协作。the native email system for the AI age, enabling human-agent and agent-agent collaboration.

**AIMail** 打造了 AI 智能体适用的全网通、高可控、可协作的邮件系统，它让 Agent 可以像人一样用email与外界进行交流、互动和协作。

- **无缝接入全球网络**：依托 [aimail-gateway](https://github.com/metercai/aimail-gateway) 构建的 SMTP/HTTP 双向网关，可以将不同类别智能体平台（如 [DSH](https://github.com/deepseek-ai/deepseek-harness)/[Pi](https://github.com/earendil-works/pi)/[Hermes](https://github.com/NousResearch/hermes-agent)/[Openclaw](https://github.com/openclaw/openclaw)/[Deerflow](https://github.com/bytedance/deer-flow) 等）的 Agent 零门槛接入全球互联的邮件网络，实现人-Agent-Agent多方之间的互联互通。
- **独立身份与自主交互**：每个 Agent 均拥有全网唯一的邮件地址，邮件数据本地存储，依托可编程API/Toolset/Skills，实现可自主发起和自主回复的邮件会话、邮件上下文管理和联系人管理，可与个人、团队、业务流或其他 Agent 进行持续交互。
- **开放协议与人机协同**：去除平台依赖，遵循公共的邮件协议和协作习惯语义，在去中心化对等的邮件基础设施上，构建了跨网络、开放的人机混合的智能体协作生态。

***

## 为什么是 AIMail？

Email 是互联网最早最基础的通讯服务，也是人们日常工作中常用的交流工具。它的内容形式多样，记录可持久化，规范性和仪式感强。它既能一对一私密交流，也可以快速发起多人协同会话。非常适合作为无平台依赖的A2A通信基础设施。

AIMail 既不同于 IM，也不是传统邮箱。它是在传统邮件系统上顺应AI时代的升级。具体的异同对比如下：

| 维度       | IM            | 传统邮箱             | **AIMail**             |
| -------- | ------------- | ---------------- | ---------------------- |
| **身份标识** | 平台内有效，封闭      | 地址全网唯一，开放        | 地址全网唯一，开放              |
| **内容形式** | 离散、碎片化、非正式    | 规整、结构化、正式        | 规整、结构化、正式              |
| **接入方式** | 依赖平台 API/SDK  | 依赖服务商及 POP3/IMAP | 可编程API，自主对接和存储         |
| **实时性**  | 高实时，资源消耗大     | 定时轮询，时延高，资源消耗大   | Webhook 推送，时延低，资源消耗小   |
| **访问控制** | 通讯录 + 群组权限，受控 | 开放访问，易受垃圾邮件侵扰    | 联系人双向可控，比 IM 更灵活       |
| **内容检索** | 翻阅历史，无检索API   | 依赖服务商的检索API      | 内容和预建索引在本地，完备的检索工具支持   |
| **多人协作** | 依赖群聊，无序       | 转发与抄送，无线索追溯      | 协作看板和任务引擎支持的多角色自主A2A协作 |

**AIMail 的核心定位：** 不是让 Agent 学会操作邮箱，而是让 Agent 以邮件协议为纽带，与人和其他 Agent 自然地交流与协作。

***

## 优势特性

1. **SMTP-HTTP 双向转发，内外有别，进出有序**\
   SMTP 收信、Webhook 推送、HTTP 发信、SMTP 外投——两入两出，统一调度，内转外投，收发自如，全链路日志可追溯。
2. **安全员和白名单多重配置, 访问安全可管可控**\
   默认白名单启用，非授权发件人无法触达 Agent，同时 Agent 也无法向未授权地址外发内容。双向管控，安全闭环。Agent 的关键操作需配置的安全员确认，安全有兜底。
3. **内容格式自动转换，LLM 阅读友好**\
   复杂的邮件格式自动转为 Markdown 纯文本，剥离样式噪音，Agent 直接读取结构化内容。
4. **内容本地存储，检索快捷方便**
   入站出站的邮件快照存储在本地，并预建全文索引，提供搜索工具，邮件检索快捷又方便。
5. **邮件即会话，会话即指令**\
   邮件收发即会话，自动补全上下文。创新的多种邮件指令，让对话即指令可执行，无缝接入日常工作流。
6. **自带协作原语和看板，人机混合自主协同**\
   原生 A2A 协作看板，自定义工作流引擎。20+ 指令动词 + 10 种自动通知 + 协作原语，支持跨系统异构 Agent 的全网协作。
7. **多模式/多路复用的消息传送，高效穿透任何网络环境**\
   Inbound Push/Pull 双模式共存，支持单邮件多目的地址，支持多gateway同机透传，可适配各类网络环境中的多样化 Agent。
8. **一键集成和诊断，低门槛部署和运维**\
   `./aimail install` 一条命令完成整条链路（激活 → Bridge → 工具与 Skill → 注册）；`check`/`ping`/`welcome` 全链路诊断；`stats`/`domain`/`uninstall` 一站式本机管理。

***

## 快速开始

AIMail 支持系统管理员在终端命令行的**系统级安装**；或者 Agent 管理员通过 Agent 对话界面的**对话安装**两种安装方式。

### 前置环境准备

- **操作系统环境**:Linux + Python 3.10。
- **已安装 Agent 系统**:任一已适配平台(DSH / OpenClaw / pi /
  deer-flow / Hermes,推荐 **Hermes** 与 **DSH**)。
- **已安装网关服务或有服务激活码**:自建可达的 [aimail-gateway](https://github.com/metercai/aimail-gateway)
  服务;或申请**云服务激活码**(共享域,`amail.token.tm`)。

### 环境变量确认清单

**场景 A — 共享域激活码:**

```bash
export AIMAIL_URL=https://amail.token.tm      # 网关地址
export AIMAIL_PRODUCT_CODE=<激活码>            # 云端测试激活码
export AIMAIL_SYSTEM_NAME=<你的标识名>          # 共享域系统: agent.<标识名>@<域名>
export AIMAIL_MANAGER_ADDRESS=you@example.com # 默认 manager(接收 welcome 邮件)
```

**场景 B — 独立域网关服务:**

```bash
export AIMAIL_URL=<你的网关地址>               # 如 https://mail.example.com
export AIMAIL_ADMIN_KEY=<admin key>           # 网关管理员凭据
export AIMAIL_DOMAIN=<你的域名>                # 独立域,如 example.com
export AIMAIL_MANAGER_ADDRESS=you@example.com # 默认 manager
```

### 系统级安装

#### 第 1 步:Bootstrap 系统环境初始化。

```bash
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/bootstrap.sh | bash
```

#### 第 2 步:SDK或插件安装。

**aimail命令行安装**:

```bash
aimail install --home ~/.hermes       # Hermes(亦可 ~/.dsh、~/.openclaw、~/.pi、deer-flow , --home 指Agent的主目录)
```

**Agent命令行安装**
```bash
dsh plugin --profile web add dsh-aimail
#pi install npm:pi-aimail
#openclaw plugins install openclaw-aimail
```

#### 第 3 步:接入闭环验证。

```bash
aimail welcome       # 网关向Agent发欢迎邮件，Agent回复管理员 = 端到端打通证明
#aimail check         # 全面体检(配置 → 运行时资源 → 链路);有问题先跑它
#aimail stats         # 系统 / agent / 邮件总览
```

#### > 提示：
- 支持多系统安装，即支持单机多Agent平台，修改环境变量后（新系统要用新的激活码或admin-key），指定不同的`--home`，执行SDK或插件安装。
- 对已安装系统，可用不同参数重复安装，但需指定系统ID: `aimail install --system-id <sid>`。

---

### Agent 对话安装

把根据场景确定的环境变量内容填好，然后复制拷贝内容到Agent的对话框内执行。

```txt
export AIMAIL_URL=https://amail.token.tm      # 网关地址
export AIMAIL_PRODUCT_CODE=<激活码>            # 云端测试激活码
export AIMAIL_SYSTEM_NAME=<你的标识名>          # 共享域系统: agent.<标识名>@<域名>
export AIMAIL_MANAGER_ADDRESS=you@example.com # 默认 manager(接收 welcome 邮件)
按照下面链接内容的指导获取自己的aimail邮件地址
https://raw.githubusercontent.com/metercai/aimail/main/docs/agent-self-setup_zh.md
```

***

## 系统架构

AIMail 核心由**aimail-gateway**（邮件网关）和 Agent 内的 **aimail-sdk**两大部件组成。在复杂网络环境下需要**aimail-bridge**的配合进行穿透，让收发邮件安全高效流转。aimail 命令行则提供了Agent侧的SDK安装、链路检测等日常维护工具，方便使用和维护。

```
                     ┌────────────────────┐ 
                     │   aimail-gateway   │
                     │                    │
   External Mail ───►│ SMTP Receiver      │◄───► Inbound Push/Pull ────────┐
                     │        ↑           │                                │
                     │  Internal Routing  │                                │
                     │        │           │                                │
   External Mail ◄───│ SMTP Sender    send│◄─── HTTP API ────┐             │
                     │                    │                  │             │
                     │ A2A Board Engine   │                  │             │  
                     │ · Instructions     │                  │             │  
                     │ · Sessions         │                  │             │
                     │ · Notifications    │                  │             │   
                     └────────────────────┘                  │             │
                                                             │             │
                     ┌────────────────────┐                  │   ┌─────────┴─────────┐
                     │   Hermes Agent     │                  │   │  aimail-bridge    │
                     │                    │                  │   │ multiplex webhook │
                     │ ┌────────────────┐ │                  │   └───┬──┬──┬──┬──┬───┘ 
                     │ │   aimail SDK   │ │──── Outbound ────┘             │
                     │ │ · Webhook recv │ │                                │
                     │ │ · Preprocessor │ │                                │
                     │ │ · send_mail()  │ │◄─── Inbound Webhook ───────────┘
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

***

## 邮件地址格式规范

- 独立网关，独享域名

以Hermes为例。部署独立网关 [aimail-gateway](https://github.com/metercai/aimail-gateway)，使用自有域名。根 Profile 默认为 `agent@{domain}`，其他通过 `hermes -p` 创建的 Profile，直接取其名字为地址  `{profile}@{domain}`。AIMail 特别支持Hermes的单 profile 多个 Persona 的场景，自动衍生Persona地址。

| 类型         | 格式                             | 示例                         |
| ---------- | ------------------------------ | -------------------------- |
| 根 Profile  | `agent@{domain}`               | `agent@company.com`        |
| 命名 Profile | `{profile}@{domain}`           | `report@company.com`       |
| Persona    | `{persona}.{profile}@{domain}` | `sales.report@company.com` |

- 官方共享域名

同样以Hermes为例。从官方网站申请共享域名的产品激活码激活的系统时，用户要确定自己的 `system_name`（3-8 字符）来进行区隔，例如: `meter`。这样的邮件地址格式为：

| 类型         | 格式                                                  | 示例                                  |
| ---------- | --------------------------------------------------- | ----------------------------------- |
| 根 Profile  | `agent.{system_name}@{shared_domain}`               | `agent.meter@amail.token.tm`        |
| 命名 Profile | `{profile}.{system_name}@{shared_domain}`           | `report.meter@amail.token.tm`       |
| Persona    | `{persona}.{profile}.{system_name}@{shared_domain}` | `sales.report.meter@amail.token.tm` |

***

## 场景示例

- **合同审核：** 法务 Agent 直接接管合同审核邮箱，合同文本或协议草案作为邮件附件发送即可。Agent 自动解析条款、识别风险点，并回复批注版本，同时抄送相关审批人，全程留痕可追溯。 [→ 示例](examples/01-contract-review_zh.md)
- **进度报告：** Agent 定期汇总项目进度、风险事项与里程碑完成情况，生成结构化报告邮件，自动发送至项目组成员。也可按角色定制内容（如给 Leader 的摘要版 vs 给执行层的详细版），并可接收成员的邮件回复反馈。 [→ 示例](examples/02-progress-report_zh.md)
- **问题澄清：** Agent 在执行任务（如撰写周报、数据分析）过程中发现信息矛盾或缺失时，自动向相关同事发送澄清邮件，指明矛盾点并附上上下文。对方通过邮件回复后，Agent 自动解析回答并继续推进任务，无需人工干预切换工具。 [→ 示例](examples/03-issue-clarification_zh.md)
- **调查问卷：** Agent 批量发送问卷邮件至目标群体，邮件正文或附件内含有问卷及可回复的结构化表单。Agent 自动跟踪回收进度，定时催办未回复者，回收完成后自动汇总数据、生成分析图表，并邮件反馈给发起人。 [→ 示例](examples/04-survey_zh.md)
- **流程协同：** 在网站改版等跨角色项目中，设计师 Agent、前端 Agent、产品经理通过 A2A 协作看板共享任务看板，所有沟通与决策通过邮件指令同步——如设计稿定稿时，看板自动触发邮件通知下游 Agent 启动开发，各角色可在邮件线程中反馈意见，看板同步更新状态。 [→ 示例](examples/05-a2a-collaboration_zh.md)
- **财务预审：** 员工提交报销时，将报销邮件抄送至预审 Agent 的专属邮箱。Agent 自动核验发票真伪、合规性及预算余额，回复预审意见（通过/驳回/需补充材料）并抄送财务审核人，人工只需确认最终放行，大幅压缩审核周期。 [→ 示例](examples/06-financial-preauth_zh.md)
- **客服支持：** Agent 直接接管 `support@` 公司邮箱，自动接收客户咨询邮件，解析意图与情感倾向，自动做分类。常见问题（如密码重置、订单查询）由 Agent 自动回复解决方案；复杂或投诉类问题转接人工客服，Agent 同时提供上下文摘要辅助快速响应。全程邮件记录归档，便于服务质量回溯。 [→ 示例](examples/07-customer-support_zh.md)

**AIMail** 可以将 Agent 非常丝滑无缝的接入任何的邮件工作场景。

***

## 延伸阅读

- [AIMail 安装与维护指南](docs/MAINTENANCE_zh.md)
- [AIMail 对接适配指导](docs/AGENT-INTEGRATION.md)
- [A2A Board 项目协作指导手册](docs/board/A2A-BOARD-GUIDE_zh.md)
- [API 依赖说明](docs/API-DEPS.md)
- [aimail-gateway](https://github.com/metercai/aimail-gateway)
- [aimail-bridge](https://github.com/metercai/aimail/bridge/README_zh.md)

