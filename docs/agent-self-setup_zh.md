# AIMail 地址自举 — 激活你自己的邮箱

**范围：只覆盖 agent 级激活码。** 用户把激活信息转发给你；你在自己的 agent
运行时里激活自己的邮箱，打开轮询把信接进你自己的入站流程，再按 `agentmail`
skill 回信。不需要 CLI、不需要系统级凭据、不需要 admin key。

**系统级**形态不在本文件范围内：创建/复用 AIMail *系统*（共享域系统标识、
独享域名、`AIMAIL_PRODUCT_CODE`）、自建网关，以及它们所用的桥（push）投递。
那些是运维动作，需要本机具备 CLI 与 bridge，见仓库 README（四种安装场景）与
`docs/agent-integration_zh.md`。不要索取产品码，也不要为自己的邮箱执行
`aimail install`。

## 0. 你加载哪个 SDK —— 只按语言分

只有两条对接路径，只按**语言**区分，从不按 agent 产品区分：

- **Python** —— 加载 `pysdk`：`pip install aimailsdk`，随后 `import aimail`
  一次（它把 SDK 模块挂上 `sys.path`，之后其它 import 才成立）。所有 Python 宿主
  都用它；平台适配层只是包在同一批函数外面替你调用。
- **TypeScript** —— 加载 `@aimail/mail-core`，通用 TS 核心：
  `npm install @aimail/mail-core`。宿主若有匹配的适配包，适配包包在这个核心外面；
  没有适配包的宿主直接用核心本身。

agent 级路径不需要再装别的 —— 不要 CLI、不要 bridge、不要适配层；SDK 这一步
是唯一需要下载的东西。

通用 TS 核心只覆盖 **agent 级**对接。增加系统、独享域名、系统 key 或 push 投递
属于上面的系统级形态，需要 CLI 和 bridge；agent 级路径两者都不需要。

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
地址记录下来。agent 侧没有任何步骤：粘贴的那行里这个值只是告知。不要自己去设置
或修改它；之后只有网关管理员能改。

## 2. 激活并落盘 — 一次调用，不碰 CLI

TypeScript —— 来自 `@aimail/mail-core`（通用核心；宿主适配包包在它外面）：

```ts
import { GatewayClient, activateAddressCodePersist } from "@aimail/mail-core"

const client = new GatewayClient(gatewayUrl, "")   // 公共端点：此时还没有 key
const res = await activateAddressCodePersist(client, code, address, {
  gatewayUrl,                     // 落进你的配置
  agentId: "<you>",               // 可选：默认取地址的 local-part
  platformHome: process.env.HOME, // 可选：.agentmail 指针写到哪
})
// res.raw_key / res.system_id / res.email_address / res.expires_at / res.config_path
```

Python —— 来自 `pysdk`：

```python
import aimail  # noqa: F401 —— 把已安装的 SDK 模块挂上 sys.path
from aimail_tools import _GatewayClient

client = _GatewayClient(gateway_url, "")            # 公共端点：此时还没有 key
res = client.activate_address_code_persist(code, address,
                                          agent_id="<you>", profile_dir=None)
```

底层两者都调 `POST /api/v1/activate-address-code`，然后落盘：

- 你**自己的 agent key**（绝不是系统 admin key）→ `agentmail.json`，权限
  `0600`，字段与注册链同构（`agent_id`、`email`、`gateway_url`、`domain`、
  `system_id`、`api_key`、`expires_at`）；
- 网关为这条地址**实际记录的 manager（安全官）** —— 由同一次激活调用带回并落成
  `manager_address`；它是联系人审批信件的收件人，权威仍在网关；
- 身份指针 `.agentmail`（之后的运行靠它认出哪个邮箱是你的）；
- 承载系统的一条「不存在才创建」的系统连接文件（`shared_addr_<domain>`），
  首次激活时自动生成。

## 3. 走完闭环：把信拉进来，然后回信

agent 级激活**不会为你注册 push 目标**，所以网关会把你的信留在收件队列里，
等你来取：这就是 **pull 模式**。`webhook_url` 为空是合法的 —— 不是缺陷 ——
信照样投递；那里有值则是系统级部署所用的 push 形态。pull 模式只为 agent 级激活码
开启，它产出的绑定其 `system_id` 以 `shared_addr_` 开头，这个前缀就是 agent 级的
标记。

### 3.1 为自己这条邮箱起轮询入口

宿主若带适配层，轮询入口会**自己起来**：适配器在初始化时起、在 dispose/shutdown
时停，并把每封拉到的信投进 **push 用的同一条入站链** —— 拉来的信与推来的信对你
的到达方式完全一样。

自行嵌入 SDK 时，就自己起：一次调用，每条 agent 级绑定一条轮询循环，投进你自己的
入站入口。

TypeScript（`@aimail/mail-core`）：

```ts
import { startAgentPullEntries, stopAgentPullEntries } from "@aimail/mail-core"

const handles = await startAgentPullEntries({
  // 你自己的入站链 —— push 用的那个入口
  onEmail: (cfg, mail) =>
    processInboundMail(
      mail.body as Record<string, unknown>,
      mail.headers as Record<string, string>,
    ),
})

// 宿主关停时：
stopAgentPullEntries(handles)
```

Python：

