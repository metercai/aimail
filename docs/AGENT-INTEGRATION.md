# AgentMail Integration Guide — 对接架构与实例示范

> 状态:定稿(2026-08-18,完整审计 + 三平台生产实证)
> 用途:后续任何 agent 系统对接 AgentMail 的第一参照文档。
> 表述约定:各主题按 目标 → 方法 → 手段 → 结果 展开,只述现状,不述演变。
> 权威代码:`tools/`(共享核心 + 平台适配)、`bin/`(运行时)、`scripts/`(CLI 层)、`skills/`(安装源)。
> 配套文档:`AGENTMAIL-JSON-REFERENCE.md`(配置文件字段权威参考)。

---

## 1. 总体架构

### 1.1 目标

AgentMail 与任意 agent 系统(LLM 运行时)对接,agent 获得完整邮件能力:

| 能力 | 达成形态 |
|------|----------|
| 入站 | 邮件经 gateway→bridge→agent 接收端点全链路可达,验签→共享预处理→投递 agent |
| 出站 | agent 经 send_mail 工具回信,服务端强制 sender==key.email 身份隔离 |
| 身份 | 1 agent = 1 amail 地址;每 agent 独立 api_key;配置单一事实源 |
| 工具 | 6 邮件工具 + board 工具全暴露(进程内 registry 或共享 MCP server) |
| 生命周期 | agent 创建/删除自动注册/注销;安装时全量补充注册 |
| 验收 | `agentmail ping`(三阶段日志闭环)+ `agentmail welcome`(含 LLM 双向)双测均过 |

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
| 共享核心 | `tools/aimail_base.py`、`aimail_tools.py`、`aimail_board.py` | 入站预处理链、ping/pong、地址派生、注册/注销链、邮件工具实现、board 工具 |
| 平台适配 | `tools/{platform}/` | 配置源、persona 开关、身份注入、工具注册、接收端点 |
| 运行时 | `bin/register_agent.py`、`deregister_agent.py` | agent 生命周期 |
| CLI 层 | `scripts/agentmail`(10 子命令)+ `scripts/gateway_api.py` | 安装/检查/测试/卸载/运维 |
| 安装源 | `skills/SKILL.md` + `DESCRIPTION.md` | 通用邮件技能(逐字拷贝,零改写) |

**铁律**:
- 共享代码只进 `tools/` 顶层;平台适配不得跨平台 import,只做三件事:平台实现、注入点赋值、注册。
- 配置单一事实源(见 1.4);禁 env 覆盖、禁目录扫描、禁跨系统借用。
- 所有平台、所有入站路径调用同一个 `process_inbound_mail`(验签后)。

### 1.4 配置单一事实源

| 配置 | 层级 | 事实内容 |
|------|------|----------|
| 指针文件(profile/.agentmail 等) | 系统身份 | system_id + email 归属 |
| `agentmail.json`(systems/{sid}/{addr}/) | 地址级 | 地址全部事实(含 webhook_url/webhook_secret 成对) |
| `agentmail_gateway.json`(systems/{sid}/) | 系统级 | 系统全部事实(含 webhook_host 三态) |

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
- `send_pong` 经 `_CONFIG_LOADER` 解析配置走 `send_mail`;日志统一 `~/.agentmail/logs/agentmail.{cleaned_addr}.log`。

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

- **1 agent = 1 amail 地址**;每 agent 独立 api_key(gateway send.rs 强制 sender == key.email_address)。
- **系统身份 = 指针文件唯一来源**:Hermes `profiles/{name}/.agentmail`、OpenClaw `~/.openclaw/.agentmail`(JSON: system_id + email)。
- 配置文件名唯一:`agentmail_gateway.json`(读写两侧统一,无兼容别名)。

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
2. CLI `agentmail bridge`:全量重刷(运维兜底);
3. 安装同步:平台安装流程全量注册(§4 各实例)。

