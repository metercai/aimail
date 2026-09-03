# agentmail.json 字段参考(地址级配置)

> 状态:备案(2026-08-18)。通用字段 = 现状实锤(三平台磁盘实例 + 代码读取点核实);
> dsh 扩展字段 = 方案已定,待 dsh 对接实施时落地。
> 位置:`~/.aimail/systems/{system_id}/{cleaned_addr}/agentmail.json`
> (cleaned_addr = email 清洗名,如 `agent.weiwei_amail.token.tm`;目录按 agent 隔离)

## 1. 定位与边界

| 项 | 说明 |
|----|------|
| 本质 | **per-address(agent 级)配置**,一个文件 = 一个邮件地址的全部运行配置 |
| 对比 | 系统级配置 = `agentmail_gateway.json`(`systems/{sid}/` 根,唯一文件名,无兼容别名);agentmail.json 在地址子目录,多 agent 系统每 agent 一份 |
| 共享布局 | 与 `board_creds.json` 同层;`~/.aimail/` 权限 700,配置文件 600 |
| 读取方 | 平台无关共享核心:`aimail_base.load_agent_config` / `_scan_systems_for_agent`(按 `agent_id` 匹配)、`set_agent_context`;`aimail_tools`(send_mail 等 12 工具经 `_GatewayClient(config["gateway_url"], config["api_key"])`) |
| 写入方 | 各平台注册脚本(薄调用共享注册链 register_agent_email 后落盘):Hermes 注册链 / OpenClaw `bin/register_agent.py` / DeerFlow `scripts/deer-flow/register_agent.py`;dsh 由 `scripts/dsh/bind_agent.py` 落盘 |
| 解析 | dict 加载(`json.loads`),**未知字段无害**(无严格 schema 拒绝)——平台特有字段可自由并入 |
| 原子写 | 改任何字段 = 整文件 tmp+replace,禁止局部覆盖(防并发写坏) |

### 1.1 webhook_url 与入站链路(2026-08-18 修正,用户纠正)

**`webhook_url` + `webhook_secret` 成对出现,是 agent 侧接收端点的注册声明**:

- 注册链 `register_agent_email(client, system_id, email, webhook_url, webhook_secret, ...)` 把两者随 `register_email` 发给 gateway(云端 system_domains 记录)——`webhook_url` = agent 侧接收端点地址,`webhook_secret` = 配套验签密钥(HMAC 校验 `X-Webhook-Signature`)。
- **bridge 是透明代理**:同内网环境下(自建 gateway),gateway 直接 webhook 到 agent 接收端点,**不经过 bridge**;跨网/NAT 环境才由 aimail-bridge(pull,2s 轮询 /pending → 路由表全 URL 转发)承担,转发到**同一接收端点**。两条路径共用同一字段,路由表在 `bridge/aimail_routes.toml`,不在 agentmail.json。
- **平台特有投递端点字段(如 deerflow_url)与 webhook_url 性质一致,应合并**:都是"agent 侧接收端点地址"。DeerFlow 的 `deerflow_url`(`http://127.0.0.1:8001`)= DeerFlow 接收端点 = 其 webhook_url;落盘统一用 `webhook_url` 字段,不另立名(2026-08-18 定调,方案执行时合并)。
- 本地接收端点(如 OpenClaw `/hook`、dsh mail-inbound 端点)同理 = 注册的 webhook_url 值。

入站完整链路:云端 SMTP 收信 → gateway 清洗/富化 → 入站队列 → **两条路径**:同内网 = gateway 直接 webhook 到接收端点;跨网 = aimail-bridge 轮询转发 → 接收端点 → **验签(webhook_secret)** → 预处理 → 投递 agent。

### 1.2 系统级 agentmail_gateway.json 字段参考(2026-08-18 复验,读写点实锤)

系统级配置 `~/.aimail/systems/{sid}/agentmail_gateway.json`(文件名唯一,无兼容别名)。**注意:此文件没有 webhook_url 字段**——接收端点是地址级概念,见下方特别说明。

