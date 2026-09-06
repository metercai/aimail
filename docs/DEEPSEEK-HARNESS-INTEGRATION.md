# DeepSeek Harness 集成方案(aimail ↔ deepseek-harness)

> 状态:**方案定稿**(2026-08-18,经三集成沉淀审计修订)
> 参照:[AGENT-INTEGRATION_zh.md](AGENT-INTEGRATION_zh.md)(AIMail 对接适配指导,修订 2026-09-06;英文版同目录 AGENT-INTEGRATION.md)
> 参照实例:Hermes(参考实现,Python toolset)→ OpenClaw(第二实例,MCP)→ DeerFlow(第三实例,进程内预处理)→ **deepseek-harness(第四实例,首个 TS 平台)**
> 契约基准:[DSH-PREPROCESS-CONTRACT.md](../DSH-PREPROCESS-CONTRACT.md)(TS 预处理逐行对照表,P0 产出)

---

## 1. deepseek-harness 架构事实(调研结论)

| 项 | 事实 | 来源 |
|----|------|------|
| 定位 | DeepSeek AI 开源 agent harness,基于 vendored **Cordis**,"一切都是插件"——model adapter/工具注册表/session log/agent loop 全是可替换插件,无特权核心 | README / AGENTS.md |
| 运行时 | Node/TypeScript(ESM,`@deepseek-ai/dsh-*` workspace),`node ^22.19 || >=24` | AGENTS.md |
| 工具注册 | `ctx.tools.register(defineTool({name,description,parameters,output,execute}))`,schema 自动流入 system-prompt;**原生工具名 = 裸名**;也接受裸 JSON-Schema | docs/cookbook/adding-a-tool.md |
| 入站 HTTP | 无统一外部 webhook 入口;可自建 `node:http` listener(profile 无关,headless 可用) | packages/host/webserver/README.md |
| 消息注入 | `agent.followup(msg)`(唤醒)、`agent.steer(msg)`、`agent.inject(msg)`(不唤醒);`createUserMessage(...)` 来自 `@deepseek-ai/dsh-llm` | packages/core/agent/README.md |
| 凭据 | `ctx.credentials.resolve(credentialRef('NAME'))`——config 只存引用不存秘密 | packages/credentials/credentials/README.md |
| email | **完全没有**(grep 证据:`send_mail`→0、`smtp|imap`→8 全误报) | 本仓库 grep(2026-08-14) |

## 2. 多 agent 判定:preset = 定义,uuid = 实例

| | aimail(Hermes/OpenClaw/DeerFlow) | dsh |
|---|---|---|
| agent 定义(角色) | 与身份融合(profile/agentId) | **preset**(1:N 共享) |
| agent 实例(身份) | 同一实体 | **uuid / SessionId**(N 个实例) |
| 邮箱地址 | 绑在 agent 上 | **绑在 uuid 上**(每实例一个地址) |
| 角色/persona | 同一实体属性 | **绑在 preset 上** |

身份单元 = `anonymous-user-id`(每 home 一个匿名 UUID,仅 telemetry,不可寻址);无 soul 文件;skills 全局 + per-preset 分层,无 per-agent skill 目录。**集成分层**:amail 的"agent 定义"(工具 + SKILL + persona)落在 **preset**,amail 的"实例身份"(地址 + key)落在 **uuid**。

## 3. 插件拓扑(4 包,分三层)

```
packages/email/mail-core        # 共享 TS 库(框架无关,零 Cordis 依赖)
                                #   gateway client + 12 工具函数 + 入站预处理全链(TS)
                                #   + agentmail.json 读取(session_id/preset/webhook)
                                #   → 未来 OpenClaw 等 TS agent 直接 import
packages/email/mail             # dsh 适配(host 层):ctx.mail 服务(gateway client + 配置 + credentials),包装 mail-core
packages/email/tool-mail        # dsh 适配(preset 层):12 defineTool(裸名),包装 mail-core
packages/email/mail-inbound     # dsh 适配(host 层):node:http listener + 验签 + 预处理 + ping/pong + followup
```

- **host 层**(`mail` + `mail-inbound`,全局一次):配置跨 session 共享,listener 服务所有 session。
- **preset 层**(`tool-mail` + SKILL):一个「mail agent」preset 才暴露 12 工具;代码 agent preset 不暴露。
- **uuid 层**(agentmail.json):每个 session 实例一个地址 + key(session_id/preset 字段落盘)。