### 3.2 接收端点(webhook_url = agentmail.json 唯一信任源)

| 平台 | 端点 | 预处理位置 |
|------|------|------------|
| Hermes | `http://127.0.0.1:{port}/webhooks/agentmail-inbound` | 进程内(preprocessor) |
| OpenClaw | `http://127.0.0.1:8799/hook` | 外部预处理进程(amail_openclaw_bridge.py)→ dispatch_to_hooks |
| DeerFlow | `http://127.0.0.1:8001/agentmail/inbound` | 进程内(8001 router)→ start_run |

### 3.3 agentmail.json 字段(地址级,唯一信任源)

通用 9 字段:`email` / `gateway_url` / `domain` / `system_id` / `system_name` / `manager_address` / `api_key` / `webhook_url` / `webhook_secret`。
平台特有:`agent_id`(OpenClaw/DeerFlow)、`assistant_id`(DeerFlow)。
字段逐项说明见 `AGENTMAIL-JSON-REFERENCE.md`。

### 3.4 agentmail_gateway.json 字段(系统级)

`gateway_url` / `admin_key` / `system_id` / `system_name` / `save_raw_snapshots` / `domain` / `manager_address` / `webhook_host` / `system_home` / `default_agent_name`(OpenClaw)。

**webhook_host 三态**(安装时设置,决定地址注册参数):

| 状态 | 含义 | 注册参数 webhook_url |
|------|------|---------------------|
| 有合法 IP:port | 有 bridge,push 模式 | webhook_host(bridge 公网入口,云端直推) |
| 显式空 "" | 有 bridge,pull 模式 | 空(云端不回调,bridge 拉取) |
| 配置项不存在 | 无 bridge | agentmail.json 的 webhook_url(本地端点) |

### 3.5 ping/pong 契约

- 前缀:`__agentmail_ping__:` / `__amail_pong__:`(gateway send.rs P0 精确匹配,两端不一致 pong 永不回环)。
- 三阶段事件:`ping_intercepted → pong_sent → pong_returned`,落 `~/.agentmail/logs/agentmail.{cleaned_addr}.log`(ping_test 唯一权威判定)。

---

## 4. 实例示范(三平台生产运行)

### 4.1 Hermes

| 组件 | 位置 |
|------|------|
| 适配层 | `tools/hermes/aimail_hermes.py`(注入点赋值 + 注册块) |
| 工具注册 | 6 邮件 + 5 board 工具 → `registry.register`(import 期执行) |
| 入站 | webhook preprocessor:`register_preprocessor("agentmail_gateway", core.process_inbound_mail)`(进程内) |
| 生命周期 | `profile_created/deleted` 钩子(事件总线) |
| 部署 | copy-deploy:install-tools.sh 拷贝 4 文件 → $HERMES_DIR/tools/,SKILL → profiles/{p}/skills/agentmail/;安装补充注册 = register_profiles.py(全量)+ gateway.sh 路由段 |
| 关键坑 | 每 profile 独立 webhook 端口,单入单出;webhook 会话需 `platform_toolsets.webhook` 含 agentmail(否则无 send_mail) |

### 4.2 OpenClaw

| 组件 | 位置 |
|------|------|
| 适配层 | `tools/openclaw/amail_base.py`(`PERSONA_SUPPORTED=False` + 身份注入 + 转发共享函数) |
| 工具 | `tools/amail_mcp_server.py` 共享 MCP stdio server(`amail__*`,newline-JSON 帧协议) |
| 入站接收端 | `tools/openclaw/amail_openclaw_bridge.py` — HTTP `/hook`:验签 → set_agent_context → process_inbound_mail → dispatch_to_hooks(POST 127.0.0.1:18789/hooks/agent,agentId/sessionKey 区分多 agent) |
| 生命周期 | CLI 包装:`bin/register_agent.py`(agents list 发现 → 注册链 → bridge 路由注册);安装补充注册 = register_agent.py --all |
| 部署 | repo-direct(改立即生效);skill 经 install-skill.sh 拷贝 |
| 关键坑 | 接收端必须先 set_agent_context 再调 process_inbound_mail;set_agent_context 需 export AIMAIL_AGENT_EMAIL(否则日志落 default.log) |

