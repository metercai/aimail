# HANDOFF — SSE 宿主契约缺口（2026-11-18）

> 依据：`docs/aimail-host-sse-contract.md`（Host SSE Activation Contract v1）。
> 提出方：验证方（QA）。**本文只描述缺口与判据，不改产品源码；夹具由验证方自行修正。**
> 本轮按 owner 裁决已在**工作树**落地的两处产品改动（尚未并入 main；diff 见文末附录 A，不在缺口清单内）：
> 1) pull 平台注册不再写默认 webhook —— `src/pi/register-cli.ts`、`src/dsh/register-cli.ts`（`--local-webhook` 不再默认成 `inboundUrl(INBOUND_PORT)`）；
> 2) dsh 自动绑定不再写本机入站 URL —— `src/dsh/mail-service.ts`（`webhookUrl: ''`）。

## 0. 结论摘要

| # | 缺口 | 违反条款 | 严重度 |
|---|---|---|---|
| G1 | 宿主**启动期** `autoBind` 自行创建 `agent@<domain>`（无 workspace、无 manager） | §5.1 / §6.1 / §2.5 | 高（曾直接导致入站 550） |
| G2 | `autoBind` 写绑定时不带 `workspace_id` / `workspace_path` | §6.1（Required） | 高 |
| G3 | dsh 把 `session_id` 写进绑定并作为投递路由键 | §6.1（Forbidden）/§5.2 /§8 | 高 |
| G4 | `runtime/session-router.ts::routeThread` 没有任何宿主消费 —— §8 实际未实现 | §8 | 高 |
| G5 | 三个宿主的绑定发现键不一致（pi 按 `agent_id`、dsh 按 `session_id`/邮箱、openclaw 自有映射） | §2.3 / §8 | 中 |
| G6 | 无绑定时是否仍应起 SSE 路由 / 入站该在哪工作区起新会话，三宿主行为不一致 | §3 / §8 | 中 |
| G7 | dsh hub 收到投递、无 agent turn、却已 ack（无 workspace 时本应留 pending） | §7.2 / §8 | 高 |

## G1 启动期 autoBind 自行创建默认工作员

**现象**：宿主进程启动（pi/openclaw）或首次绑定解析失败（dsh）时，会向网关注册 `agent@<domain>`。

**证据**
- `src/pi/index.ts:314` — SSE 分支 `void tryAutoBindOnce({ allowCli: false, webhookUrl: '' }).then(() => startInboundPull(...))`
- `src/openclaw/index.ts:160` — `await tryAutoBindOnce(agentId, transport === 'sse' ? { allowCli: false, webhookUrl: '' } : {})`
- `src/dsh/mail-service.ts:261`（catch 分支 `tryAutoBindSession`）→ `src/core/ensure-binding.ts` 第 4 步 "Idempotent autoBind"
- 网关日志（本机夹具，每轮一条）：`Agent address registered system_id=… email=agent@l2.local`

**违反**
- §5.1：默认工作员由**系统对接（`aimail_system bind`）** 第 6 步创建并绑定"宿主默认工作区"——不是启动期自建。
- §6.1：SSE 绑定必须含 `workspace_id`/`workspace_path`；autoBind 两者都不写。
- §2.5：welcome/manager 邮件必须走 pending/SSE；自建地址没有 manager ⇒ 网关创建时不写白名单行 ⇒ 该地址入站 `550 Sender not whitelisted`（本轮实测：`domain_addr_meta.manager_address` 有值、`whitelists` 无行）。

**修法**：删除三处启动期 autoBind；绑定的唯一入口是 `aimail_system bind`（系统对接，含默认工作员）与 `aimail_address bind`（地址绑定）。宿主启动只做"读本地绑定 → 开流"，没有绑定就只起路由、不建地址。

**判据**
1. 宿主启动后网关日志**不得**出现 `Agent address registered`（无工具调用时）；
2. 启动后 10s 内 `POST /api/v1/admin/systems/*/addresses` 调用数为 0；
3. 无绑定时宿主仍能启动、并能开 SSE 路由（只是无流）。

## G2 autoBind 写绑定缺 workspace 字段

**证据**：`src/core/auto-bind.ts:379`（`platform fields merged into the binding`）、`:424`（`platform fields persisted into agentmail.json`）、`:289`（`cfg['webhook_url'] = opts.webhookUrl` 分支）——写入路径不产生 `workspace_id`/`workspace_path`。<!-- contract-allowed: QA evidence quotes the binding filename -->
对照：地址工具路径是正确的 —— `src/dsh/address.ts:170-171` 写 `workspace_id` + `workspace_path`。

**违反**：§6.1 Required 字段表。

**判据**：任意 SSE 绑定文件必须同时含 `workspace_id` 与 `workspace_path`（绝对路径，`cwd` 可用）；缺失即红。

## G3 session_id 被写进绑定并作为路由键

**证据**
- `src/dsh/mail-service.ts` autoBind `extraFields: { session_id: sessionId, preset }`；
- `src/core/auto-bind.ts:379/424` 把 `session_id` 合并/落盘进绑定；
- `src/dsh/inbound.ts:141-142,161,185` —— 入站按 `cfg.session_id` 决定 followup/resume。