| 字段 | 类型 | 含义与作用 | 写入方 | 读取方 |
|------|------|-----------|--------|--------|
| `gateway_url` | string | 云端 gateway 基址(https://amail.token.tm)。全部 HTTP 客户端基址 | setup_system | 共享核心/client/注册链 |
| `admin_key` | string | **system scope 管理密钥**(系统级;agent 级 api_key 在 agentmail.json)。注册链 client、bridge [pull] 条目派生 | setup_system | 注册链、reconcile、bridge 配置 |
| `system_id` | string | 系统标识(shared-token-xxx),目录键;指针文件值来源 | setup_system | 全 |
| `system_name` | string | 系统名(共享域地址的 .{system_name} 段) | setup_system | email_for_agent |
| `save_raw_snapshots` | bool | 原始快照保存开关 | setup_system | (注册链 inject 透传) |
| `domain` | string | 邮件域(地址拼接) | setup_system | email_for_agent |
| `manager_address` | string | 系统默认管理员地址(agent 注册缺省时沿用) | setup_system | 注册链 |
| `webhook_host` | string | **三态语义(2026-08-18 用户定稿)**:① 有合法 IP:port → 有 bridge,push 模式(地址注册参数直接用 webhook_host)② 显式空值 "" → 有 bridge,pull 模式(注册参数为空,云端不回调;bridge 空值=拉取)③ **配置项不存在** → 无 bridge(注册参数 = agentmail.json 的 webhook_url 本地端点)。安装时设置 | setup_system / deploy_bridge | 注册链(resolve_register_webhook_url 三态) |
| `system_home` | string | 平台根目录(Hermes=~/.hermes,OpenClaw=~/.openclaw)——CLI 平台推断的锚点 | setup_system | CLI(install/check/reset 平台探测) |
| `default_agent_name` | string | 默认主 agent 名映射(OpenClaw: main→agent) | CLI mailname | CLI mailname/check |

> 已删除(2026-08-18):`mode`(冗余——push/pull 由 webhook_host 三态表达,agent 侧只有 hook 入口;bridge 自身模式在 aimail_bridge.toml)、`bridge_port`(冗余——接收端点已在 agentmail.json webhook_url 里含端口;bridge 自身端口在 aimail_bridge.toml)。

### 1.3 特别说明:webhook_url(agentmail.json)与 webhook_host(agentmail_gateway.json)的区别

| | agentmail.json `webhook_url` | agentmail_gateway.json `webhook_host` |
|---|---|---|
| 层级 | **地址级(per-agent)** | 系统级 |
| 内容 | **完整接收端点 URL**(含协议+路径,如 `http://127.0.0.1:8646/webhooks/aimail-inbound`) | **三态**:有合法 IP:port / 空("")/ 配置项不存在 |
| 语义 | 本地接收端点 = **唯一信任源**(给 bridge 路由表;注册链落盘) | ① 有值 = 有 bridge,push 模式(bridge 公网入口,注册参数直接用)② 空 = 有 bridge,pull 模式(注册参数空,云端不回调)③ 无键 = 无 bridge(注册参数用本地端点) |
| 注册参数关系 | 无 bridge 时 = 地址注册参数;pull 时注册参数为空 | push 时 = 地址注册参数 |

**一句话:webhook_url 是"agent 在哪收"的本地事实(地址级,唯一信任源,始终落盘);webhook_host 是"是否有 bridge / 走 push 还是 pull"的系统级三态声明(安装时设置,决定地址注册参数)。两个字段各自独立——注册参数 = 三态解析结果,agentmail.json 落盘值始终是本地端点。**

## 2. 字段总表

### 2.1 通用字段(全部平台,4 平台共享)

| 字段 | 类型 | 必填 | 作用 |
|------|------|------|------|
| `email` | string | ✅ | agent 的 amail 地址。出站 sender(服务端强制 sender==key.email 身份隔离);入站路由目标;persona 归一基准;日志文件名(`aimail.{cleaned_addr}.log`)的构成源 |
| `api_key` | string(64 hex) | ✅ | agent 级 API 密钥(最小权限,1:1)。全部 gateway HTTP 调用鉴权(send/contacts/附件下载/pong);SMTP auth.local 认证唯一凭证(**无 admin_key 回退**) |
| `gateway_url` | string | ✅ | 云端 gateway 基址(如 `https://amail.token.tm`)。所有 HTTP API 调用目标 |
| `system_id` | string | ✅ | 系统标识(`shared-token-{hash}` / `system-{code}`)。目录键;gateway 侧系统归属;指针文件的值来源 |
| `domain` | string | ✅ | 邮件域(如 `amail.token.tm`)。**唯一作用 = agent 地址拼接**(`{base}[.{system_name}]@{domain}`);传给 `email_for_agent` 与 `register_email`(gateway 侧从 email 自行提取域,不另收 domain 参数) |
| `webhook_url` | string | ✅ | **agent 侧接收端点地址,所有平台入站成对字段,agentmail.json 唯一信任源**(2026-08-18 定调):注册时随 register_email 发给 gateway(云端 system_domains 记录);bridge 透明代理下同内网 gateway 直连、跨网 bridge 转发到同一端点。Hermes = `http://127.0.0.1:{wh_port}/webhooks/aimail-inbound`;OpenClaw = `http://127.0.0.1:{bridge_port}/hook`(外部预处理进程);DeerFlow = `http://127.0.0.1:8001/aimail/inbound`(进程内预处理)。**Hermes 的 profile config(platforms.webhook)只是运行时副本(webhook.py 监听/验签用),值同源写入——agentmail.json 才是 aimail 范畴唯一可读可信任的源** |
| `webhook_secret` | string | ✅ | **入站 HMAC 验签密钥,与 webhook_url 成对**(校验 bridge 转发的 `X-Webhook-Signature`)。权威源同 webhook_url:一律落 agentmail.json(Hermes 注册链从 agentmail.json 读取复用,防漂移;profile config 副本值同源) |
| `system_name` | string | 共享域必填 | 系统名。**共享域下参与地址拼接**:`{base}.{system_name}@{domain}`(如 `agent.weiwei@amail.token.tm`);独立域下为空 → `{base}@{domain}`。两种全地址拼接方式由 `system_id` 前缀判定(shared-* → 共享域,其余独立域) |
| `manager_address` | string | ✅ | 管理员邮箱。入站白名单判定;welcome 验收的收件人(管理员收到 Re: 回复) |
| `mx_domain` | string | 冗余 | **历史遗留冗余字段,已从所有实例文件移除**(2026-08-18 代码验证):client.register_email 的 mx_domain 形参从未传给 gateway(gateway 从 email 提取域);aimail_base 默认构造已简化。**与 domain 同一事物,无功能意义,新平台不写入,存量已清理** |

### 2.2 平台特有字段(按平台,互不冲突)

| 字段 | 类型 | 平台 | 作用 |
|------|------|------|------|
| `agent_id` | string | OpenClaw / DeerFlow / (Hermes 可选) | 平台内 agent 标识(`main` / `default` / profile 名)。`_scan_systems_for_agent(agent_id)` 的匹配键;Hermes 按 profile 名注册、文件内可缺省 |
| `deerflow_url` | string | DeerFlow | **已删除**(2026-08-18 重构执行):预处理并入 DeerFlow 本地 gateway(8001)进程后,接收端点统一为 `webhook_url`(`http://127.0.0.1:8001/aimail/inbound`);旧独立接收进程 amail_deerflow_bridge(8798)退役。存量文件含此字段时忽略 |
| `assistant_id` | string | DeerFlow | DeerFlow 平台内**助理定义标识**(如 `lead_agent`,预设角色)。**不进地址**:地址 base 来自 `agent_id`(default→agent);assistant_id 是投递目标(aimail_inbound 调 start_run 时指定哪个 assistant 处理)与 Hermes **profile 同级**(定义/角色层)。**assistant 有 name 字段**(代码实锤:AssistantResponse = assistant_id/graph_id/name,默认三者同名 "lead_agent";自定义 agent 时 name = 配置名,graph 统一 lead_agent)——未来多 assistant 各自收信时,地址 base 直接取 assistant 的 name 即可,无需另设命名体系;当前仅 lead_agent |

### 2.3 dsh 扩展字段(方案已定,待实施)

| 字段 | 类型 | 必填 | 作用 |
|------|------|------|------|
| `session_id` | string | dsh 必填 | **dsh 会话实例标识**。入站:收件地址 → 查此字段得 session_id → `ctx.agents.resume()/get()` → `followup`;出站:工具调用 `exec.agent.id`(= session_id)→ 反查此字段得 email/system_id → 读 api_key 调 gateway。dsh 身份 = 匿名 SessionId,此字段是其与 amail 地址的唯一绑定 |
| `preset` | string | dsh 必填 | dsh preset 名(如 `mail`)。agent 定义层归属记录(工具/SKILL 层),供 bind/unbind 与巡检使用 |

> 设计要点:api_key 不重复存(session_id/preset 之外无新秘密)——凭据唯一权威仍是
> 本文件的 `api_key` 字段,避免双份秘密。合并自原独立 `binding.json` 概念
> (2026-08-18 用户定调:per-address 文件独立成表是冗余,字段并入 agentmail.json)。

### 2.4 身份层级对照(2026-08-18 调研,防概念混淆)

四个平台"定义级 / 实例级 / 会话级"三层对照:

| 平台 | 定义级(角色/预设) | 实例级(身份,进地址) | 会话级(一次对话) |
|------|------------------|---------------------|------------------|
| Hermes | **profile**(命名,持久,如 aimail;default→agent 归一) | = profile(定义实例合一,无独立实例层) | session(sessions.json / state.db;webhook 会话按 sessionKey 聚合) |
| OpenClaw | agentId(main→agent 归一) | = agentId | hook 会话(sessionKey `agent:{id}:hook:amail`) |
| DeerFlow | **assistant_id**(lead_agent,预设角色,投递目标) | agent_id(default→agent,地址 base 来源;assistant_id 不进地址) | DeerFlow 侧会话(bridge 调用时上下文) |
| dsh | **preset**(mail,工具/SKILL 层) | **session_id**(匿名 UUID,1 地址 = 1 会话,唯一实例标识) | = 实例(无独立会话层;preset/uuid 解耦) |

结论与用户判断一致:**deerflow assistant_id ≈ Hermes profile 同级**(都是定义/角色层)。
差异:Hermes/OpenClaw 定义=实例(身份合一,profile/agentId 直接进地址);DeerFlow 定义(assistant_id)与地址分离(地址用 agent_id);dsh 定义(preset)与地址完全解耦(地址绑 session_id)。"默认主 agent 名"约定:Hermes default→agent、OpenClaw main→agent、DeerFlow default→agent(共享域 `agent.{system_name}@{domain}`);**dsh 无内置默认名**——bind 脚本显式绑定,主 agent 约定绑 `agent` 地址。

## 3. 实例(修订后形态,2026-08-18;存量文件字段超集,兼容读取)

```json
// Hermes(shared-token-40b34a66,共享域 weiwei)——通用字段全集(含 webhook_url/
// webhook_secret:唯一信任源落 agentmail.json;profile config 的 platforms.webhook
// 是 Hermes 运行时副本,值同源)
{
  "email": "agent.weiwei@amail.token.tm",
  "gateway_url": "https://amail.token.tm",
  "domain": "amail.token.tm",
  "system_id": "shared-token-40b34a66",
  "system_name": "weiwei",
  "manager_address": "925457@qq.com",
  "api_key": "<64hex>",
  "webhook_url": "http://127.0.0.1:8646/webhooks/aimail-inbound",
  "webhook_secret": "<64hex>"
}
```

```json
// OpenClaw(shared-token-9479c607,共享域 xianlin)——通用 + 平台特有(webhook_url 成对)
{
  "email": "agent.xianlin@amail.token.tm",
  "gateway_url": "https://amail.token.tm",
  "domain": "amail.token.tm",
  "system_id": "shared-token-9479c607",
  "system_name": "xianlin",
  "manager_address": "925457@qq.com",
  "api_key": "<64hex>",
  "agent_id": "main",
  "webhook_url": "http://127.0.0.1:8799/hook",
  "webhook_secret": "<hex>"
}
```

```json
// DeerFlow(shared-token-66b33608,共享域 deerflow)——通用 + 平台特有(webhook_url =
// 本地 gateway 接收端点,预处理并入 8001 进程;2026-08-18 重构)
{
  "email": "agent.deerflow@amail.token.tm",
  "gateway_url": "https://amail.token.tm",
  "domain": "amail.token.tm",
  "system_id": "shared-token-66b33608",
  "system_name": "deerflow",
  "manager_address": "925457@qq.com",
  "api_key": "<64hex>",
  "agent_id": "default",
  "webhook_url": "http://127.0.0.1:8001/aimail/inbound",
  "webhook_secret": "<hex>",
  "assistant_id": "lead_agent"
}
```

```json
// dsh(方案态,共享域 dsh)——通用 + dsh 扩展(session_id/preset;webhook_url =
// mail-inbound 接收端点,待实施)
{
  "email": "agent.dsh@amail.token.tm",
  "gateway_url": "https://amail.token.tm",
  "domain": "amail.token.tm",
  "system_id": "shared-token-xxxxxxxx",
  "system_name": "dsh",
  "manager_address": "925457@qq.com",
  "api_key": "<64hex>",
  "webhook_url": "http://127.0.0.1:<port>/aimail/deliver",
  "webhook_secret": "<hex>",
  "session_id": "<dsh-session-uuid>",
  "preset": "mail"
}
```

## 4. 相关约束(铁律)

- 配置文件权限 600、`~/.aimail/` 700;凭据最小化(agent 文件只存 agent key,无 admin_key)。
- 系统身份 = 指针文件唯一来源(Hermes `profiles/{name}/.aimail`、OpenClaw `~/.openclaw/.agentmail`),agentmail.json 的 system_id 与指针一致;禁 env 覆盖、禁目录扫描、禁跨系统借用。
- 读取方不做兼容别名(`amail.json` 不存在);文件名唯一 `agentmail.json`。
- 新增字段 = 各平台注册脚本写(注册链薄壳落盘),业务语义仍在共享链一处。