## 4. 与既有三实例的异同(对接合理性评估)

### 4.1 工具面:Hermes 路线(进程内裸名),语言 TS

| | Hermes | OpenClaw | DeerFlow | dsh |
|---|---|---|---|---|
| 工具载体 | Python 进程内 registry | 外部 MCP stdio | 外部 MCP stdio | **TS 进程内 Cordis 插件** |
| 工具名 | `send_mail`(裸名) | `amail__send_mail` | `amail__send_mail` | **`send_mail`(裸名)** |
| SKILL.md | 逐字复用 | 逐字复用 | 逐字复用 | **逐字复用(零改写)** |

业务逻辑在 **gateway HTTP API**(API 是契约),TS 薄封装不产生业务分叉。「TS 壳掉 Python 瓤」三种形态(MCP / 每调用 spawn / 长驻 Python 服务)均已评估否决——T1 全 TS。

### 4.2 入站:TS 进程内预处理(仿 DeerFlow 8001 / Hermes)

dsh 是 Node,预处理全链由 **mail-core TS 实现**(契约逐字对齐 Python,基准见 DSH-PREPROCESS-CONTRACT.md)。`mail-inbound` = node:http 端点,直接承载 验签 → 预处理 → ping/pong → followup。**无外部 Python bridge 进程**(DeerFlow 8798 模式已退役,不重蹈)。

入站 pull:复用 aimail-bridge(`[pull].systems` 数组加 dsh 条目 + 路由表 `email → dsh 端点全 URL`),不写 poller。

### 4.3 身份:preset=定义 / uuid=实例(见 §2)

### 4.4 合理性总评

- **可行**:dsh 扩展点完整覆盖 aimail 组件需求,无一处改 dsh 核心或 aimail-gateway(gateway 零改动铁律)。
- **新增成本**:mail-core(TS:gateway client + 12 工具 + 预处理全链)+ 3 个 dsh 适配包 + bind/unbind 脚本 + CLI 集成。
- **预期结果**:dsh 上的 mail-agent(preset 定义 + uuid 实例)获得与 Hermes 同等的邮件能力——出站 12 工具裸名可用;入站 bridge 转发 → 验签 → TS 预处理 → followup 唤醒对应 session。

## 5. 细化实现方案(文件级)

### 5.1 已定决策

| 项 | 决策 |
|----|------|
| 入站 | dsh 侧 `mail-inbound` node:http listener 直接承载:验签 + **TS 进程内预处理** + ping/pong + followup(仿 DeerFlow 8001;无外部 Python bridge) |
| 后端 | 复用 aimail-gateway(零改动) |
| 工具集 | 全部 12 工具,全 TS 重写(T1),业务逻辑由 gateway 保障 |
| 共享库 | `mail-core`(框架无关,零 Cordis 依赖),OpenClaw 未来复用、本次不迁 |
| 工具/skill 层级 | **preset 层**(agent 定义);绑定 **uuid 层**(实例身份) |
| 绑定 | **并入 agentmail.json**(session_id/preset 字段;api_key 不重复存,凭据唯一权威仍为 agentmail.json)——无独立 binding.json 文件 |
| persona | 不启用(`PERSONA_SUPPORTED=False`——dsh-persona 是提示词身份,与 amail 地址角色路由同名不同义) |
| 入站 pull | **aimail-bridge 复用**(不写 poller;`[pull].systems` + 路由表全 URL) |
| 配置规范 | agentmail.json 落盘 webhook_url(本地接收端点)+ webhook_secret(成对,唯一信任源);注册参数按 webhook_host 三态(resolve_register_webhook_url 契约) |
| 路由注册 | 注册地址后**必调** register_bridge_route(POST bridge /api/v1/routes,port=80 占位)——否则入站断链 |
| 安装补充注册 | CLI PLATFORMS 注册表(detect/list_agents/check_config/check_hook)+ install 平台分支全量注册已有 agent |

### 5.2 dsh 侧新包(deepseek-harness 仓库,`packages/email/`)

```
packages/email/mail-core/        # 共享 TS 库(框架无关):gateway client + 12 工具函数
                                 #   + 入站预处理全链(TS,契约基准见 DSH-PREPROCESS-CONTRACT.md)
                                 #   + agentmail.json 读取/写回(原子 tmp+replace)
packages/email/mail/             # host 层:ctx.mail 服务(配置加载 + credentials),包装 mail-core
packages/email/mail-inbound/     # host 层:node:http listener + 验签 + 预处理 + ping/pong + followup
packages/email/tool-mail/        # preset 层:12 defineTool(裸名),包装 mail-core
```