**违反**
- §6.1：`session_id`（或等价 per-session 键）**Forbidden as routing state**；legacy 值可忽略/清除，**不得**决定投递工作区。
- §5.2 workspace rule 第 3 条：绑定**不得**写 `session_id`。
- §8：路由应为 recipient → binding → **workspace** → thread root（`references[0]`/`in_reply_to`/`message_id`）→ 本地 meta 的 session。

**修法**：绑定只存 workspace；session 归属放到 per-address 本地 meta（§8 第 8 步 "stamp local meta with session id/workspace path"）。

**判据**：绑定文件不得含 `session_id`；把一条 welcome 的回复（同 thread root）投回，必须落在**同一 workspace** 的会话里；不同 thread 必须新建会话且 `cwd == workspace_path`。

## G4 共享路由实现未被任何宿主使用

**证据**：`src/runtime/session-router.ts:27 routeThread` 实现符合 §8，但 `grep routeThread` 仅命中 `src/runtime/index.ts`（导出）与 `test/core/runtime-router.test.ts`（自测）——**三个宿主的入站路径都没有调用它**。

**违反**：§8（收件 → 绑定 → workspace → thread → 会话 的唯一语义）。

**修法**：pi/dsh/openclaw 的入站路径统一改为调用 runtime 的 `routeThread` + 各自宿主的"新建会话（cwd=workspace_path）/复用会话"能力。

**判据**：三宿主对同一封邮件产生同一 `DeliveryTarget`（followup 或 new+workspace）；该函数有宿主侧调用点（非仅测试）。

## G5 绑定发现键不一致

**证据**
- pi：`core/ensure-binding.ts` adopt 用 `loadConfigByAgentId(systemId, 'agent')` —— **按 agent_id**，不校验邮箱（本轮实测：pi 把夹具注册的 `reg…@` 绑定当成自己的，L2 因此 12/12）；
- dsh：启动按默认地址 `agent@<domain>` 解析（本轮实测日志 `no local binding for agent@l2.local`）；
- openclaw：`resolveAgentId()` + 自有 id 映射。

**违反**：§2.3（邮箱绑定到 workspace，会话可弃）+ §8（按收件人 → 绑定路由）。

**判据**：三宿主"找到绑定"的键必须一致且为**地址**（+ workspace），不得因宿主内部 id 不同而借到别的地址的绑定。

## G6 无绑定时行为不一致

- pi/openclaw：启动即尝试绑定（G1）；
- dsh：无绑定 → 报错并（本轮）阻断绑定链。
**违反**：§3（SSE consumer 能力）+ §8 第 2 步（无绑定 ⇒ 不 ack、留在 pending）。
**判据**：无绑定时宿主应"起流但无可投递目标"，网关侧该地址邮件保持 pending（不得 ack、不得被自动回执）。

## G7 —— dsh mail-hub：收到投递、无 agent turn、却被 ack（新，2026-11-18 实测）

**现象**（本轮 dsh SDK L2，宿主 `profile=web`，系统流 `system:system-4b28`）
```
hub 状态(投递前): {"connected":true,"streams":["system:system-4b28"],"lastEventAt":"","lastError":""}
hub 状态(投递后): {"connected":true,"streams":["system:system-4b28"],"lastEventAt":"2026-11-18T03:32:32.507Z","lastError":""}
stub LLM 命中: 0 → 0（无 agent turn）
网关侧: pending_deliveries 中该行已消失（被消费/ack）
```
对照：同一网关、同一系统密钥，直接用 SDK `startSystemPull()` 拉取同一封信，`onEmail` **正常回调**（`[PULLED] 18 agent@l2.local`）——说明网关、流接口、scope、签名都正确，问题在 dsh hub 的投递处理。

**违反**
- §7.2："Ack only after the host accepts the payload into a session… **Do not ack on binding miss, workspace miss, paused, or Agent creation failure.**"
- §8 第 8 步："Deliver the processed inbound message into that session; stamp local meta with session id/workspace path; **ack**."

**推断**（需开发确认）：绑定当前没有 `workspace_id`/`workspace_path`（G2），hub 在路由时命中 "workspace miss"，按契约应**不 ack 并留 pending**；实测却被 ack 且未建会话、未产生 turn。

**判据**
1. 同一封信投递后：stub LLM 命中 +1 且新增请求体含唯一串（agent turn 真的发生）；
2. 只有在该 turn 完成后该 pending 行才消失（ack 在投递之后）；
3. workspace 缺失时该行必须留在 pending（不得 ack），日志给出 workspace miss 原因。

## 夹具侧（验证方自行修正，不需开发处理）

1. 地址绑定改为**在宿主会话内**调用地址工具完成（写入 `workspace_id`/`workspace_path`），不再用 register-cli 直连注册代替；
2. 断言绑定含 workspace 两字段、不含 `session_id`、不含 webhook 相关字段；
3. 断言流身份：有系统域密钥时 `identity=system_id`（一条系统流），否则按地址流；
4. 投递断言按 §8：recipient → binding → workspace → thread；同 thread 复用会话、异 thread 新会话（`cwd=workspace_path`）；
5. welcome/manager 邮件必须经 pending/SSE 到达并 ack；`[Auto-Reply]` 出现即红（§8 第 2 步：无绑定不得自动回执）。

