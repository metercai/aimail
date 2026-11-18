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

**证据**：`src/core/auto-bind.ts:379`（`platform fields merged into the binding`）、`:424`（`platform fields persisted into agentmail.json`）、`:289`（`cfg['webhook_url'] = opts.webhookUrl` 分支）——写入路径不产生 `workspace_id`/`workspace_path`。
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