**12 工具清单(与 OpenClaw MCP server 逐字对齐,已验证)**:
- 邮件 6:`send_mail` / `manage_contacts` / `contact_profile` / `set_contact_profile` / `email_summary` / `set_email_summary`
- 看板 6:`board_status` / `board_task_list` / `board_task_show` / `board_heartbeat` / `board_members` / `set_public_whoami`

每个 `defineTool`(对照 `packages/shell/tool-bash` 模板):`parameters`(模型入参,自动校验)→ `output.schema`+`output.render` → `execute(args, exec)` 内调 `mail-core` 工具函数;出站注入 `X-AIMail-Agent: dsh/{version}`(只报真实检测结果);凭证 `ctx.credentials.resolve(credentialRef('AIMAIL_*'))`。

**身份解析**:`tool-mail` 在 preset 层注册(`agent.cordis.yml` 加 `dsh-mail` + `dsh-tool-mail` 行),所有 joined session 可见;每次 `execute` 用 `exec.agent.id`(SessionId)查 agentmail.json 解析当前 session 的地址+key。多 session 隔离由 gateway server 端 `sender==key.email` 兜底。persona 关闭 → `my_amail_addr` 直接用绑定地址。

### 5.3 入站链路(dsh 侧 TS 插件)

```
aimail-gateway → pending → aimail-bridge pull → 路由表 email → http://127.0.0.1:<port>/aimail/deliver
→ dsh mail-inbound(node:http):
  → HMAC 验签(webhook_secret)→ TS 预处理全链(mail-core,契约对齐)
    → ping/pong 拦截(三阶段日志)→ 未拦截:
      → 按 session_id(agentmail.json)查 ctx.agents.get(session_id)(live)
        → agent.followup(createUserMessage(...))
        → 冷: ctx.agents.resume({session_id})(需 sessionPersistence 已配)→ followup
  → 200 回执(bridge 收 200 才 ack gateway pending)
```

### 5.4 地址绑定(注册链 → agentmail.json 落盘)

```
bind(一次性,bind_agent.py):
  1. 选/建 preset:一个「mail agent」preset(agent.cordis.yml 含 dsh-mail + dsh-tool-mail 行 + SKILL.md)
  2. 建 session(加入该 preset)→ 得 SessionId(或复用已存在 session)
  3. gateway 侧: register_agent_email 链(注册参数 = webhook_host 三态解析)→ api_key
  4. agentmail.json 落盘:email / api_key / webhook_url(本地端点)/ webhook_secret / session_id / preset
  5. 注册 bridge 路由: register_bridge_route(email → 本地端点全 URL)——铁律
unbind(一次性,unbind_agent.py):
  gateway 侧: deregister_agent_email 链 → 删 agentmail.json
```

agentmail.json 读写在 `mail-core` 内唯一实现(原子写 tmp+replace),`mail-inbound` 与 `tool-mail` 都经它读写,无双份。

### 5.5 aimail 仓库新文件(repo-direct,同 DeerFlow 模式)

```
tools/dsh/
  amail_base.py                 # dsh 适配层最小壳:注入点赋值 + PERSONA_SUPPORTED=False + config loader
scripts/dsh/
  bind_agent.py                 # bind:preset 选型 + register_agent_email 链 + 三态注册参数 + 路由注册 + agentmail.json 落盘(薄壳)
  unbind_agent.py               # unbind:deregister_agent_email 链 + 删 agentmail.json
  install-skill.sh              # SKILL.md → preset skill 目录(或 <dshHome>/skills/agentmail/,SKILL 零改写)
docs/DEEPSEEK-HARNESS-INTEGRATION.md   # 本文档
DSH-PREPROCESS-CONTRACT.md               # P0 契约基准(仓库根,入库;TS 预处理逐行对照)
```

## 6. 入站预处理契约(TS 实现基准)

预处理全链 13 步由 **mail-core TS 实现**(不再经外部 Python bridge)。TS 端身份/凭据/目录三类依赖全部从 agentmail.json 读取(唯一信任源)——无需 agent 运行时进程在跑。**逐行对照基准见 [DSH-PREPROCESS-CONTRACT.md](../DSH-PREPROCESS-CONTRACT.md)**:13 步输入/输出/字段名/事件名/前缀/日志路径/目录,以 aimail_base.py `preprocess_mail_payload` 为准;TS 实现用 `aimail ping` 双测锁验证契约一致。