## 附：本轮观测（供复现）

- 本机夹具网关 `gateway-852a051`、CLI 0.1.65、SDK 0.4.1（本地 tarball，`SDK_PACK_DIR`）。
- pi SDK L2：12/12 绿（依赖 G5 的宽松 adopt，合同层面应予纠正）。
- dsh SDK L2：10/12；`agent@l2.local` 绑定缺失 ⇒ 宿主拒绝绑定 ⇒ 无 turn。
- 网关侧证据：`Inserted pending delivery (pull mode)` 正常；`whitelists` 中 `agent@l2.local` 无记录（G1 后果）。

---

## 附录 A —— owner 已裁决、待并入 main 的 diff（两处）

1) `src/pi/register-cli.ts` / `src/dsh/register-cli.ts`
```diff
-  const localWebhook =
-    arg(argv, '--local-webhook') || inboundUrl(INBOUND_PORT)
+  // SSE 平台无本地监听：不再默认写本机入站 URL（owner 裁决 2026-11-18）
+  const localWebhook = arg(argv, '--local-webhook') || ''
```
（并移除随之无用的 `inboundUrl` / `INBOUND_PORT` import）

2) `src/dsh/mail-service.ts`
```diff
-      webhookUrl: inboundWebhookUrl(),
+      // dsh 是 InboundMode::Pull：绑定不得写本机入站 URL（owner 裁决 2026-11-18）
+      webhookUrl: '',
```
（并移除随之无用的 `inboundWebhookUrl` 助手与 `INBOUND_PATH`/`INBOUND_PORT` import）

验证基线：`pnpm build` 绿、`pnpm test` **489 passed / 55 files**（2026-11-18 实测）。

## G7-续 —— 跨宿主：pull 形态下 pi 之外两个宿主不产生 agent turn（2026-11-18 复测）

同一网关（gateway-852a051）、同一系统密钥、同一夹具（SDK L2，已发 CLI 0.1.65 + 本地 0.4.1
tarball），三宿主结果：

| 宿主 | 网关是否接受投递 | 适配器/流状态 | agent turn |
|---|---|---|---|
| pi | ✓ | `transport=sse`，`pull scope … only_email=…` | **✓**（stub 命中 +1，请求体含唯一串） |
| dsh | ✓ | hub `{"connected":true,"streams":["system:system-4b28"]}`，`lastEventAt` 始终为空 | ✗ |
| openclaw | ✓ | `plugins.entries` 已启用；`hooks` 已接线 `{"enabled":true,"hasToken":true,"path":"/hooks"}` | ✗ |

判定依据（同一把钥匙在同一网关上是通的）：
`startSystemPull({systemId, gatewayUrl, apiKey})` 在容器内直连同一网关，能正常收到
`[PULLED] 18 agent@l2.local`；`GET /api/v1/admin/pending/stream` 带系统身份时保持长连接。
⇒ 网关/流/签名/scope 均正常，差异在 dsh 与 openclaw 各自的消费→交付实现，
   §8 第 8 步（投递进会话并 ack）未真正发生；dsh 侧还出现"队列被消费但 `lastEventAt` 为空"。

复现（本机，夹具在私有仓）：
```
HOME=/tmp/l2d/home AIMAIL_REPO=<aimail 工作树> SDK_PACK_DIR=/tmp/sdkpack E3_SMTP_PORT=12525 \
  bash run-hosts-from-host.sh dsh|openclaw system-4b28 http://127.0.0.1:18080 l2.local
```
期望：SMTP 注入后 120s 内 stub 命中新增且请求体含唯一串（pi 已达标）。

## G8 —— 无头环境缺少"宿主内激活"入口，pull 无法 arm（2026-11-18，验证方）

现象：地址经夹具在**宿主外**绑定（工具模块直接调用，写入 workspace=… 后重启宿主），
宿主启动成功、插件已加载（`dump-config` 可见 `mail-hub`，Docker 内实测 `grep -c mail-hub` = 2），
但宿主日志除 `dsh web: …` 外没有任何 `[agent-aimail]` 激活行，SMTP 注入被接受后
**stub LLM 零命中**（无 agent turn）；同一网关同一系统密钥用 SDK `startSystemPull()` 直连可正常回调。

对照：pi 在相同夹具下 13/13 绿（单地址模型，启动即按指针 arm）。

结论：按 `fix(hosts): re-arm pull after in-agent activation` 的设计，pull 只在
**宿主进程内发生激活**（agent 调 `aimail_address`）后才被 arm；外部预写绑定 + 重启不触发 arm。

请开发在下列两种做法中选一，并把结论写进 `docs/aimail-host-sse-contract.md`：
1. 提供**无头激活入口**：`dsh` 侧给出可在宿主进程内触发 `aimail_address` 的 API/CLI
   （现有 `dsh headless "<task>"` 是独立进程，无法作用于正在运行的 web 进程的 hub）；
