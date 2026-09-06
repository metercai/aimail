# AIMail Integration Guide — 对接架构与实例示范

> 状态:修订(2026-09-06)
> 用途:后续任何 agent 系统对接 AIMail 的第一参照文档。
> 表述约定:各主题按 目标 → 方法 → 手段 → 结果 展开,只述现状,不述演变。
> 权威代码:`pysdk/`(共享核心 + 平台适配 + MCP server)、`cli/`(CLI 与脚本)、`pysdk/resources/skills/`(SKILL 源)、`cli/bin/`(运行时注册工具)。

---

## 1. 总体架构

### 1.1 目标

AIMail 与任意 agent 系统(LLM 运行时)对接,agent 获得完整邮件能力:

| 能力 | 达成形态 |
|------|----------|
| 入站 | 邮件经 gateway→bridge→agent 接收端点全链路可达,验签→共享预处理→投递 agent |
| 出站 | agent 经 send_mail 工具回信,服务端强制 sender==key.email 身份隔离 |
| 身份 | 1 agent = 1 AIMail 地址;每 agent 独立 api_key;配置单一事实源 |
| 工具 | 7 邮件工具(含 search_mail 本地全文检索)+ board 工具全暴露(进程内 registry / 平台插件 / 共享 MCP server) |
| 生命周期 | agent 创建/删除自动注册/注销;安装时全量补充注册 |
| 验收 | `aimail ping`(三阶段日志闭环)+ `aimail welcome`(含 LLM 双向)双测均过 |

### 1.2 拓扑

```
                        云端 aimail-gateway
   ┌─────────────────────────────────────────────┐
   │ SMTP 收信 → 清洗/富化 → 入站队列(pending)     │
   │ HTTP API (send/contacts/...) / A2A Board    │
   └──────────┬──────────────────────────▲────────┘
              │ pending 轮询              │ HTTP API
   ┌──────────▼──────────┐   ┌───────────┴──────────┐
   │ aimail-bridge (本机)  │──►│ agent 接收端点 (本机)  │
   │ 单进程多系统拉取      │   │ 验签 → 共享预处理       │
   │ 按路由表全 URL 转发   │   │ → 投递 agent           │
   └─────────────────────┘   └───────────▲──────────┘
                                         │ send_mail 出站
                                         ▼
                                云端 HTTP API → SMTP 投递
```

### 1.3 分层职责

| 层 | 位置 | 职责 |
|----|------|------|
| 共享核心 | `pysdk/aimail_base.py`、`aimail_tools.py`、`aimail_board.py`、`aimail_mcp_server.py` | 入站预处理链、ping/pong、地址派生、注册/注销链、邮件工具实现、board 工具 |
| 平台适配 | `pysdk/{platform}/`(hermes/openclaw/deer-flow)+ 平台侧 TS 插件(dsh/pi/openclaw,见 §4.4/§4.5/§4.2) | 配置源、persona 开关、身份注入、工具注册、接收端点 |
| 运行时 | `cli/bin/register_agent.py`、`cli/bin/deregister_agent.py` | agent 生命周期(注册/注销链入口) |
| CLI 层 | `cli/aimail`(15 子命令,仓库根 `./aimail` 符号链接)+ 运维脚本 `cli/{check_status,send_welcome,repair,setup_system,deploy_bridge,ping_test}.py`;API 客户端 `pysdk/gateway_api.py` | 安装/检查/测试/卸载/运维 |
| 安装源 | `pysdk/resources/skills/SKILL.md` + `DESCRIPTION.md` | 通用邮件技能(逐字拷贝,零改写) |

**铁律**:
- 共享代码只进 `pysdk/` 顶层;平台适配不得跨平台 import,只做三件事:平台实现、注入点赋值、注册。
- 配置单一事实源(见 1.4);禁 env 覆盖、禁目录扫描、禁跨系统借用。
- 所有平台、所有入站路径调用同一个 `process_inbound_mail`(验签后)。

### 1.4 配置单一事实源

| 配置 | 层级 | 事实内容 |
|------|------|----------|
| 指针文件(profile/.agentmail 等) | 系统身份 | system_id + email 归属 |
| `agentmail.json`(systems/{sid}/{addr}/) | 地址级 | 地址全部事实(含 webhook_url/webhook_secret 成对) |
| `aimail_gateway.json`(systems/{sid}/) | 系统级 | 系统全部事实(含 webhook_host 三态;旧名 `agentmail_gateway.json` 首次访问自动迁移) |

---