## 7. 关键约束与铁律(延续既有)

- aimail-gateway **零改动**(铁律);语义变更须三仓同步(本次预期无需)。
- 入站中间链 **契约逐字对齐 Python**(ping/pong 前缀、三阶段事件名、日志路径、persona 归一、地址派生、富化字段)——TS 重写只换语言,不换行为。
- ping/pong 拦截在**调 agent 前最后一刻**(走完整中间链才回 pong)。
- 身份严格 = agentmail.json + api_key 唯一来源,禁扫描/禁 env 覆盖/禁跨系统。
- 配置单一事实源:agentmail.json 地址级(webhook_url/secret 成对)、agentmail_gateway.json 系统级(webhook_host 三态)。
- **路由注册铁律**:注册地址后必调 register_bridge_route(POST /api/v1/routes,port=80 占位)。
- **安装补充注册**:CLI PLATFORMS 注册表 + install 平台分支全量注册已有 agent(三平台对称)。
- 安全论证走「攻击面 + 杠杆」分析;凭据 600、目录 700。
- 出站 `X-AIMail-Agent` 只报真实检测结果,禁显式配置/env 覆盖。
- 入站 pull 复用 aimail-bridge,不写新 poller。

## 8. 风险、回滚与验证

- **风险 1**:TS 预处理与 Python 语义漂移 → 缓解:契约基准文档逐行对照 + `aimail ping` 双测锁(三阶段日志事件逐项核对)。
- **风险 2**:session 冷恢复失败(无持久化配置)→ 缓解:bind 脚本强制要求 sessionPersistence 已配,否则 fail-loud。
- **风险 3**:dsh 多 session 并发写 agentmail.json → 缓解:mail-core 唯一读写实现,原子写(tmp+replace)。
- **回滚**:dsh 侧卸载 `dsh-mail-*` 插件即移除工具与 listener;gateway 侧 deregister 释放地址;agentmail.json 删除;无 DB 结构改动。
- **验证**:ping/pong 全链路(验签→TS 预处理→followup)本地闭环;`send_mail` 出站对齐 Hermes 结果;X-AIMail-Agent 头检测;冷恢复路径(resume 后 followup);多 session 隔离(A/B 两 session 各回各的地址)。
- **验收双测铁律**:`aimail ping`(三阶段日志闭环)+ `aimail welcome`(管理员收到 Re: 回复,带 `X-AIMail-Agent: dsh/...` 头)。

## 9. 实施步骤(6 阶段)

| 阶段 | 内容 | 产出/验收 |
|------|------|-----------|
| P0 契约基准 | 提取 preprocess_mail_payload 逐行对照表(13 步输入/输出/字段/事件/前缀/日志/目录) | docs/DSH-PREPROCESS-CONTRACT.md |
| P1 mail-core | TS 零依赖共享库:gateway client + 12 工具函数 + 预处理全链 + agentmail.json 读写 | mail-core 包;单测覆盖契约字段 |
| P2 mail + tool-mail | host 层 ctx.mail 服务 + preset 层 12 defineTool(裸名,preset 挂载) | dsh `mail` preset 中 12 工具可见 |
| P3 mail-inbound | node:http 端点:验签 → TS 预处理 → ping/pong → followup/resume | 端点可接 bridge 路由表 |
| P4 bind/unbind | 注册链(三态注册参数)+ 路由注册 + agentmail.json 落盘 + install-skill | bind 后 ping 可回 pong |
| P5 CLI 集成 | check_status PLATFORMS 四函数 + install 平台分支 + 补充注册 | `aimail check` 识别 dsh 系统 |
| P6 验收 | 双测铁律 + 云端身份验证 | `aimail ping` + `aimail welcome` 均过 |

---

## 10. 待确认(启动 P1 前)

1. 工具/skill 落 **preset 层**(agent 定义)——确认。
2. `mail-core` 独立共享库(框架无关)——确认。
3. 绑定并入 agentmail.json(session_id/preset 字段,无独立 binding.json)——已定(2026-08-18)。
4. 入站 TS 进程内预处理(仿 DeerFlow 8001,无外部 Python bridge)——已定(2026-08-18)。