2. 或**支持"绑定已存在时重启即 arm"**（read-on-boot 后主动 arm 一次），并明确该行为对
   dsh / openclaw 都成立。

判据：在宿主外完成绑定并重启后，宿主日志出现 pull 已 arm 的证据，注入的邮件在
stub LLM 里产生一次 turn（含唯一串），且 ack 发生在 turn 之后。

补充（openclaw）：其 `/hooks/agent` 可创建/继续会话，理论上可作为"进程内激活"入口；
验证方将先用该入口尝试（stub 返回 `aimail_address` 的 tool_calls），若可行则 openclaw 不需要改产品。

## G9 —— openclaw 会话工具面里没有 agent-aimail 的工具，导致"对话框绑定"无法执行（2026-11-18，验证方）

现象（Docker，openclaw 2026.11.5，SDK 0.4.1 本地构建）：
- 插件已安装并启用：`openclaw plugins enable agent-aimail --accept-capabilities` → `plugins.entries` 有值、hooks 接线成功（`{"enabled":true,"hasToken":true,"path":"/hooks"}`）；
- 通过宿主自己的对话入口（`POST /hooks/agent`，等价"在 agent 对话框输入"）提交激活 prompt，宿主接受：
  `HTTP 200 {"ok":true,"runId":"6cb8ab0d-…"}`，且 stub LLM 收到 **5 次**请求、prompt 文本命中 4 次；
- **但请求体里的 `tools` 只有宿主内置工具**：
  `apply_patch dispatch_action edit exec github_publish ls message openclaw process read sessions_yield tool_describe tool_search`
  **没有任何 aimail 工具**（`aimail_address` / `aimail_system` 等 15 个 MAIL_TOOLS 均不在其中）。
- 结果：agent 无法按 prompt 调 `aimail_address` → 绑定不会在宿主内发生 → pull 不 arm → 注入邮件不产生 turn。

代码位置：`tssdk/packages/agent-aimail/src/openclaw/index.ts:136`
`api.registerTool((ctx) => createMailTools(ctx, resolveAgentWorkspaceDir))` —— 插件的 15 个工具确实注册了，但没有出现在会话的工具面里。

需要开发确认/修：
1）插件工具进入 openclaw 会话工具面是否需要额外的能力授予/策略（`--accept-capabilities` 是否覆盖"tools"这一类）？
2）是否需要 per-agent 的 `tools` 允许列表或 plugin→agent 绑定？
3）若这是预期（工具只在特定 agent 上下文可见），请给出无头环境下的正确开启方式；否则这是缺陷。

判据（拿到修法后验证方会跑）：对话入口提交"绑定地址 X"的 prompt 后，会话工具面应包含 `aimail_address`；agent 调用后宿主出现绑定/激活证据；随后注入邮件在 stub 产生 turn（含唯一串），并且 ack 在 turn 之后。

### G8 补充（2026-11-18 复验）

- `dsh --profile web --dump-config` 能查到 `mail-hub`（容器内 `grep -c mail-hub` = 2），宿主启动静默，
  说明插件已加载；缺的是"宿主内激活"事件。
- 无头对话入口现状（`dsh headless "<prompt>"`）：
  `dsh: MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official";
   store DEEPSEEK_API_KEY through the credentials service`
  ⇒ headless 不读 web profile 里配置的 LLM（我们指向 stub），直接找 provider 凭据；
  而 `dsh web` 的 chat API 没有文档化路由。
- 结论：验证方无法在无头环境"像用户在对话框输入 prompt"一样触发 in-agent 激活。
  需要（任一）：① document web chat API（含 token 用法）；② 让 headless 接受 profile 的 LLM 配置；
  ③ 支持"绑定已存在时重启即 arm pull"。

### G8 更正（owner 裁决，2026-11-18）

边界以 owner 裁决为准，本段覆盖上文 G8 的措辞：

- **不提供宿主外写 binding 的激活**；
- **不提供 headless 激活入口**；
- **唯一激活路径**：宿主内 prompt → Agent 调用 `aimail_system` / `aimail_address`；
- `read-on-boot` 只是"已合法激活的 binding"的**重启恢复**，**不是**第二种激活方式。

因此验证方的诉求收窄为一条（属"可测性/文档"，不是产品功能）：

> 请给出**向运行中的宿主提交一条对话 prompt** 的稳定方式（dsh：`dsh web` 的 chat API 路由与 token 用法；
> openclaw：`/hooks/agent` 已在用，已验证可用）。L2 夹具必须走这条 in-host prompt 路径完成激活，
> 不得再用"宿主外写 binding + 重启"伪造激活。

配套的验收判据不变：in-host prompt 之后，绑定/激活证据出现在宿主侧 → 注入邮件产生 agent turn（含唯一串）→ ack 在 turn 之后。

### G9 补充（2026-11-18 自查，验证方）

- 插件侧无问题：`openclaw.plugin.json` 声明 `activation.onCapabilities:["tool"]` 与 `contracts.tools`（14 个），
  运行时 `api.registerTool(...)` 注册 15 个；`plugins enable --accept-capabilities` 已执行。