## 2. 共享核心(对接必读)

### 2.1 注入点(适配层 import 共享核心后赋值)

| 注入点 | 含义 |
|--------|------|
| `_CONFIG_LOADER` | agent 配置加载 `() -> Optional[dict]` |
| `_PROFILE_DIR_RESOLVER` | agent 目录 `() -> Optional[str]` |
| `_PERSONAS_PROVIDER` | personas 配置(无 persona 能力的平台设 `PERSONA_SUPPORTED=False`) |
| `_SOUL_PROVIDER` / `_SKILLS_PROVIDER` | board 上下文 SOUL/skills |
| `_BOARD_GATEWAY_SINK` | board 网关注册回调 |
| `PERSONA_SUPPORTED` | 能力开关(False → 归一基础地址) |
| `_AGENT_IDENTITY_OVERRIDE`(tools) | 出站身份 header X-AIMail-Agent = "platform/ver" |
| `_PERSONA_NAME_PROVIDER`(tools) | 当前 persona 名 |

### 2.2 入站单一入口(中间链铁律)

所有平台、所有入站路径(push 直推 / bridge pull 转发)调用同一个函数:

```
process_inbound_mail(payload, headers)
  1. preprocess_mail_payload()   # 身份 → persona → 富化 → 附件落盘 → 存储
  2. handle_ping_pong()          # ping/pong 拦截(全链走完才回 pong)
     → 拦截返回 None → 接收端 200 吞掉,不触发 agent
```

- ping/pong 拦截在调用 agent 前的最后一刻:pong 只在全链路正常时回复(最大化 E2E 验证)。
- 未拦截 → 接收端把原始 body(非富化产物)投递给 agent 运行时。
- `send_pong` 经 `_CONFIG_LOADER` 解析配置走 `send_mail`;日志统一 `~/.aimail/logs/aimail.{cleaned_addr}.log`。

### 2.3 地址派生(全系统统一)

```
email_for_agent(agent_id, domain, system_name, default_aliases)
```

- 默认名归一:各系统自己的默认名 → `agent`(Hermes `("default",)`、OpenClaw `("main",)`;互不替换)。
- 非法字符清洗:`.` 及其余非 atext-no-dot → `_`(无条件);空结果 → `agent`。
- 共享域:`{base}.{system_name}@{domain}`;独立域:`{base}@{domain}`(按 system_id 前缀 shared-* 判定)。

### 2.4 注册/注销链(公共,幂等)

```
register_agent_email(client, system_id, email, webhook_url, webhook_secret,
                     manager_address) -> {"api_key", "activation_code"}
deregister_agent_email(client, system_id, email, manager_address) -> {api_key, domain, whitelist}
```

- 注册参数 `webhook_url` 由 `resolve_register_webhook_url(gw, local_webhook_url)` 按 webhook_host 三态解析(§3.4);agentmail.json 落盘一律 = 本地端点。
- 注册后**必调** `register_bridge_route(system_id, email, gw, local_webhook_url)`(POST bridge /api/v1/routes,幂等 upsert)——否则 bridge 拉取后无路由,入站断链。
- manager 白名单 + domain_addr_meta 由 gateway register_address 自动创建,Python 侧不补。
- client 必须是 `aimail_tools._GatewayClient`(全方法集)。

### 2.5 身份模型

- **1 agent = 1 AIMail 地址**;每 agent 独立 api_key(gateway send.rs 强制 sender == key.email_address)。
- **系统身份 = 指针文件唯一来源**:Hermes `profiles/{name}/.aimail`、OpenClaw `~/.openclaw/.agentmail`(JSON: system_id + email)。
- 配置文件名唯一:`aimail_gateway.json`(读写两侧统一;旧名 `agentmail_gateway.json` 首次访问自动迁移,无兼容别名)。

---

## 3. 入站链路与配置规范

### 3.1 入站链路

```
云端收信 → gateway 入站队列 → bridge pull(2s 轮询 /pending)
  → 查路由表 aimail_routes.toml(email → 接收端点全 URL)
  → 透明转发(逐字节 body + 头白名单 X-AIMail-Email / X-AIMail-Timestamp / X-Webhook-Signature)
  → 接收端点:HMAC 验签(webhook_secret)→ process_inbound_mail
  → ping/pong 拦截(三阶段日志)→ 未拦截投递 agent
```

**路由表维护三入口**(保证任意时刻路由完备):
1. 注册链:新 agent 注册地址后必调 `register_bridge_route`(§2.4);
2. CLI `aimail bridge`:全量重刷(运维兜底);
3. 安装同步:平台安装流程全量注册(§4 各实例)。

