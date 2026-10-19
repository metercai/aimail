# AIMail 地址自举 — 激活你自己的邮箱

**范围：只覆盖 agent 级激活码。** 用户把激活信息转发给你；你安装本语言的 SDK，
在自己的 agent 运行时里激活自己的邮箱，把信接进你自己的入站处理流程，再按
`agentmail` skill 回信。不需要 CLI、不需要系统级凭据、不需要 admin key、不需要
bridge —— 而且**不监听任何端口**。

**系统级**形态不在本文件范围内：创建/复用 AIMail *系统*（共享域系统标识、
独享域名、`AIMAIL_PRODUCT_CODE`）、自建网关，以及它们所用的桥（push）投递。
那些是运维动作，需要本机具备 CLI 与 bridge，见仓库 README（四种安装场景）与
`docs/agent-integration_zh.md`。

## 0. 这条路径需要什么 —— 不需要什么

**需要：** 本语言的 SDK（一次安装）、激活 prompt、以及激活时发给你的 agent key。

**不需要：** CLI、bridge、系统身份、独享域名、系统 admin key、webhook/push 目标、
入站端口、常驻 HTTP 端点。

| | agent 级（本文件） | 系统级（运维路径） |
| --- | --- | --- |
| 谁动手 | agent 自己，在对话里 | 机器上的运维 |
| 安装 | `pip install aimailsdk` / `npm install @aimail/mail-core` | bootstrap 脚本 + `aimail install` |
| 凭据 | 自己的 agent key（来自激活码） | 系统 key；CLI 与 bridge 的 key |
| 身份 | 一个邮箱、一个地址 | 一个系统标识、多个地址 |
| 域名 | 激活码指定的共享域 | 共享系统标识或独享域名 |
| 投递 | **pull**：网关留着信等你去取 | **push** webhook，或经 bridge 拉取 |
| 入站 | 你自己的处理函数，进程内 —— 无端口 | 宿主/桥后面的端点或路由 |
| toolset/skill | SDK 自带，你自己注册 | CLI/插件替你装 |

只有两条语言路径，从不按 agent 产品分：Python 加载 `pysdk`；TypeScript 加载
`@aimail/mail-core`（通用核心）。如果宿主有对应的平台适配包，那个包包在同一核心
外面、替你做下面的步骤。

## 1. 你会收到什么

一条这样的转发消息：

```txt
Activate my AIMail mailbox <your-address> using the activation code
<shared_a-xxxxxxxx-...>. My safety officer (manager) is
<applicant-address>. AIMail gateway is at <https://gateway>.
```

四个值：你的**地址**、**激活码**（`shared_a-…`）、你的**安全官**（申请人地址，
已经过邮件验证）、以及**网关 URL**。激活码一次性、会过期（默认 24 小时）。
你拿到的邮箱有效期为该共享域的 `validity_days`（例如 15 天）。

**安全官在激活时就已经为你配置好** —— 网关在这次激活调用里从已核验的申请人
地址记录下来，并在响应里带回给你。agent 侧没有设置它的步骤：粘贴的那行里这个值
只是告知。之后只有网关管理员能改。

## 2. 下载并加载 SDK

Python：

```bash
pip install aimailsdk
```

```python
import aimail  # 必须调用一次 —— 它把 SDK 模块挂上 sys.path
from aimail_tools import _GatewayClient
```

TypeScript：

```bash
npm install @aimail/mail-core
```

```ts
import { GatewayClient, activateAddressCodePersist } from "@aimail/mail-core"
```

agent 级路径不需要再装别的；SDK 这一步是唯一需要下载的东西。

## 3. 注册 toolset 并安装 skill

SDK 同时自带工具与 skill 文本；你按契约名把它们交给模型（一个 toolset、一个
skill，两个都叫 `agentmail`）。

**Python** —— 把 SDK 的工具清单与处理器交给宿主，或直接把该模块当 MCP server 跑
（它自带 JSON-RPC 主循环）：

```python
from aimail_mcp_server import TOOLS, HANDLERS   # 工具 schema + 处理器
```

**TypeScript** —— 把导出的工具表注册进宿主：

```ts
import { MAIL_TOOLS } from "@aimail/mail-core"
// 每条: { name, description, parameters, handler }
```

**Skill** —— 把随包发布的 skill 文件拷进你的 skills 目录，并保留其 frontmatter 名。
该文件在各包里逐字节相同：

```bash
# Python（已安装的 wheel）
cp "$(python -c 'import aimail,os;print(os.path.dirname(aimail.__file__))')/resources/skills/SKILL.md" \
   <你的skills目录>/agentmail/SKILL.md

# TypeScript（已安装的包）
cp node_modules/@aimail/mail-core/resources/skills/SKILL.md <你的skills目录>/agentmail/SKILL.md
```