- 会话工具面仍只有内建工具（`apply_patch/exec/ls/read/message/...`），无 `aimail_*`。
- 尝试用配置放开：`agents.defaults.toolPolicy` → 拒绝（Unrecognized key）；
  `agents.entries.main.toolPolicy` → 拒绝（Unrecognized key）。
- `openclaw config schema` 中 `toolPolicy` 仅出现在 `channels/discord/voice/realtime` 下，与 agent 无关。
⇒ 结论：**插件声明的 tool 能力没有进入会话工具面**，且宿主的 schema 里没有"agent 级 toolPolicy"可用。
请开发给出：插件工具进入会话工具面的正确开关（能力名/配置路径），或确认这是宿主的工具暴露缺陷。

### G8 最终证据（2026-11-18，验证方）

宿主内对话链路已在无 UI 条件下跑通并被受理（不是猜测，逐条实测）：

```
GET  /connect?token=…                                  → cookie
POST /api/session/create  payload={"args":{"request":{"cwd":"/root"}}}
  → {"ok":true,"value":{"sessionId":"session-…","agentPreset":"standard"}}
POST /api/session/follow  {"args":{"request":{"sessionId":…}}}                → ok
POST /api/session/initializeDefaultModel {"args":{"request":{"sessionId":…}}} → ok
POST /api/session/prompt  {"args":{"request":{"requestId":…,"sessionId":…,
     "mode":"queue","content":[{"type":"text","text":"AIMAIL_ACTIVATE bind <email>"}]}}}
  → {"ok":true,"value":{"accepted":true}}
```

**断点**：prompt 仅进入 Agent inbox；**stub LLM 请求数 = 0**，即 agent 未被启动，
因此既无 tool_call（`aimail_address`）也无 turn。已暴露的 session 方法里没有明显的 "start"：
`session/{create,list,page,search,rename,fork,follow,events,observeEvent,observeProjections,observeState,projections,control,cancel,attachment,openWorkspacePath,canOpenWorkspacePath,modelCatalog,initializeDefaultModel,prompt}`，
且 `session/control` 无对应 request 类型定义。

**问题（需开发回答，属自动化所必需）**：无 UI 时，`session/prompt` 之后应调用什么来启动 agent 并消费 inbox？
候选：`session/control`（请给 request 结构）或先建立某个流式订阅（`session/observeState`/`observeEvent`/`events`）。

**判据**：prompt 后应出现 ≥1 次 LLM 请求，且 `tools[]` 含 `aimail_address`；agent 调用后宿主出现绑定/激活证据；
注入邮件后出现 turn（含唯一串），ack 在 turn 之后。

#### G8 复验更新（2026-11-18，同日晚）

`session/initializeDefaultModel` 的 args 必须为空对象（代码：`typert.remote-client.d.ts` → `initializeDefaultModel: () => Promise<RemoteResult<void>>`）。
修正后完整链路均成功：

```
session/create              → ok, sessionId=…, agentPreset=standard
session/follow              → ok
session/initializeDefaultModel (args={})  → {"ok":true}
session/prompt              → {"ok":true,"value":{"accepted":true}}
但 stub LLM 请求数 = 0（agent 仍未启动，inbox 未被消费）
```

因此问题精确化为：**无 UI 时，prompt 进入 inbox 后由什么驱动 agent 消费**？
候选：`session/control`（未见 request 类型定义）、或必须先建立流式订阅
（`session/observeState` / `session.observeEvent` / `session/events`）。
判据不变：出现 ≥1 次 LLM 请求且 `tools[]` 含 `aimail_address` → 绑定/激活证据 → 注入邮件后 turn（含唯一串）→ ack 在 turn 后。

#### G8 复验（2026-11-18 深夜，最终证据链）

无 UI 条件下，dsh 的会话链路每一步都成功，但 **agent 始终不启动**（stub LLM 请求数 = 0）：

```
GET  /connect?token=…                                   → cookie                        ✓
POST /api/session/create            args={request:{cwd}}                                → sessionId, agentPreset=standard ✓
POST /api/session/initializeDefaultModel  args={}                                        → {"ok":true}                     ✓
POST /api/session/observeState      (流式, 保持打开)                                      → 连接建立                         ✓
POST /api/session/control           (流式, args={}, 保持打开)                             → 连接建立                         ✓
POST /api/session/modelCatalog      args={}                                              → default={provider:stub,model:stub-model},
                                                                                            routableProviders=[deepseek-official, stub] ✓
POST /api/session/prompt            args={request:{requestId,sessionId,mode:'queue',content:[{type:'text',text:…}]}}
                                                                                         → {"ok":true,"value":{"accepted":true}} ✓
stub LLM 请求数（激活后）                                                                  → 0  ✗
```

客户端的 chat 请求在 `dsh-client-connection/lib/client.node.js` 里是
`{type:"client-request", rpcId, method:<ns>/<method>, payload:{args:{…}}}`，
`payload` 必须恰好一个 `args` 字段；`initializeDefaultModel` / `modelCatalog` 的 args 必须为空对象。