### 3.2 接收端点(webhook_url = agentmail.json 唯一信任源)

| 平台 | 端点 | 预处理位置 |
|------|------|------------|
| Hermes | `http://127.0.0.1:{port}/webhooks/aimail-inbound` | 进程内(preprocessor) |
| OpenClaw | `http://127.0.0.1:18789/aimail/inbound` | OpenClaw gateway 插件端点(openclaw-aimail inbound.ts 注册,`gateway.port` 默认 18789)→ 验签 → TS `processInboundMail` → agent turn |
| DeerFlow | `http://127.0.0.1:8001/aimail/inbound` | 进程内(8001 router)→ start_run |

### 3.3 agentmail.json 字段(地址级,唯一信任源)

通用 9 字段:`email` / `gateway_url` / `domain` / `system_id` / `system_name` / `manager_address` / `api_key` / `webhook_url` / `webhook_secret`。
平台特有:`agent_id`(OpenClaw/DeerFlow)、`assistant_id`(DeerFlow)。
字段语义以 MAINTENANCE §2/§9 与代码契约为准。

### 3.4 aimail_gateway.json 字段(系统级)

`gateway_url` / `admin_key` / `system_id` / `system_name` / `save_raw_snapshots` / `domain` / `manager_address` / `webhook_host` / `system_home` / `default_agent_name`(OpenClaw)。

**webhook_host 三态**(安装时设置,决定地址注册参数):

| 状态 | 含义 | 注册参数 webhook_url |
|------|------|---------------------|
| 有合法 IP:port | 有 bridge,push 模式 | webhook_host(bridge 公网入口,云端直推) |
| 显式空 "" | 有 bridge,pull 模式 | 空(云端不回调,bridge 拉取) |
| 配置项不存在 | 无 bridge | agentmail.json 的 webhook_url(本地端点) |

### 3.5 ping/pong 契约

- 前缀:`__aimail_ping__:` / `__amail_pong__:`(gateway send.rs P0 精确匹配,两端不一致 pong 永不回环)。
- 三阶段事件:`ping_intercepted → pong_sent → pong_returned`,落 `~/.aimail/logs/aimail.{cleaned_addr}.log`(ping_test 唯一权威判定)。

---

## 4. 实例示范(五平台生产运行)

> TS 适配索引:三个 TS 平台适配器(`dsh-aimail` / `openclaw-aimail` /
> `pi-aimail`)的实现,以及为新 TS 平台写适配器的完整指南(MAIL_TOOLS
> 遍历、身份、入站链、包形态),见本仓库 `tssdk/docs/platform-adapter-guide.md`;
> `tssdk/README.md` 为 TS SDK 总览。

### 4.1 Hermes