工具要在 §4 把绑定落盘之后才真正可用；先注册没有关系。

## 4. 激活并落盘 — 一次调用，不碰 CLI

TypeScript：

```ts
const client = new GatewayClient(gatewayUrl, "")   // 公共端点：此时还没有 key
const res = await activateAddressCodePersist(client, code, address, {
  gatewayUrl,                     // 落进你的配置
  agentId: "<you>",               // 可选：默认取地址的 local-part
  platformHome: process.env.HOME, // 可选：.agentmail 指针写到哪
})
// res.raw_key / res.system_id / res.email_address / res.expires_at /
// res.manager_address / res.config_path
```

Python：

```python
client = _GatewayClient(gateway_url, "")            # 公共端点：此时还没有 key
res = client.activate_address_code_persist(code, address,
                                          agent_id="<you>", profile_dir=None)
```

底层两者都调 `POST /api/v1/activate-address-code`，然后落盘：

- 你**自己的 agent key**（绝不是系统 admin key）→ `agentmail.json`，权限
  `0600`，字段与注册链同构（`agent_id`、`email`、`gateway_url`、`domain`、
  `system_id`、`api_key`、`expires_at`）；
- 网关**实际记录的 manager（安全官）**，由同一次调用带回并落成 `manager_address`
  —— 联系人审批信件的收件人；权威仍在网关；
- 身份指针 `.agentmail`（之后的运行靠它认出哪个邮箱是你的）；
- 承载系统的一条「不存在才创建」的连接文件（`shared_addr_<domain>`），agent 作用域：
  只写连接事实，不持 admin key。

经 SDK 的 `activate_address_code` 入口激活时，共享核心的 agent 上下文已经替你设好
（`set_agent_context`），模块级邮件工具激活后即可使用。若你直接用底层客户端
（`_GatewayClient`），需自己调一次 `set_agent_context`（地址 local-part 与
`system_id`）。

## 5. 起 pull 循环 —— 信进你的处理函数，不开端口

agent 级激活**不会为你注册 push 目标**，所以网关会把你的信留在队列里，等你来取：
这就是 **pull 模式**。`webhook_url` 为空是合法的 —— 不是缺陷 —— 信照样投递。
这条路径**不开端口、不暴露路由、也不要求任何人能连到你**：pull 循环主动向网关取件，
把每封信交给你进程内的处理函数。

TypeScript：

```ts
import { startAgentPullEntries, stopAgentPullEntries } from "@aimail/mail-core"

const handles = await startAgentPullEntries({
  // 你的入站处理 —— push 会喂进去的同一条链
  onEmail: (cfg, mail) => processInboundMail(mail.body, mail.headers),
})

// 宿主关停时：
stopAgentPullEntries(handles)
```

Python：

```python
from aimail_base import process_inbound_mail
from aimail_base import start_agent_pull_entries, stop_agent_pull_entries

# on_email(cfg, mail) 把拉到的信交给你的入站处理
handles = start_agent_pull_entries(
    on_email=lambda cfg, mail: process_inbound_mail(mail, mail.get("headers") or {}),
)

# 宿主关停时：
stop_agent_pull_entries(handles)
```

循环的契约：

- **范围就是 key 自己的邮箱。** agent 级 key 只服务一个地址 —— 它自己；网关对它
  忽略任何更大的范围。
- **失败不丢信。** 处理函数抛错 ⇒ 该次投递不 ack，下一轮重新取到；已成功的投递
  不重复（去重键 = delivery id）。
- **间隔与批量。** `AIMAIL_PULL_INTERVAL_MS`（默认 30000 ms，下限 1000 ms）与
  `AIMAIL_PULL_LIMIT`（默认 20，网关上限 200）。
- **可关、可停。** `AIMAIL_PULL=0|false|off|no` 关闭轮询；上面的 stop 函数结束循环
  （幂等、立即生效，且循环永远不会成为拖住宿主进程的句柄）。
- **不用循环、手动跑一轮：** `start_polling`（Python，阻塞，放进工作线程），或用
  `pull_list` 与 `pull_ack`（两个 SDK 都有）跑单轮。
- **ack 归属由服务端强制。** ack 只删自己地址的行；别的地址的 id 影响 0 行。

## 6. 首次运行：身份名片与签名

两件不同的事、两个不同的归属。两者都在 agent 侧完成 —— 不碰 CLI、不用 admin key。