问题：prompt 被接受到 agent inbox，但没有任何 agent 去消费它 —— 即使
会话已初始化默认模型、catalog 的 default 指向我们的 stub 且 stub 可路由、
并同时保持了 `observeState` 与 `control` 两条流。

需要开发回答（一条即可）：**无 UI 时，谁负责把 inbox 里的 prompt 变成一次 agent turn？**
（是否需要 client-connection 的 WS attach / 某个 control 帧 / 某个未文档化的 start 调用。）
判据不变：prompt 后 stub 至少命中一次且 `tools[]` 含 `aimail_address` → 绑定/激活证据 →
注入邮件后出现含唯一串的 turn，且 ack 在 turn 之后。

### G9 撤回（2026-11-19，owner 指出为夹具配置问题）

结论：G9 不成立。插件工具没进会话工具面，是我这套夹具的 openclaw 配置缺了两项，而不是产品缺陷。
按官方文档（docs.openclaw.ai/tools/plugin）配置后即可：

```json5
{ plugins: { enabled: true, allow: ["agent-aimail"],
             entries: { "agent-aimail": { enabled: true } } },
  tools: { profile: "coding",
           alsoAllow: ["send_mail","manage_contacts",...(15 个)...,"aimail_address","aimail_system"] } }
```

要点（文档原文）：`plugins.allow` 非空时限定可加载插件；**插件自有工具即使在 `tools.allow` 含 `"*"` 时也不可用**——插件激活与工具可用性是两项独立策略；工具还需要 `tools.allow`/`alsoAllow` 的许可。
夹具已按此写入（`install-sdk.sh` openclaw 分支），复跑验证工具面与 turn。

### G9 重开（2026-11-19，按官方文档配置后仍为空）

配置已按官方文档（docs.openclaw.ai/gateway/config-tools/tool-policy）改为：
- `plugins.enabled=true`, `plugins.allow=["agent-aimail"]`, `plugins.entries["agent-aimail"].enabled=true`
- `tools.profile="full"`（文档原文：only the full profile **selects optional plugin tools**）
（`tools.allow`/`alsoAllow` 不可与 profile 同 scope 并用；插件 configSchema 为 `properties:{}`，不接受任何配置键）

仍观察到：
```
openclaw plugins inspect agent-aimail --runtime --json
→ capabilities: []
→ tools: [ { "names": [], "optional": false } ]

会话 tools[]（tools.profile=full）:
apply_patch dispatch_action edit exec github_publish ls message openclaw process read sessions_yield tool_describe tool_search write
（无 aimail_*）
```
manifest 声明：`activation.onCapabilities:["tool"]`、`contracts.tools`（14 个）。

判据：`plugins inspect --runtime` 的 `tools[].names` 非空；会话 `tools[]` 出现 `aimail_address`/`aimail_system`；随后 prompt 驱动 agent 调 `aimail_address` 完成绑定并 arm pull。

#### G9 关键证据（2026-11-19，宿主自检）

`openclaw plugins inspect agent-aimail --runtime --json` 的关键字段：

```
shape            = "non-capability"
capabilityMode   = "none"
capabilityCount  = 0
capabilities     = []
typedHooks       = []
customHooks      = []
commands         = []
tools            = [ { names: [], optional: false } ]
plugin.compat    = ["activation-capability-hint"]
plugin.version   = 0.4.1
```

即宿主把这个插件按**非能力型（non-capability）**加载，`api.registerTool(...)` 根本没有被执行——
所以 `tools[].names` 为空、`hooks` 也为空（与"hooks 已接线"的早期观察不矛盾：那条是 `plugins enable` 写的配置，不是插件运行期注册）。

manifest 侧声明的是 `activation.onCapabilities: ["tool"]`，而宿主 compat 只回报 `activation-capability-hint`。
需要开发确认：0.4.x 的 openclaw 插件是否需要显式声明能力体（如 `capabilities.tool` / `openclaw.compat.pluginApi` 版本段），
否则会被当作 legacy non-capability 插件加载、工具永不注册。
判据不变：`shape` 不再是 `non-capability`、`tools[].names` 非空、会话 `tools[]` 出现 `aimail_address`。

### G9 定稿 + G10（2026-11-19，验证方；需产品侧修订）

**G9（openclaw 插件在镜像里加载不到运行时代码，宿主静默降级）**

证据链（同一镜像，2026.11.5）：
1. 直接导入插件入口：
   `Error [ERR_MODULE_NOT_FOUND]: Cannot find package 'openclaw' imported from …/agent-aimail/dist/openclaw/index.js`
   镜像里 openclaw 是 `/app` 打包体（`/app/package.json` 的 `name: "openclaw"`，`exports` 含 `.`/`./plugin-sdk/*`），
   **没有 `/app/node_modules/openclaw`**；插件装在 `/app/.openclaw/extensions/agent-aimail`，逐级上溯找不到该包。
2. 后果（宿主自检）：
   `plugins inspect agent-aimail --runtime --json` → `shape=non-capability`, `capabilityMode=none`,
   `tools=[{names:[],optional:false}]`, `commands=[]`；会话 `tools[]` 无任何 `aimail_*`。