### 4.3 DeerFlow

| 组件 | 位置 |
|------|------|
| 适配层 | `tools/deer-flow/amail_base.py`(`PERSONA_SUPPORTED=False` + 身份注入 `deerflow/{ver}`) |
| 工具 | `tools/amail_mcp_server.py` 共享 MCP stdio server |
| 入站 | **进程内预处理**:deer-flow `backend/app/gateway/routers/aimail_inbound.py` — `POST /agentmail/inbound`:验签 → process_inbound_mail → ping/pong 拦截 → `start_run` 投递(thread=uuid5("amail", email),assistant_id 读 agentmail.json) |
| 生命周期 | `scripts/deer-flow/reconcile.py`(对账)+ register_agent.py(即时注册);安装补充注册 = register_agent.py --all + install-inbound.sh + skill/mcp |
| 部署 | 共享布局(~/.agentmail/systems/{sid}/{cleaned_addr}/agentmail.json);入站补丁经 `scripts/deer-flow/install-inbound.sh` 幂等安装(拷贝 + app.py 双锚点 patch + py_compile 校验;上游仓保持干净,安装后重启 8001 生效) |
| 关键坑 | 8001 进程内 import amail_base 需 sys.path 注入(router 模块级);Pyright 误报(运行时路径已插入) |

### 4.4 对比(新系统选型参考)

| 维度 | Hermes | OpenClaw | DeerFlow |
|------|--------|----------|----------|
| 入站模型 | 单入单出(每 profile 独立端口,进程内预处理) | 单入多出(一个 hooks 端点路由多 agent,外部预处理进程) | 进程内预处理(8001 router,start_run 投递) |
| 工具暴露 | 进程内 registry | MCP stdio server(amail__ 前缀) | MCP stdio server(amail__ 前缀) |
| 部署 | copy-deploy(改后重跑 install) | repo-direct(改立即生效) | 适配层 repo-direct;预处理在 deer-flow 仓(补丁安装 + 重启) |
| 生命周期 | 事件总线(profile_created/deleted) | CLI 包装 | reconcile 对账 |
| persona | 全能力(PERSONA_SUPPORTED=True) | 无(False) | 无(False) |

---

## 5. CLI 契约(scripts/agentmail)

**命令名冲突警告**:`~/.local/bin/agentmail` 是 Hermes 启动器,repo 的 CLI 只能经仓库根 `./agentmail` 运行,不得把 scripts/ 加进全局 PATH。

子命令(字母序,10 个):

| 子命令 | 职责 |
|--------|------|
| `bridge` | 本机 bridge 维护:无参=状态;`--system-id` 重刷路由;`--restart` 单实例重启 |
| `check` | 全链路状态检查(L1 gateway/L2 bridge/L3 agent 配置/L4 hook/L5 ping-pong) |
| `domain` | 查看/创建系统域名(list 默认 / `--add DOMAIN`) |
| `install` | 非交互安装(激活→domain 预置→bridge 部署→平台适配含补充注册) |
| `mailname` | 默认主 agent 名映射查看/修改(hermes default→agent;openclaw main→agent) |
| `ping` | ping-pong 闭环测试(只信 agent 侧三阶段日志事件) |
| `reset` | 重置配置(admin-key 路径,业务字段零变化) |
| `stats` | 本机对接状态(系统/agent/邮件统计,只读) |
| `uninstall` | 卸载(网关注销 + 平台清理 + 本地数据) |
| `welcome` | welcome 端到端测试(含 LLM,唯一验收) |

**平台推断(无 --agent-type)**:`--home` 目录特征(hermes-agent/profiles = hermes;openclaw.json = openclaw;backend/app/gateway = deerflow)→ 配置 system_home 反查 → 自动探测指针。