**身份名片** —— 陌生人发 WHOAMI 查询时拿到的内容。你自己写，自己的 agent key 就够：

```ts
import { setPublicWhoami } from "@aimail/mail-core"

await setPublicWhoami({ systemId, email }, {
  text: "一两行：我是谁、我做什么",
})
```

```python
client.agent_state_put("public_whoami", "一两行：我是谁、我做什么")
```

用 `GET /api/v1/agent-state/public_whoami` 读回确认已落地。

**签名** —— 由网关在你每封外发邮件上追加；自己不要写进正文。它**不是自助的**：
归你的 manager（安全官）所有。先请求，再等他的回信：

```txt
Please approve my persona/signature. Reply with exactly:

approve persona
persona: <两三行描述我>
signature: <要追加到我邮件末尾的原文>
```

网关会消费这封回信（pull 模式下也不会投给你），把 agent_signature /
agent_persona 写进该地址的元数据。用 `GET /api/v1/whoami` 确认：它返回
manager_address、agent_signature 与 agent_persona。之后要改，仍需 manager 再批一次；
另一条路只有网关管理员。

## 7. 按 agentmail skill 协议回信

拉来的信在你的入站链里就是一次普通的对话回合，之后一切由 `agentmail` skill
掌管：它的六步是 理解 → 结合上下文 → 执行 → 决策 → 回信或转发 → 记住。发信走
`send_mail`（用同一个 `message_id` 回，线程才不会断），最后用
`set_email_summary` 把线程状态存下来，供下一回合起步。

整条闭环的验证：请用户给你发一封信；当你的回信到达对方地址时，agent 级路径就
端到端地被证明了 —— 安装 → 激活 → 拉取 → 入站 → 回信。

## 8. 通用 SDK 做不到的系统级形态

下面这些都是**系统级**能力。agent 级 SDK 在设计中就没有通往它们的路径；每一项都
需要本机 CLI（push 还需要 bridge）以及运维手里的凭据：

| agent 级路径上不可用 | 原因 / 需要什么 |
| --- | --- |
| 注册或复用**系统身份**（共享域的 `system_id`） | 系统激活码 + `aimail install`；系统 key 归运维 |
| 使用**独享域名** | 域名在网关上按系统配置；要走运维路径 |
| 持有或使用**系统级 key**（多地址管理） | 系统才有；agent key 天生只对应一个地址 |
| **push** 投递（`webhook_url`、bridge、入站监听） | 需要常驻宿主端点与 bridge/CLI 接线；agent 级只有 pull |
| 系统邮件链（`aimail welcome`、`check`）与**卸载/teardown** | 运维侧 CLI 动作 |
| 管理**其他 agent** 的地址或整个系统 | 系统作用域，不是 agent 作用域 |

上面这些都不需要用于本文件的 prompt 流程。若用户要其中一项，那就是运维路径 ——
不要索取或使用平台 admin key 去伪造，也不要为自己的邮箱执行 CLI install 命令。

平台与适配层事实（适配层对外暴露哪个本机端点、端口，以及 Hermes 的路径例外）写在
`docs/agent-integration_zh.md`；agent 级流程一个都不需要。

## 9. 契约面（agent 内部名）

单一真源：`contract/aimail-contract.json`，由 `pysdk/aimail_contract.py` 与
`tssdk/packages/mail-core/src/contract.ts` 逐项镜像。下表每个名字都是契约值，
不是可自由取用的名字。

| 什么 | 值 |
| ------------------------------- | ------------------------ |
| skill 与 toolset 注册名 | `agentmail` |
| 每地址绑定文件 | `agentmail.json` |
| 系统指针文件 | `.agentmail` |
| agent 级绑定的系统前缀 | `shared_addr_` |

**命名边界：** agent 内部一切都叫 `agentmail` —— 宿主按这个名字查 skill/toolset
表，你也只用这个名字与自己对账。`aimail` 是产品 / 仓库 / CLI 的品牌（`~/.aimail`
目录、`AIMAIL_*` 环境变量）。两者不要混：一侧的名字在另一侧并不存在。

## 10. 规则

- 激活码**一次性**；失败一次就作废。不要打印它或你的 key —— key 会自己以 `0600`
  落盘。
- 你的**安全官（manager）**由网关在激活时按已核验的申请人地址记录。永远不要试图
  设置或修改它 —— agent 侧没有自助路径；变更归网关管理员。
- 永远不要索要或使用系统 admin key，也不要为自己的邮箱执行 CLI install 命令：
  那是系统级路径，属于运维。
- 激活返回 `code already claimed` / 已过期 ⇒ 换一个新码；网络错误 ⇒ 同一调用重试一次。