3. 加入解析后（仅环境层，未改任何产品代码）：
   `mkdir -p /app/node_modules && ln -sfn /app /app/node_modules/openclaw`
   宿主日志：
   `startup trace: plugins.gateway-load.plugin.agent-aimail … loadFailedCount=0.0 registerFailedCount=0.0`
   `http server listening (2 plugins: agent-aimail, memory-core)`
   ⇒ 插件加载与注册恢复正常。

**需要产品侧修订（二选一）**：镜像自带 `/app/node_modules/openclaw` 解析（或打包时提供等效 link）；
或插件入口改用可解析的 specifier（如 `openclaw/plugin-sdk/*`）并声明对应 peer/依赖。

**G10（openclaw 的绑定入口与契约工具名不一致）**

证据：
- manifest `contracts.tools` 列表包含 `activate_address_code`，**不含 `aimail_address` / `aimail_system`**。
- 加载成功后适配器立即打印：
  `[aimail-pull] no agent-scope binding — polling not started (push path unchanged; address-code activation enables it)`

契约要求：会话内可被调用的工具是 `aimail_address`（系统级为 `aimail_system`）；绑定必须由宿主内 prompt 驱动该工具完成。
当前 openclaw 只认 `activate_address_code` 且未声明 `aimail_address`，导致契约路径无法建立 agent-scope 绑定 → 拉取不启动 → 邮件不产生 turn。

**需要产品侧修订**：把 `aimail_address`（及 `aimail_system`）纳入 manifest `contracts.tools` 并在会话工具面暴露；
绑定成功后应启动拉取（`[aimail-pull] polling started …`）。

判据（复验用）：
1）`plugins inspect --runtime --json` 的 `tools[].names` 含 `aimail_address`；
2）会话 `tools[]` 含 `aimail_address`；
3）宿主内 prompt 调用该工具后日志出现 polling started + 绑定文件含 workspace；
4）注入邮件后 stub 出现含唯一串的 turn，且 ack 在 turn 之后。

### G9/G10 复验（2026-11-19，dev fix 18f65cc + SDK 0.4.2）

已按 dev 指引验证（无任何源码/产品改动，仅夹具）：

1）新 SDK 0.4.2 本地构建：`pnpm build` 绿、`pnpm test` **490 passed**、`npm pack` → `agent-aimail-0.4.2.tgz`。
2）**不再需要 `/app/node_modules/openclaw` 链接**（已从夹具删除）：
   `export … dist/openclaw/index.js` → `exports:['default']`, `id=agent-aimail`, `register` 存在；无 ERR_MODULE_NOT_FOUND。
3）宿主加载/注册：
```
startup trace: plugins.gateway-load.plugin.agent-aimail
  loadFailedCount=0.0 registerFailedCount=0.0 registerMs=21.5
http server listening (2 plugins: agent-aimail, memory-core)
```
4）manifest 已含 `aimail_address` / `aimail_system`（`contracts.tools` 16 项）。
5）**仍然为红**（会话工具面 + turn）：
```
会话 tools[] = apply_patch dispatch_action edit exec github_publish ls message openclaw
               process read sessions_yield tool_describe tool_search write     ← 无 aimail_*
hooks agent run: status=error
  summary=OpenClaw stopped this turn: the same aimail_address call failed 3 times (unknown tool)
```
 即：插件加载/注册成功，但 **会话工具面里没有 `aimail_address`**，agent 调用被判 `unknown tool`。
 `plugins inspect agent-aimail --runtime --json` 仍是 `shape=non-capability, capabilityMode=none, tools=[{names:[]}]`。

**需要 dev 确认（一条即可）**：0.4.2 在 openclaw 2026.11.5 上，工具注册后的**实际工具 id** 是什么（是否带命名空间前缀），以及它们在会话 `tools[]` 出现的条件（是否需要 `plugins.entries["agent-aimail"].capabilities=["tool"]` 或宿主策略键）。
我方已试过：`tools.profile=full`（文档称只有 full 选可选插件工具）、`tools.allow=[16 个裸名]`、`tools.alsoAllow=[16 个裸名]`——三种都拿不到 `aimail_*`；`tools.allow` 反而把工具面压到只剩 3 个 meta 工具，说明**裸名与宿主注册 id 不匹配**。

#### G9/G10 复验（2）：dev 的第 3 步在本机不成立

dev 给的验收步骤 1、2 通过，**第 3 步失败**：

| 步骤 | 期望 | 实测 |
|---|---|---|
| 1 装/载 dev SDK | 无需 link | ✓ 0.4.2 直接 import 成功（`exports:['default']`, `id=agent-aimail`, `register` 存在） |
| 2 load/register | `loadFailedCount=0 registerFailedCount=0` | ✓ 均 0（registerMs=21.5，`http server listening (2 plugins: agent-aimail, memory-core)`） |
| 3 inspect | `shape=capability`，`tools[].names` 含 `aimail_address`/`aimail_system` | ✗ `shape=non-capability`, `capabilityMode=none`, `tools=[{names:[],optional:false}]` |
| 4 会话工具面 | `tools[]` 含 `aimail_address` | ✗ 只有内置/元工具；agent 调用被判 `unknown tool` |