```python
import aimail  # noqa: F401 —— 把已安装的 SDK 模块挂上 sys.path
from aimail_base import process_inbound_mail
from aimail_base import start_agent_pull_entries, stop_agent_pull_entries

# on_email(cfg, mail) 把拉到的信交给 push 用的那个入口
handles = start_agent_pull_entries(
    on_email=lambda cfg, mail: process_inbound_mail(mail, mail.get("headers") or {}),
)

# 宿主关停时：
stop_agent_pull_entries(handles)
```

经 SDK 的 `activate_address_code` 入口激活时，共享核心的 agent 上下文已经替你设好
（`set_agent_context`），模块级邮件工具激活后即可使用。若你直接用底层客户端
（`_GatewayClient`），需自己调一次 `set_agent_context`（地址 local-part 与
`system_id`）；否则这些工具会答 “aimail not configured for this profile”。

若要自己手动驱动，底层调用是 `start_polling`（Python，阻塞循环，放进工作线程）
以及 `pull_list` 与 `pull_ack`（两个 SDK 都有），用来手工跑一轮。

### 3.2 拉进来的信落在哪

拉来的信交给你传进去的入站处理函数（TypeScript 的 onEmail、Python 的 on_email）
—— 就是 push 指向的那一个，所以两种模式下你的收信处理没有任何差别。适配层对外
暴露哪个本机端点属于适配层事实、不是本流程的一环；各平台的端点列在
`docs/agent-integration_zh.md`。

### 3.3 按 agentmail skill 协议回信

拉来的信在你的入站链里就是一次普通的对话回合，之后一切由 `agentmail` skill
掌管：它的六步是 理解 → 结合上下文 → 执行 → 决策 → 回信或转发 → 记住。发信走
`send_mail`（用同一个 `message_id` 回，线程才不会断），最后用
`set_email_summary` 把线程状态存下来，供下一回合起步。

整条闭环的验证：请用户给你发一封信；当你的回信到达对方地址时，agent 级路径就
端到端地被证明了 —— 激活 → 落盘 → 轮询 → 入站 → 回信。

## 4. 首次运行：身份名片与签名

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

首次运行全链：安装 → 激活 → 身份名片 → 签名 → pull → 回信。

## 5. 契约面（agent 内部名）

单一真源：`contract/aimail-contract.json`，由 `pysdk/aimail_contract.py` 与
`tssdk/packages/mail-core/src/contract.ts` 逐项镜像。下表每个名字都是契约值，
不是可自由取用的名字。

| 什么 | 值 |
| ------------------------------- | ------------------------ |
| skill 与 toolset 注册名 | `agentmail` |
| 每地址绑定文件 | `agentmail.json` |
| 系统指针文件 | `.agentmail` |
| agent 级绑定的系统前缀 | `shared_addr_` |

宿主（或其适配层）对外暴露的入站路径与监听端口属于宿主一侧，列在
`docs/agent-integration_zh.md`；上面的 agent 级流程一个都不需要，因为拉来的信
交给你自己传进去的处理函数。

**命名边界：** agent 内部一切都叫 `agentmail` —— 宿主按这个名字查 skill/toolset
表，你也只用这个名字与自己对账。`aimail` 是产品 / 仓库 / CLI 的品牌（`~/.aimail`
目录、`AIMAIL_*` 环境变量）。两者不要混：一侧的名字在另一侧并不存在。

## 6. pull 契约

- **作用域就是这个 key 自己的邮箱。** agent 级 key 只服务一个地址 —— 它自己的。
  pull 请求里的 `emails` 字段是为了与桥的报文形状兼容而存在，网关侧忽略它：
  你的作用域**本身就是**范围。
- **ack 的所有权在服务端强制。** 一次 ack 只删除属于你自己地址的行；属于别的地址
  的 id 影响 0 行。
- **失败不会丢信。** 你的入站处理抛异常时，那件投递不做 ack，下一轮会重新拉到；
  已经成功投递的不会重复。去重键是 delivery id。
- **间隔与批量。** `AIMAIL_PULL_INTERVAL_MS` 定轮询间隔（默认 30000 ms，下限
  1000 ms）；`AIMAIL_PULL_LIMIT` 定单轮批量（默认 20，网关上限 200）。
- **可以关、可以停。** `AIMAIL_PULL=0|false|off|no` 关闭轮询；
  `stopAgentPullEntries`（TypeScript）与 `stop_agent_pull_entries`（Python）停掉
  循环 —— 幂等、立即生效，而且轮询循环永远不会成为吊住你宿主进程的那个句柄。
- **key 到期与续期。** `expires_at` 在你的绑定里。到期前请用户重新申请：你会拿到
  一个新码并重做 §2，续期**保持同一地址**。你停机期间到达的信会继续留在网关，
  下一轮被拉进来。

## 7. 规则

- 激活码**一次性**；一次失败就废掉。不要打印它或你的 key —— key 自己以 `0600`
  落盘。
- 你的**安全官（manager）**由网关在激活时按已核验的申请人地址记录。永远不要尝试
  设置或修改它 —— 没有 agent 侧或自助路径；改它属于网关管理员。
- 永远不要索取或使用系统 admin key，也不要为自己的邮箱跑 CLI 安装命令：那是
  系统级路径，只属于运维。
- 激活若报 `code already claimed` / 过期，就再要一个新码；若报网络错误，把同一次
  调用重试一次。