| 组件 | 位置 |
|------|------|
| 适配层 | `pysdk/hermes/aimail_hermes.py`(注入点赋值 + 注册块;辅助 pysdk/hermes/{patch_webhook,toolsets,register_profiles,ensure_config}.py) |
| 工具注册 | 7 邮件(含 search_mail)+ 4 board 工具 → `registry.register`(import 期执行) |
| 入站 | webhook preprocessor:`register_preprocessor("aimail_gateway", core.process_inbound_mail)`(进程内) |
| 生命周期 | `profile_created/deleted` 钩子(事件总线) |
| 部署 | `aimail install --home ~/.hermes`(现行安装,取代 install-tools.sh):pysdk/install.py 展开 SKILL → profiles/*/skills/agentmail + toolsets.py 补丁 platform_toolsets + board 资源;补充注册 = pysdk/hermes/register_profiles.py(全量)+ profile_created/deleted 事件钩子 |
| 关键坑 | 每 profile 独立 webhook 端口,单入单出;webhook 会话需 `platform_toolsets.webhook` 含 aimail(否则无 send_mail) |

### 4.2 OpenClaw

| 组件 | 位置 |
|------|------|
| 适配层 | tssdk `openclaw-aimail` 插件(identity = `~/.openclaw/.agentmail` 指针 + agentmail.json 单一事实源;出站 X-AIMail-Agent = `openclaw/{ver}`) |
| 工具 | 12 邮件/board 裸名工具,插件进程内注册(MAIL_TOOLS 单一语义源;非 MCP) |
| 入站接收端 | **网关插件 HTTP 路由** `POST http://127.0.0.1:18789/aimail/inbound`(`openclaw.json gateway.port` 默认 18789;auth=plugin,桥/直推目标不变):HMAC 验签 → TS `processInboundMail` → agent turn(`subagent.run` 主 / `gateway.request` 备;多 agent 经 sessionKey 路由) |
| 生命周期 | 插件 register/deregister/status 命令;或 CLI 注册链 `cli/bin/register_agent.py`(local_webhook_url = 18789/aimail/inbound → bridge 路由) |
| 部署 | `openclaw plugins install openclaw-aimail`(或经 tssdk 包);Python 侧仅注册/检查(cli/check_status L4 探测插件端点) |
| 关键坑 | 入站处理前 `setAgentIdentity`(身份注入 TS 版);日志/事件契约与 Python 逐字对齐;8799 外置桥已退役(§9) |

### 4.3 DeerFlow

| 组件 | 位置 |
|------|------|
| 适配层 | `pysdk/deer-flow/amail_base.py`(`PERSONA_SUPPORTED=False` + 身份注入 `deerflow/{ver}`) |
| 工具 | `pysdk/amail_mcp_server.py` 共享 MCP stdio server(经 cli/deer-flow/install-mcp.sh 安装) |
| 入站 | **进程内预处理**:deer-flow `backend/app/gateway/routers/aimail_inbound.py` — `POST /aimail/inbound`:验签 → process_inbound_mail → ping/pong 拦截 → `start_run` 投递(thread=uuid5("amail", email),assistant_id 读 agentmail.json) |
| 生命周期 | `pysdk/deer-flow/manage.py`(register/reconcile/deregister 子命令;原 scripts/deer-flow/{register_agent,reconcile,deregister_agent}.py + install-inbound.sh 于 2026-09-02 聚合于此);安装补充注册 = manage.py reconcile(全量)+ cli/deer-flow/install-skill.sh / install-mcp.sh |
| 部署 | 共享布局(~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json);入站安装/补丁经 `pysdk/deer-flow/manage.py install/patch`(捆绑安装 + app.py 双锚点 patch + py_compile 校验;上游仓保持干净,安装后重启 8001 生效) |
| 关键坑 | 8001 进程内 import amail_base 需 sys.path 注入(router 模块级);Pyright 误报(运行时路径已插入) |

### 4.4 DSH(deepseek-harness,TS 插件平台)

| 组件 | 位置 |
|------|------|
| 适配层 | tssdk `dsh-aimail` 插件(3 子包:mail-service / tools / inbound;identity = `~/.dsh/.agentmail` 指针;preset = 定义 / uuid = 实例) |
| 工具 | 12 邮件/board 裸名工具(preset 层注册,joined session 可见;出站 X-AIMail-Agent = `dsh/{ver}`) |
| 入站 | host 层 `mail-inbound`:node:http listener(`POST /aimail/inbound`,默认端口 `AIMAIL_INBOUND_PORT`/9099)→ HMAC 验签 → TS `processInboundMail` → `followup` 唤醒对应 session |
| 生命周期 | `cli/dsh/bind_agent.py` / `unbind_agent.py` + 共享注册链(注册后必调 register_bridge_route) |
| 部署 | `dsh plugin --profile web add dsh-aimail`(bundle 经 cordis.patch.yml 自挂载) |
| 关键坑 | persona 关闭(`PERSONA_SUPPORTED=False`,dsh-persona 同名不同义);多 session 隔离由网关 `sender==key.email` 兜底;契约逐字对齐 Python |


### 4.5 pi(TS 扩展平台)

| 组件 | 位置 |
|------|------|
| 适配层 | tssdk `pi-aimail` 扩展(identity = `~/.pi/.agentmail` 指针 + agentmail.json) |
| 工具 | 12 邮件/board 裸名工具(`pi.registerTool`,TypeBox 参数;出站 X-AIMail-Agent = `pi/{ver}`) |
| 入站 | 扩展自有本地 listener `http://127.0.0.1:9101/aimail/inbound`(默认端口 9101;bridge push 目标)→ HMAC 验签 → TS `processInboundMail` → `pi.sendUserMessage`(必触发 turn) |
| 生命周期 | `~/.pi/.agentmail` 指针 + 共享注册链(与 openclaw 同构);安装补充注册见 cli/check_status pi adapter |
| 部署 | 拷贝/符号链接 → `~/.pi/agent/extensions/`(或 pi 包);board 资源幂等展开 |
| 关键坑 | pi 无 HTTP 路由注册 → listener 端口须可达(webhook_url = 该端点,bridge 路由全 URL) |

### 4.6 对比(新系统选型参考;DSH/pi 速览见 §4.4/§4.5)

| 维度 | Hermes | OpenClaw | DeerFlow |
|------|--------|----------|----------|
| 入站模型 | 单入单出(每 profile 独立端口,进程内预处理) | 网关插件路由 `/aimail/inbound`(进程内,多 agent 经 sessionKey) | 进程内预处理(8001 router,start_run 投递) |
| 工具暴露 | 进程内 registry | 插件进程内裸名(13 工具) | MCP stdio server(amail__ 前缀) |
| 部署 | copy-deploy(`aimail install` 驱动) | TS 插件(`openclaw plugins install openclaw-aimail`) | 适配层 repo-direct;预处理在 deer-flow 仓(补丁安装 + 重启) |
| 生命周期 | 事件总线(profile_created/deleted) | 插件 register 命令 / CLI 注册链 | manage.py reconcile 对账 |
| persona | 全能力(PERSONA_SUPPORTED=True) | 无(False) | 无(False) |

---

## 5. CLI 契约(cli/aimail)

**命令名冲突警告**:`~/.local/bin/aimail` 是 Hermes 启动器,repo 的 CLI 只能经仓库根 `./aimail` 运行(符号链接 → `cli/aimail`),不得把 cli/ 加进全局 PATH。

子命令(15 个,按场景分 4 组):

- **setup**:`init` `install` `uninstall` `reset`
- **operate**:`stats` `renew` `version`
- **diagnose**:`check` `repair` `ping` `welcome` `persona`
- **resources**:`domain` `mailname` `bridge`

| 子命令 | 职责 |
|--------|------|
| `bridge` | 本机 bridge 维护:无参=状态;`--system-id` 重刷路由;`--restart` 单实例重启 |
| `check` | 全链路状态检查(L1 gateway/L2 bridge/L3 agent 配置/L4 hook/L5 ping-pong) |
| `domain` | 查看/创建系统域名(list 默认 / `--add DOMAIN`) |
| `init` | 一次性机器初始化:锁定 gateway URL、直连或 bridge 模式 |
| `install` | 集成 agent 平台到 AIMail 系统(激活或复用现有系统,含平台适配与补充注册) |
| `mailname` | 默认主 agent 名映射查看/修改(hermes default→agent;openclaw main→agent) |
| `persona` | persona 流程:manager 发 'update persona',agent 回草稿 |
| `ping` | ping-pong 闭环测试(只信 agent 侧三阶段日志事件) |
| `repair` | 按 check 结果自动修复(幂等),再复检 |
| `renew` | 用产品码续期系统,或只读查看到期 |
| `reset` | 重置连接配置(admin-key 路径,业务字段零变化) |
| `stats` | 本机对接状态(系统/agent/邮件统计,只读) |
| `uninstall` | 卸载(网关注销 + 平台清理 + 本地数据) |
| `version` | 显示 CLI/引导版本(升级检测) |
| `welcome` | welcome 端到端测试(含 LLM 双向;最终验收,与 `ping` 双测并列) |

**平台推断(无 --agent-type)**:`--home` 目录特征按序判定——`pi`(`~/.pi` + agent/)、`dsh`(`~/.dsh` + profiles/ + storages/;dsh 也有 profiles/,须在 hermes 特征前)、`hermes`(hermes-agent/ 或 profiles/)、`openclaw`(openclaw.json)、`deerflow`(backend/app/gateway)→ 配置 system_home 反查 → 自动探测指针。

**.env 自动加载**:CLI 参数 > shell env > .env > 内置默认。.env 键:AIMAIL_URL / AIMAIL_ADMIN_KEY / AIMAIL_PRODUCT_CODE / AIMAIL_MANAGER_ADDRESS / AIMAIL_SYSTEM_NAME / AIMAIL_DOMAIN / AIMAIL_SAVE_SNAPSHOTS / AIMAIL_WEBHOOK_HOST。
install 全非交互:激活 → 从 setup_system JSON stdout 取 server 分配的 system_id → domain 预置/创建 → deploy_bridge → 平台适配。

---

## 6. 新 agent 系统对接清单(8 步)

1. **建共享层引用**:import `pysdk/aimail_base` / `aimail_tools` / `aimail_board`(sys.path 插入 pysdk/);不复制、不改共享代码。
2. **写适配层** `pysdk/<system>/<adapter>.py`(或平台侧 TS 插件):平台三件事(配置源 / personas 或 `PERSONA_SUPPORTED=False` / 身份注入 `_AGENT_IDENTITY_OVERRIDE = "platform/ver"`)+ 赋值注入点(§2.1)。
3. **暴露工具**:进程内 registry(照 Hermes)、平台插件(照 DSH/pi/OpenClaw TS)或直接复用共享 `pysdk/amail_mcp_server.py`(平台无关,按共享布局落 agentmail.json 即可)。
4. **接入站**:接收端点先注入 agent 配置(set_agent_context 等价物)→ 验签 → `process_inbound_mail` → 未拦截投递原始 body;入站拉取复用 aimail-bridge,不写新 poller。
5. **接生命周期**:有事件总线 → 挂钩子;无 → 包装 agents add/delete CLI 调共享注册/注销链。
6. **装 skill**:逐字拷贝 `pysdk/resources/skills/SKILL.md`(+ DESCRIPTION.md),零改写。
7. **注册到 CLI**:cli/check_status.py `PLATFORMS` 注册表加 adapter(detect/list_agents/check_config/check_hook 四函数);install/uninstall 平台适配段加分支(含安装补充注册)。
8. **验收(双测铁律)**:`aimail check` 全绿 → `aimail ping` 三阶段闭环 → `aimail welcome` 管理员收到 Re: 回复(带头 `X-AIMail-Agent: {platform}/{version}`)。

---

## 7. 安全模型

- **最小权限 key**:每 agent 独立 api_key;SMTP auth.local 认证只接受 agent 自己的 key(无 admin_key 回退);ping_test 的 pending 轮询用 system scope admin_key。
- **指针唯一来源**:系统身份 = 指针文件;禁扫描、禁 env 覆盖、禁跨系统借用。
- **安全论证铁律**:分析攻击面与杠杆;只增复杂度不减少攻击面的缓解是伪优化。
- **出站自定义头白名单**:X-AIMail-Agent / X-Board-Members / X-AIMail-AutoReply 外发透传;X-Board-ID/Role 仅内转;_persona.* 内部专用。

---

## 8. 对接排障速查

| 症状 | 根因 |
|------|------|
| ping 永不回 pong | 前缀不一致(PONG_PREFIX 必须 `__amail_pong__:`);或接收端没走 process_inbound_mail 最后一步 |
| 入站断链(新 agent) | 注册后未调 register_bridge_route(路由表无条目) |
| webhook 会话收得到回不出 | profile `platform_toolsets.webhook` 缺 aimail;或路由 skills 为空 |
| 日志落 aimail.default.log | 独立进程没 set_agent_context / 没 export AIMAIL_AGENT_EMAIL |
| bridge 转发 401 无限重试 | webhook_secret 与接收端配置不一致(注册时落盘值) |
| 入站富化跳过 | 接收端未先注入 agent 配置就调 process_inbound_mail |
| check 报系统缺失 | 指针文件缺 system_id;或读错了 home(profile 布局须 --agent-home) |
| MCP 连接挂起 | 帧协议不对:MCP SDK 用 newline JSON,不是 Content-Length |
| agent 回复带错平台身份 | 适配层未注入 _AGENT_IDENTITY_OVERRIDE(目录检测误判) |

---

## 9. 已退役/勿用

- **amail-poll.py**:已删除。入站 pull 统一走 aimail-bridge(单进程多系统)。
- **amail_deerflow_bridge.py**(8798):已退役。DeerFlow 入站为 8001 进程内预处理。
- **amail_openclaw_bridge.py**(8799/hook 外置预处理进程):已退役。OpenClaw 入站为 gateway 插件端点 `http://127.0.0.1:18789/aimail/inbound`(openclaw-aimail 插件,与 cli/check_status 注释、cli/bin/register_agent.py 一致)。
- **integrate.sh / uninstall.sh / bridge-ctl.sh / install-tools.sh**:已被 `aimail install/uninstall/bridge` 取代(install-tools.sh 亦被 pysdk/hermes/toolsets.py 工具集补丁取代)。
- **agentmail_gateway.json(旧名,2026-09-04 前)**:读写统一 `aimail_gateway.json`;旧名首次访问自动迁移,无兼容别名。
- **--agent-type 参数**:平台事实推断,禁止手动指定。
- **mode / bridge_port 配置项**:webhook_host 三态表达 push/pull;接收端点端口在 webhook_url。
- **docs/ 目录**:正式文档目录(版本化,随仓库维护);接口权威口径见 MAINTENANCE.md、README.md。