补充：openclaw 2026.11.5 本会话工具面是"元工具 + 动态检索"形态（`tool_search`/`tool_describe`/`dispatch_action`）。
验证方已尝试：让 agent 先 `tool_search("aimail_address")` 再调用 —— 检索结果也不会把该工具放进可用集，
hooks run 仍报 `the same aimail_address call failed 3 times (unknown tool)`。

**请 dev 说明**：他们验证第 3 步时用的宿主版本/安装方式（是否同一 2026.11.5 镜像、`plugins install <tgz>` 还是 registry 安装），
以及 `shape=capability` 由什么决定（manifest 字段？安装记录/信任状态？provenance-invalid 是否会导致降级为 non-capability）。
本机日志里同时存在：
`[plugins] OpenClaw can't verify where this plugin came from … reason=provenance-invalid; installSource="archive"`
——怀疑 archive 安装导致插件按 non-capability 加载、工具不注册。

#### G9 定位到可修的根因（2026-11-19，验证方，0.4.2）

对比实验（同一镜像、同一次 inspect）：

```
memory-core (bundled)   → tools: [ {names:["memory_search"]}, {names:["memory_get"]}, {names:["intent"]}, … ]
agent-aimail (0.4.2)    → tools: [ {names:[], optional:false} ]
```
⇒ `shape=non-capability` 是**正常形态**（memory-core 也是），差别只在"注册出来几个工具名"。

直接调用插件工厂（不经过宿主）：
```
createMailTools(ctx, () => '/root')  → count: 16
names: send_mail manage_contacts contact_profile set_contact_profile email_summary
       set_email_summary search_mail board_status board_task_list board_task_show
       board_heartbeat board_members board_roles aimail_address role_calibrator aimail_system
```
⇒ 工厂本身没问题，16 个工具齐全。

宿主侧注册 API 的用法（宿主自带 SDK 原文 `/app/dist/plugin-sdk/meeting-runtime.js:4955`）：
```js
t.registerTool((r) => ({ ... }))     // 工厂返回「单个」工具描述
```
而本插件（`dist/openclaw/index.js:124`）是：
```js
api.registerTool((ctx) => createMailTools(ctx, resolveAgentWorkspaceDir))   // 工厂返回「数组」
```
**假设（高置信）**：openclaw 2026.11.5 的 `registerTool(factory)` 期望工厂返回单个工具描述；返回数组时该次注册被丢弃（不报错），
于是 `tools.names=[]`、会话工具面无 `aimail_*`、agent 调用被判 `unknown tool`。

**建议修法（产品侧，一行级）**：改为每个工具注册一次
```js
for (const t of createMailTools(ctx, resolveAgentWorkspaceDir)) api.registerTool(() => t)
```
或使用宿主提供的复数注册 API（若有）。

判据（修完应满足）：
1）`plugins inspect agent-aimail --runtime --json` → `tools[].names` 含 16 个工具名（含 `aimail_address`/`aimail_system`）；
2）会话 `tools[]` 含 `aimail_address`；
3）宿主内 prompt 调用后出现 `[aimail-pull] polling started`；
4）注入邮件 → stub turn 含唯一串 → ack 在 turn 之后。

#### G9 追加实验（2026-11-19）：改成"每个工具注册一次"仍然 names=[]

在不改仓库源码的前提下，把 0.4.2 的 dist 复制到 /tmp 做了一次**一次性实验**（未提交、不进入产品）：
```js
// 原：api.registerTool((ctx) => createMailTools(ctx, resolveAgentWorkspaceDir))   // 返回数组
for (const _t of createMailTools({}, resolveAgentWorkspaceDir)) api.registerTool(() => _t)
```
结果（同一 2026.11.5 镜像、`plugins install <tgz>`）：
```
plugins inspect agent-aimail --runtime --json
→ tools: [ {names:[]}, {names:[]}, … ] × 16        ← 变成 16 条，但每条名字仍为空
```
对照：`memory-core` 是 `[{names:["memory_search"]}, {names:["memory_get"]}, {names:["intent"]}]`。

工具对象本身形状正常（直调工厂可见）：
```
keys: [ 'name', 'label', 'description', 'parameters', 'execute' ]
sample: {"name":"send_mail","label":"send_mail","description":"…","parameters":{...}}
```
⇒ 说明：宿主接受了 16 次注册调用，但**没有从工厂返回值里取到 name**；
即 `api.registerTool(factory)` 的契约与插件用法不一致（宿主自带示例为 `registerTool((r) => ({...}))`，且 memory-core 的条目能取到名字）。

**请 dev 给出**：2026.11.5 上 `registerTool` 的准确调用契约（工厂返回单对象？直接传对象？是否需要 `{name, description, inputSchema, handler}` 字段名？），
按该契约修改后，`tools[].names` 应出现 16 个名字。验证方已把两次实验的可复现步骤写在上文，可直接照做。