**.env 自动加载**:CLI 参数 > shell env > .env > 内置默认。.env 键:AIMAIL_URL / AIMAIL_ADMIN_KEY / AIMAIL_PRODUCT_CODE / AIMAIL_MANAGER_ADDRESS / AIMAIL_SYSTEM_NAME / AIMAIL_DOMAIN / AIMAIL_SAVE_SNAPSHOTS / AIMAIL_WEBHOOK_HOST。
install 全非交互:激活 → 从 setup_system JSON stdout 取 server 分配的 system_id → domain 预置/创建 → deploy_bridge → 平台适配。

---

## 6. 新 agent 系统对接清单(8 步)

1. **建共享层引用**:import `tools/aimail_base` / `aimail_tools` / `aimail_board`(sys.path 插入 tools/);不复制、不改共享代码。
2. **写适配层** `tools/<system>/<adapter>.py`:平台三件事(配置源 / personas 或 `PERSONA_SUPPORTED=False` / 身份注入 `_AGENT_IDENTITY_OVERRIDE = "platform/ver"`)+ 赋值注入点(§2.1)。
3. **暴露工具**:进程内 registry(照 Hermes)或直接复用共享 `tools/amail_mcp_server.py`(平台无关,按共享布局落 agentmail.json 即可)。
4. **接入站**:接收端点先注入 agent 配置(set_agent_context 等价物)→ 验签 → `process_inbound_mail` → 未拦截投递原始 body;入站拉取复用 aimail-bridge,不写新 poller。
5. **接生命周期**:有事件总线 → 挂钩子;无 → 包装 agents add/delete CLI 调共享注册/注销链。
6. **装 skill**:逐字拷贝 `skills/SKILL.md`(+ DESCRIPTION.md),零改写。
7. **注册到 CLI**:check_status.py `PLATFORMS` 注册表加 adapter(detect/list_agents/check_config/check_hook 四函数);install/uninstall 平台适配段加分支(含安装补充注册)。
8. **验收(双测铁律)**:`agentmail check` 全绿 → `agentmail ping` 三阶段闭环 → `agentmail welcome` 管理员收到 Re: 回复(带头 `X-AIMail-Agent: {platform}/{version}`)。

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
| webhook 会话收得到回不出 | profile `platform_toolsets.webhook` 缺 agentmail;或路由 skills 为空 |
| 日志落 agentmail.default.log | 独立进程没 set_agent_context / 没 export AIMAIL_AGENT_EMAIL |
| bridge 转发 401 无限重试 | webhook_secret 与接收端配置不一致(注册时落盘值) |
| 入站富化跳过 | 接收端未先注入 agent 配置就调 process_inbound_mail |
| check 报系统缺失 | 指针文件缺 system_id;或读错了 home(profile 布局须 --agent-home) |
| MCP 连接挂起 | 帧协议不对:MCP SDK 用 newline JSON,不是 Content-Length |
| agent 回复带错平台身份 | 适配层未注入 _AGENT_IDENTITY_OVERRIDE(目录检测误判) |

---

## 9. 已退役/勿用

- **amail-poll.py**:已删除。入站 pull 统一走 aimail-bridge(单进程多系统)。
- **amail_deerflow_bridge.py**(8798):已退役。DeerFlow 入站为 8001 进程内预处理。
- **integrate.sh / uninstall.sh / bridge-ctl.sh**:已被 `agentmail install/uninstall/bridge` 取代。
- **aimail_gateway.json 旧名**:统一 `agentmail_gateway.json`,无兼容别名。
- **--agent-type 参数**:平台事实推断,禁止手动指定。
- **mode / bridge_port 配置项**:webhook_host 三态表达 push/pull;接收端点端口在 webhook_url。
- **docs/ 目录**:本地草稿区,不版本化;正式文档落仓库根(本文件 + README/MAINTENANCE)。
