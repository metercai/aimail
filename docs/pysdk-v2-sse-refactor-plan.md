# pysdk V2 重构方案：去 CLI、去 push，插件内 SSE 闭环 + L2 门禁绿

状态：**方案（可实施），本文件取代同名的旧版汇总**  
目标版本：`agent-aimail` pysdk **0.3.0**（当前 0.2.9）  
对齐契约：[`aimail-host-sse-contract-v2.md`](aimail-host-sse-contract-v2.md)（下称“契约”）  
参考实现：tssdk `tssdk/packages/agent-aimail/src/**`（0.5.x，已通过 L2/L3）  
门禁：[`tests/host-acceptance/v2/GATE.md`](../tests/host-acceptance/v2/GATE.md)（L2）

---

## 0. 一句话结论

```text
pysdk 现在的入站是“V1 地址级 long-pull + 本地 HTTP replay”：
  依赖 CLI 安装、依赖 per-profile webhook 端口、依赖 host 源码 patch、
  binding 没有 AgentSystemId、pull client 没有聚合路径与 X-Agent-System-Id。

目标形态与 tssdk 完全同构：
  AgentSystemId 聚合 SSE（每 (AgentSystemId, Gateway) 一条）
  -> 插件内预处理
  -> 宿主插件机制提供的“进 session”入口
  -> 宿主确认接受后才 ack
  全程无 CLI、无本地 inbound HTTP、无 host patch、无 per-profile 端口。
```

三个已核实的前提（不再是假设）：

1. **Hermes** 有正式插件系统与 platform adapter，入站可直投 `handle_message`，ack 判据是 `event._gateway_accepted`（§2.3）；
2. **DeerFlow** 有正式 Python 扩展系统（`deerflow.extensions` + `install(registry, config)`）与带幂等键的 run API（§2.5）；
3. **网关侧 pull 台账行缺陷（ING-1/REQ-13）已修复并发布**（v1.0.12 起，`pins.env` 现 v1.0.13），S3 不再被网关阻塞（§2.6）。

**L2 门禁宿主 = hermes + deerflow 两个**（G5）。deerflow 的**前置**是先把干净的 agent 环境做出来（§7.1，独立可并行的工作流）。

---

## 1. 目标与不变量

### 1.1 目标（交付口径）

| # | 目标 | 判定方式 |
|---|---|---|
| G1 | 安装/激活不调用 SDK 自己的 CLI | 全流程无 `aimail install` / `sdk_ops` 子命令；安装 = pip（或宿主插件机制） |
| G2 | 无 push/webhook 端点依赖 | binding 无 webhook 字段；无本地 inbound listener；无 host 源码 patch |
| G3 | 每 `(AgentSystemId, Gateway)` 一条 SSE | 启动日志 `SSE reconcile: N gateway stream(s) from M binding(s)`，N = Gateway 数 |
| G4 | 与 tssdk 行为同构（同契约、同端点、同 ack 语义、同幂等键） | §5 模块级对照 + 跨语言一致性测试 |
| G5a | **hermes** 转为 L2 实跑 PASS | `run-matrix.sh --sdk py --host hermes --stages "s1 s2 s3 s4 s5 neg"`，RED=0 GAP=0 SKIP_LEGACY=0 |
| G5b | **deerflow** 转为 L2 实跑 PASS（先决：干净环境就绪） | `run-matrix.sh --sdk py --host deerflow --stages "s1 s2 s3 s4 s5 neg"`，同上 |
| G6 | pysdk 发新版 | wheel 校验 + L0 PASS + L2（两宿主）PASS + 版本/变更记录 |

### 1.2 不变量（不得回退）

- `aimail_address` 仍是**唯一**地址激活/生命周期工具；不新增 `aimail_system`；
- binding 只存契约 §7.1 字段；**不存 Session id、不存一次性 code、不存 admin/domain raw key**；
- 地址激活唯一入口 = 地址激活码；不接受 `system_code`/`manager`/`domain` 等用户参数；
- 目录分组键 = `GatewaySystemId`（不是 AgentSystemId）；
- SSE 聚合身份 = 本地 `AgentSystemId`（不是 GatewaySystemId）；
- ack 只在“宿主已接受进 session”之后；任何 miss/paused/失败都不 ack；
- 一份邮件不能被两个消费端同时消费（V1 pull 与 V2 SSE 共存必须显式互斥）。

---

## 2. 事实基线（本次已核实，不是推测）

### 2.1 pysdk 现状（V1 事实，含缺陷定位）

| 位置 | 现状 | 问题 |
|---|---|---|
| `pysdk/aimail_base.py:1945` `AGENT_SCOPE_SYSTEM_PREFIX="shared_addr_"` | agent-scope 判定靠 V1 前缀 | 契约 V2 的 `system-*` binding 结构性地**永不**被判定为 agent-scope ⇒ 不 arm（QA B1 的 pysdk 版本） |
| `pysdk/aimail_base.py:2038` `is_agent_scope_binding` | 只认前缀 + api_key | 没有 `agent_system_id` 概念 |
| `pysdk/aimail_base.py:2095` `resolve_inbound_replay_target` | 解析 `webhook_url` + `webhook_secret` | push 时代产物：无本地端点就不启动 pull（邮件永远 pending） |
| `pysdk/aimail_base.py:2637` `start_agent_pull_entry` | **每 binding 一条** pull 循环 | 与契约 §6.1（每 (ASID, Gateway) 一条）冲突；多地址 = 多条流 |
| `pysdk/aimail_base.py:2731` `start_agent_pull_entries` | 按 pointer 单地址 / 无 pointer 时“恰好一个则启动” | 无 ASID 聚合、无跨 binding 分组 |
| `pysdk/aimail_base.py:2480` 附近 replay | 拉到邮件再 HMAC POST 回宿主本地 inbound | 本地 HTTP 依赖；ack 语义变成“本地回放成功” |
| `pysdk/aimail_tools.py:842` `_stream_round` | 硬编码 V1 `PULL_STREAM_PATH` | 无聚合路径、无 `X-Agent-System-Id`（QA B2 的 pysdk 版本） |
| `pysdk/aimail_tools.py:890` `_deliver_batches` | 仅“成功/抛异常”两态 | 无 `skip`（paused/unknown）语义 ⇒ 暂停地址被当成 error 反复重试 |
| `pysdk/aimail_tools.py:541` `activate_address_code` | 兑换时不带 `X-Agent-System-Id` | Gateway 侧没有该 ASID 归属行 ⇒ 聚合流会 403 |
| `pysdk/aimail_tools.py` `activate_address_code_persist` | 写 `webhook_secret`/`webhook_url`，**不写** `agent_system_id` | binding 缺契约必填字段 |
| `pysdk/hermes/ensure_config.py` | 写 `platforms.webhook`、端口 8644、secret | 多 profile gateway 下二级 profile **禁止** port-binding platform |
| `pysdk/hermes/patch_webhook.py`（811 行） | 按 commit 锚点 patch 宿主 `webhook.py` | 宿主升级即失效；7 个子 patch；与“去 host patch”直接冲突 |
| `pysdk/hermes/register_profiles.py` | 遍历 profile、改全局 `HERMES_PROFILE_DIR` 再注册 | 进程全局污染；与“激活唯一入口”冲突 |
| `pysdk/hermes/aimail_hermes.py:1060` 附近 | `_pull_replay_target` / `_on_pull_email` / `start_agent_pull` | 入站 = 本地回放；无 ASID |
| `pysdk/deer-flow/aimail_inbound.py`（514 行） | FastAPI inbound（push 服务） | push 路径，V2 不使用 |
| `pysdk/deer-flow/manage.py`、`aimail_deerflow.py` | 绑定/注册 + 适配层（进程全局 context） | 无 ASID、无聚合、thread 按地址固定 |
| `pysdk/install.py`、`pysdk/sdk_ops.py` | CLI / 进程命令契约（assemble/update/teardown） | 本次要移除的主路径依赖 |

**结论：pysdk 侧 `agent_system_id` 出现次数 = 0**。V2 所需的核心身份在 pysdk 里完全不存在。

### 2.2 tssdk V2 参考实现（逐模块对照源）

| tssdk 文件 | 关键内容（可直接移植） |
|---|---|
| `src/core/agent-system-id.ts` | `deriveAgentSystemId` = `sha256("aimail-agent-system-v1\0"+realpath(agent_home)+"\0"+fp)` 取前 32 hex，前缀 `asid_`；`hardwareFingerprint` = `hostname\|platform\|arch\|machine-id`；env seam：`AIMAIL_HARDWARE_FINGERPRINT` / `AIMAIL_AGENT_SYSTEM_ID` / `AIMAIL_AGENT_HOME`；`setAgentHomeResolver` 宿主 seam |
| `src/core/pull-contract.ts` | `PULL_HOLD_MAX_MS`=50s、`PULL_HTTP_TIMEOUT_MS`=hold+15s、`PULL_STREAM_READ_TIMEOUT_MS`=90s、`PULL_STREAM_PING_TIMEOUT_MS`=30s、`PULL_REPROBE_INTERVAL_MS`=10min、`parsePullMode`、`pullStreamBackoffSecs`(1,2,4,8,16,30)、`pullPollBackoffSecs`(interval·2^f, cap 300)、`pullDecideNextMode` |
| `src/core/poll-entry.ts:71` | `isAgentScopeBinding` = V1 前缀 **或** `isV2AgentScopeBinding`（`agent_system_id` 非空）——B1 的修法 |
| `src/core/poll-entry.ts:97` | `buildAgentSystemClient`：聚合 `stream`/`pending`/`ack` 三路径 + `X-Agent-System-Id`——B2 的修法（唯一构造函数，禁止第二处自建 client） |
| `src/core/poll-entry.ts:168` | `startAgentSystemGroups`：按 `(agent_system_id, gateway_url)` 分组；rep = 按 email 排序的第一个 active binding；组内全 paused ⇒ 不建流；配置禁用 ⇒ 不建流 + 审计行 |
| `src/core/poll-entry.ts:274` | `freshDeliveryTarget`：每次投递**重新查** binding ⇒ `skip('paused')` / `skip('unknown')`（不抛错、不 ack、不算 error） |
| `src/core/system-pull.ts` | `startSystemPull`：一条 system 级流 + `stats{pulled,acked,errors}` + `onAuthFailure`（`AUTH_FATAL_STREAK=3`） |
| `src/core/address-code.ts:165` | `activateAddressCode` 带 `X-Agent-System-Id`；persist 写 `agent_system_id`、`manager_address`、按 `system_id` 分组目录、pointer |
| `src/core/address-code.ts:492` | `deliverBatches`：accepted/void ⇒ ack；`skip` ⇒ 不 ack、从去重集移除、下轮重来；throw ⇒ 不 ack + 累计 errors |
| `src/dsh/mail-hub.ts:139` | `reconcile()`：过滤 `paused`/无 key；非 V2 ⇒ V1 单地址流（legacy）；`agent_system_id != 本机 ASID` ⇒ **跳过并告警**；每 Gateway 取一个 rep；输出 `SSE reconcile: <streams> gateway stream(s) from <bindings> binding(s)` |
| `src/dsh/mail-hub.ts:225` | 契约 §8.4 status：`{mode:'sse', agentSystemId, gateways:[{gatewayUrl,connected,lastEventAt,lastError}]}` |
| `src/runtime/session-router.ts` | `threadRoot` = `references[0] → in_reply_to → message_id`；`routeThread` = 有 `meta.session_id` 且 workspace 一致 ⇒ followup，否则 new |
| `src/runtime/stream/plan.ts` | 纯函数 `planStreams`：有 system key ⇒ 按 system 分组；否则退化为按地址（过渡） |

### 2.3 Hermes 宿主能力（在 `aimail-host-hermes:latest` 镜像内实测）

以下均为容器内直接读到的**源码事实**，是本方案可行性的基础：

| 能力 | 实测结论 | 证据（容器内路径） |
|---|---|---|
| 插件机制 | 完整插件系统：`<profile home>/plugins/<name>/`（可 drop-in）、pip entry point 组 `hermes_agent.plugins`（`ep.load()` 后调用 `register(ctx)`） | `hermes_cli/plugins.py:4-5`、`hermes_cli/plugins_discovery.py:29,79`、`hermes_cli/plugins_loader.py:610-618` |
| 插件清单 | `plugin.yaml` v1/v2（`SUPPORTED_MANIFEST_VERSION=2`），`kind ∈ {standalone,backend,exclusive,platform,model-provider}`，支持 `provides_tools`/`config_schema`/`pip_dependencies`/`python_dependencies`/`requires_env` | `hermes_cli/plugins_manifest.py:29,39-46` |
| 启用方式 | 插件 opt-in：`plugins.enabled` 白名单（路径键或裸名），`plugins.disabled` 优先 | `hermes_cli/plugins_discovery.py:123-128,274,323` |
| 平台注册 | `ctx.register_platform(name,label,adapter_factory,check_fn,...)`；非 bundled 插件可传 `trusted_inbound=True`（仅 core 平台名被拒） | `hermes_cli/plugins_context.py:587-618` |
| 平台适配器 | `class BasePlatformAdapter(ABC)`，抽象方法 **4 个**：`connect(*,is_reconnect=False)`、`disconnect()`、`send(chat_id,content,reply_to=None,...)`、`get_chat_info(chat_id)` | `gateway/platforms/base.py:1780,2593-2632,4631` |
| 入站入口 | `await self.handle_message(MessageEvent(...))`；`build_source(chat_id=...,chat_type="dm",...)` 由基类提供；`_mark_connected()` 标记连接 | `gateway/platforms/base.py:3870`（`plugins/platforms/a2a/adapter.py:335-360,620-627` 为同构范本） |
| **ack 判据** | `handle_message` 内 `event._gateway_accepted = False` 起始；无 handler / 被丢弃 ⇒ 保持 `False`；成功 `_start_session_processing` ⇒ **`True`** | `gateway/platforms/base.py:3872-3912` |
| 注入 API（备选） | `ctx.inject_message(content, role, session_key=..., origin=...)`；`origin` 走 `_dispatch_plugin_origin_injection` → `get_or_create_session` 可**新建** session；但需 `plugins.entries.<id>.allow_gateway_injection: true`，且 session authority 生效时“无既有 session 的 origin 会被拒” | `hermes_cli/plugins_context.py:397-406`、`gateway/run_plugin_injection.py:447-492` |
| 工具注册 | `ctx.register_tool(name, toolset, schema, handler, check_fn=..., ...)`（同名内置需 `override=True` + 运营授权） | `hermes_cli/plugins_context.py:272-296` |
| 后台任务/生命期 | `ctx.spawn_task(coro, name=...)`、`ctx.on_unload(cb)`、`ctx.emit/subscribe`、`ctx.register_hook` | `hermes_cli/plugins_context.py:225-244,753,810` |
| profile 身份 | `ctx.profile_name`、`ctx.plugin_id`、manager 的 `home_path`（插件自己的 profile home，不是全局 `HERMES_HOME`） | `hermes_cli/plugins_context.py:210,55`、`hermes_cli/plugins_injection.py:52-60` |

**由此得到一个比旧方案更强的事实**：Hermes 上“SSE → session”根本不需要本地 HTTP、不需要 webhook 端口、也不需要 `ctx.inject_message` 的授权开关——只要注册一个真正的 platform adapter，SSE 线程把邮件变成 `MessageEvent` 交给 `self.handle_message()`，gateway 的 `get_or_create_session` 负责建/复用 session。

必须写进代码的约束：`plugins.entries.<id>/allow_gateway_injection` **不是**主路径依赖项（只在 §6.7 降级路径 B 需要）。

### 2.4 pysdk 与门禁现状

- `tests/host-acceptance/v2/GATE.md:54`：`hermes`/`deerflow`（pysdk）当前为 `SKIP_LEGACY` —— “reported, never executed, never counted”。
- `tests/host-acceptance/v2/lib/assert.sh:14`：`skip_legacy()` 只打印，不计入 verdict。
- 矩阵目前只能构建/安装 **tssdk**：`run-matrix.sh` 里 `pnpm install && pnpm build && npm pack`，`hosts/*.sh` 里 `dsh plugin add`；无 `--sdk py`，无 `lib/pysdk.sh`，无 `hosts/hermes.sh` / `hosts/deerflow.sh`。
- `run-matrix.sh` 硬性要求 `LLM_API_KEY`；六个 case 的断言口径已稳定（`cases/*.sh`），pysdk 复用同族日志即可。
- S1 断言 binding 的 `system_id` 以 `system-`/`system_` 开头、`agent_system_id` 形如 `asid_*`、`workspace_path`/`workspace_id` 非空、无 `webhook_url`、有 `api_key`、文件 0600（`cases/s1_activation.sh:10-33`）。
- S2 断言 `polling enabled|pull scope|SSE reconcile: [1-9]`（`cases/s2_arming.sh:10`）。
- S4 对 `pi` 走“单地址拒绝”分支（硬编码 `[ "$host" = pi ]`）⇒ pysdk 两个宿主一个单地址（hermes）一个多地址（deerflow），必须改为宿主能力声明（§8.2）。

### 2.5 DeerFlow 宿主能力（在 `aimail-host-deerflow:latest` 镜像内实测）

| 能力 | 实测结论 | 证据（容器内路径） |
|---|---|---|
| **Python 扩展系统** | 正式扩展机制：第三方包暴露 `install(registry, config)`，由 `config.yaml` 顶层 `plugins:` 列表按确定性顺序加载 | `backend/packages/harness/deerflow/extensions/AGENTS.md`、`extensions/loader.py:150`、`app/gateway/app.py:894-916` |
| 扩展入口 | 打包入口 = PEP 621 entry point 组 **`deerflow.extensions`**（例：`example = "pkg:install"`）；`required: true` 加载失败即 fail-boot | `extensions/AGENTS.md` |
| 扩展 API | `ExtensionRegistry`：`plugin(PluginContribution)`、`middlewares()`、`task_lifecycle()`、`service()`、`routers()`、若干 observer（system model / agent assembly / context compaction） | `extensions/registry.py:67-230` |
| **模型工具** | `PluginContribution` 可携带 `ModelTool(name, description, input_schema, handler(args, ToolContext), group)`，`ToolContext(principal, settings, thread_id)` ——**工具调用能拿到 `thread_id`** | `backend/packages/extension-api/deerflow_extension_api/plugins.py` |
| 启用方式 | `config.yaml` 的 `plugins:` 记录（`name`/`package`/`use`/`enabled`/`required`/私有 `config`）；运营面是宿主 CLI `deerflow install/enable/disable/remove`（**宿主** CLI，不是 SDK CLI） | `extensions/AGENTS.md`、`extensions/cli.py`、`extensions/manager.py` |
| **run API** | `POST /api/threads`（`ThreadCreateRequest`，**幂等**：thread_id 已存在则返回既有记录）；`POST /api/threads/{thread_id}/runs`（`RunCreateRequest` + **`Idempotency-Key` 头** → 内部 `start_run(body, thread_id, request, idempotency_key=…)`，立即返回 run 记录）；另有 `/runs/wait`、`/runs/stream` | `app/gateway/routers/threads.py:866`、`routers/thread_runs.py:945,1018` |
| **run API 实测** | 在 `aimail-host-deerflow:latest`（`DEER_FLOW_AUTH_DISABLED=1`）实跑：`GET /openapi.json`=200、`POST /api/threads {thread_id}`=200（返回 thread 记录）、`POST /api/threads/{tid}/runs`=**200 且返回 `run_id` + `status="pending"`** ⇒ 这就是门禁路径可用的“accepted”信号 | 本地容器实测（2026-11-23，脚本见 §14 C） |
| 鉴权 | `DEER_FLOW_AUTH_DISABLED` 覆盖全部鉴权（`app/gateway/auth_disabled.py` 存在，CSRF 中间件据此放行）；另有 `internal_auth.py`（内部可信调用面） | `app/gateway/auth_disabled.py`、`csrf_middleware.py`、`internal_auth.py` |
| 解释器 | 宿主解释器 = `/app/backend/.venv/bin/python`（系统 `python3` 没有 fastapi/PyYAML） | 镜像实测 |
| 环境干净度 | 镜像内**无** `aimail` 模块、无 binding 文件（符合“宿主镜像不预装我方 SDK”纪律） | 镜像实测 |
| 版本 | 基础镜像 tag `deer-flow-gateway:src-fc26204d`，源码检出 `/home/ubuntu/deer-flow` @ `fc26204d`（2026-09-23） | `docker images` + `git -C ~/deer-flow rev-parse HEAD` |

**结论**：deerflow 具备与 hermes 同级的“插件内闭环”条件——扩展在 gateway 进程内，既能注册模型工具（拿到 `thread_id`），也能在后台跑 SSE 消费循环，再把邮件变成一次 **带幂等键** 的 run。

⚠️ 未定项（§7.7 探针）：生产态（`DEER_FLOW_AUTH_DISABLED` 关闭）下，**扩展进程内如何触发 run** —— 走内部 HTTP（需可信 principal）还是进程内服务对象。这是 deerflow 侧唯一的真实技术未知。

### 2.6 网关侧 ING-1 已关闭（本方案 R7 撤销）

| 事实 | 证据 |
|---|---|
| 原报“入站被转发到出站 relay”不成立：inbound 恒走 webhook/pull 分支，症状由夹具端口 off-by-one 解释 | `docs/qa/REQ1-ingress-evidence.md` §1/§2/§3 |
| 真实缺陷“pull 入队后台账行被立即删除（`emails=0`）”已在网关 main `0c46b38` 修复 | 同上 §4（单测 4 新增 + 全量 415/415 + 活体 e2e `emails=1 pending=1 status=delivered`） |
| **已发版**：随 gateway **v1.0.12** 发布（REQ-13 记 `已修并发版`），`pins.env` 现 pin **v1.0.13** | `docs/TSSDK-L2-FIX-PLAN.md:22,58,367`、`tests/agent-env/pins.env:31` |
| 实跑证据：v1.0.13 下 S3 `inbound stored (emails=1/2)` 已在 dsh、pi 通过 | `evidence/l2/round2-20261123/all-s3.log`、`evidence/l2/*/results.txt` |

⇒ **S3 不是网关阻塞项**；pysdk 侧只要按 §5 正确 ack，就能拿到 drained 证据。

---

## 3. 契约 V2 → pysdk 差距矩阵

| ID | 契约要求 | pysdk 现状 | 本方案处置 | 验证 |
|---|---|---|---|---|
| C1 | 单地址宿主拒绝第二地址 | 仅 deer-flow 有 notion，无强制 | 宿主能力声明（hermes=`single`、deerflow=`multi`），`single` 在 bind 时本地拒绝 | 单测 + L2 S4（两宿主） |
| C2 | 仅靠地址激活码绑定，返回 GatewaySystemId | 满足（`activate_address_code`） | 保留；补 `X-Agent-System-Id` | 单测 + L2 S1 |
| C3 | 目录 `systems/<GatewaySystemId>/<addr>` | 满足 | 保留 | 单测 |
| C4 | ASID 同机稳定，home/硬件变化生成新 ID | **缺失** | 新增 `pysdk/v2/agent_system_id.py`（§5.1） | 单测 |
| C5 | 单 Gateway 聚合：只聚合本 ASID 激活地址，一条 SSE | **缺失**（每 binding 一条） | `v2/binding.py` 分组 + `v2/system_pull.py` | 单测 + L2 S4 |
| C6 | 多 Gateway 各一条 SSE，不混 pending/ack | **缺失** | 按 `gateway_url` 分组，client per group | 单测 + 双 Gateway L2 |
| C7 | 同 Gateway 下不同 GatewaySystemId 正确路由 | 部分（按地址） | 投递时 `gateway_url + recipient` 查 binding（fresh） | 单测 |
| C8 | 同 thread followup，新 thread 新 session，空间 = cwd | **缺失**（本地 replay） | `v2/thread.py` + 宿主 session 映射（hermes chat_id / deerflow thread_id） | L2 S3 + 真实宿主 |
| C9 | 成功投递后才 ack；miss/paused 不 ack | **语义错误**（replay 成功即 ack；无 skip） | ack 判据 = 宿主确认（hermes `_gateway_accepted`；deerflow run 记录已建） | 单测 + L2 S3 |
| C10 | 重启：有 binding 即 arm；无 binding 0 SSE | 满足但依赖 pointer | 启动即 reconcile（无需 pointer），无 binding 输出既有 “no agent-scope binding” 行 | L2 S2 |
| C11 | 安全：无 raw key/code 日志；外部预写不算激活 | 部分（F1 曾回传 raw_key） | 日志脱敏 + 不回传 raw_key；只认激活返回的 binding | 单测 + L0 |
| C12 | 解绑停路由/聚合/pointer，不误删其他地址 | 部分（文件即信号） | 文件即信号 + reconcile | L2 S5 |
| C13 | 同系统 move 保持地址；跨系统重新激活 | 部分 | `move` 只改 `workspace_*`，不兑换 code、不新建流 | 单测 |
| §7.1 | binding 必填字段（含 `agent_system_id`、`workspace_id/path`） | 缺 `agent_system_id`、`workspace_*`；多 `webhook_*` | 写契约字段；`webhook_*` 仅 legacy 读兼容 | L2 S1 |
| §8.1 | 认证用 Gateway 签发的 ASID 凭据 | 地址 key + `X-Api-Identity` 有，ASID 头无 | client 支持路径/额外头覆盖；聚合 client 唯一构造函数 | 单测 |
| §8.2 | 心跳/退避重连；重连后不重复建 session/ack | pysdk 已有成熟状态机 | 复用 `start_polling`（已与 tssdk parity） | 单测（既有） |
| §8.4 | status `{mode,agentSystemId,gateways[]}` | 无 | `v2/status.py` 写 `<aimail_home>/state/sse-status.json` | 单测 |
| §9.1-9.9 | 路由 10 步 | 无 | `v2/delivery.py` 编排（§5.5） | 单测 + L2 |
| §11 | 单/多地址宿主能力矩阵 | 未声明 | 能力声明表（§6.1 hermes=single / §7.1 deerflow=multi） | 文档 + L2 S4 |
| §12 | 混合态 S0..S10 均可启动 | 部分 | reconcile 容错：单 binding/单 Gateway 失败不影响其它 | 单测 |
| §13 | pause/resume/unbind/move | 满足（文件即信号） | 保留，补 reconcile 触发 | L2 S5 |
| §14 | 工具面 = 邮件 + board + `aimail_address` | 满足（无 `aimail_system`） | 保留 | L0 契约门禁 |

---

## 4. 目标架构

### 4.1 分层

```text
┌─ core（保留，平台无关）─────────────────────────────────────────────┐
│ aimail_base.py   业务核心：identity/persona/enrichment/attachment/    │
│                  thread/search/ping-pong，binding 读写，签名          │
│ aimail_tools.py  _GatewayClient（签名/stream/poll 状态机）+ 邮件工具   │
│ aimail_board.py  board/A2A 工具                                       │
│ tool_spec.py + tool-spec.json  工具 schema 单一源                     │
└──────────────────────────────────────────────────────────────────────┘
┌─ v2（新增，共享 V2 运输与路由，两宿主共用）─────────────────────────┐
│ agent_system_id.py  ASID 派生/缓存/seam                               │
│ binding.py          binding 枚举、V1/V2 判定、分组、fresh lookup       │
│ pull_client.py      聚合 client 唯一构造函数（路径 + 头）              │
│ system_pull.py      每 (ASID, Gateway) 一条流 + stats + auth 致命      │
│ delivery.py         delivery → 预处理 → 宿主投递 → ack 决策            │
│ thread.py           thread root / thread meta                         │
│ outbound.py         outbound Message-ID alias（回复追溯）              │
│ ack.py              ack 策略（唯一出口）                              │
│ status.py           §8.4 状态文件 + 审计行                             │
└──────────────────────────────────────────────────────────────────────┘
┌─ hosts（宿主适配，只放宿主差异）────────────────────────────────────┐
│ hosts/capabilities.py     宿主能力声明（single/multi、空间类型）        │
│ hermes_plugin/            Hermes plugin platform adapter（§6）          │
│ deerflow_plugin/          DeerFlow extension（§7）                     │
└──────────────────────────────────────────────────────────────────────┘
┌─ legacy（只读/显式弃用，不再作为主路径）────────────────────────────┐
│ install.py / sdk_ops.py / hermes/{ensure_config,patch_webhook,...}.py  │
│ deer-flow/aimail_inbound.py / bridge 引用                              │
└──────────────────────────────────────────────────────────────────────┘
```

### 4.2 依赖方向（硬规则）

```text
core  ←  v2  ←  hosts
```

- `v2/*` 不 import 任何宿主模块；
- `hosts/*` 只允许 import `v2/*` + `core`；
- `core` 不得 import `v2/*`；ASID 通过**参数注入**（`agent_system_id=`）进入 core 的激活/persist 路径。

### 4.3 业务闭环（V2，两宿主同构）

```text
SSE（aggregate，X-Agent-System-Id）
  └─ delivery{ id, email, headers, body }
       ├─ 1 binding lookup（gateway_url + recipient，fresh）   → miss ⇒ 不 ack
       ├─ 2 paused ⇒ skip（不 ack）
       ├─ 3 thread root 解析 + thread meta 读取
       ├─ 4 预处理（identity/persona/enrichment/attachment/ping-pong）
       ├─ 5 交给宿主：
       │     hermes   → adapter.handle_message(MessageEvent)
       │     deerflow → POST /api/threads/{tid}/runs（Idempotency-Key）
       ├─ 6 宿主 accepted ⇒ 写 thread/session meta
       └─ 7 ack（只对 accepted 的 delivery id，只发给该 Gateway）
```

---

## 5. 共享 V2 模块详细设计

### 5.1 `v2/agent_system_id.py`

```text
derive(agent_home, hardware_fingerprint)
  -> "asid_" + sha256("aimail-agent-system-v1\0" + realpath(agent_home) + "\0" + fp)[:32]
```

1. `agent_home` 先 `realpath` + strip；空 ⇒ 抛错（不得静默退化到 `/`）；
2. `fp` strip 后为空 ⇒ 抛错；`fp` = `hostname|platform|arch|machine-id`（`/etc/machine-id` → `/var/lib/dbus/machine-id` → `no-machine-id`）；
3. env seam 三个（与 tssdk 同名）：`AIMAIL_AGENT_SYSTEM_ID`、`AIMAIL_HARDWARE_FINGERPRINT`、`AIMAIL_AGENT_HOME`；
4. 进程内缓存 + `reset_for_tests()`；
5. 宿主 seam `set_agent_home_resolver(fn)`：hermes → profile home；deerflow → 宿主 home（`/app` 或 `DEERFLOW_HOME`）；
6. 不落盘为“身份文件”（契约 §5.1：ASID 不是 secret），只在 binding 里出现；
7. reconcile 时与 binding 的 `agent_system_id` 比对，不一致 ⇒ 跳过 + 告警 `skip binding <addr>: agent_system_id <x> != <y> (re-activate)`。

### 5.2 `v2/binding.py`

```python
is_v1_agent_scope(cfg)      # system_id.startswith("shared_addr_")   ← 保留，仅过渡
is_v2_agent_scope(cfg)      # bool(cfg.get("agent_system_id"))
is_agent_scope_binding(cfg) # is_v1 or is_v2      ← B1 修法的 pysdk 版
```

- `iter_active(agent_system_id, gateway_url=None)`：过滤 `paused` / 无 `api_key` / foreign ASID；同地址多份文件按地址去重（排序稳定）；
- `group_by_gateway(bindings)`：键 `(agent_system_id, gateway_url.rstrip('/'))`；**rep** = 组内按 email 排序的第一个 active；
- `fresh_target(email, gateway_url)` ⇒ `(cfg, skip)`：无 binding ⇒ `skip='unknown'`；`paused` ⇒ `skip='paused'`；否则 `(cfg, None)`；
- `count_active(...)`：给 `SSE reconcile` 审计行用。

硬规则：建流阶段的分组是**快照**，投递阶段必须 `fresh_target` 重新查（tssdk P0-6/P0-7）。

### 5.3 `v2/pull_client.py`

```python
def build_agent_system_client(gateway_url, api_key, agent_system_id, identity) -> _GatewayClient
```

- 端点（三条，全部聚合作用域）：`/api/v1/agent-system/pending/stream`、`/api/v1/agent-system/pending`、`/api/v1/agent-system/pending/ack`；
- 头：`X-Agent-System-Id: <asid>`；保留既有 `X-Api-Identity` + 签名头 + `X-Aimail-Client`；
- `_GatewayClient` 只加两个能力（**唯一改动点**）：`stream_path`/`pending_path`/`ack_path` 可覆盖、`extra_headers` 合并到每个请求（含 SSE GET 与长拉取）；
- 禁止任何模块自建 V1 路径的聚合流（B2 的教训）。

### 5.4 `v2/system_pull.py`

- 每流一个 daemon 线程，复用 `_GatewayClient.start_polling`（auto/stream/poll、ping 30s、read 90s、hold 50s、退避 1..30s、3 次降级）；
- `stats` 实时可读；认证连续 3 次 401/403 ⇒ 该流 fatal 停止（其它 Gateway 不受影响）；
- `on_email` 返回 `PullDeliveryResult`（含 `skip`），`start_polling` 的投递/ack 循环需升级支持 `skip`。

### 5.5 `v2/delivery.py`（编排层，宿主调一次）

| 步 | 动作 | 失败时 |
|---|---|---|
| 1 | `binding.fresh_target(email, gateway_url)` | miss ⇒ `skip('unknown')`，不 ack |
| 2 | `paused` | `skip('paused')`，不 ack |
| 3 | 空间解析（宿主能力 → workspace） | 无空间 ⇒ 不 ack + 原因 |
| 4 | `thread_root(headers)` = `references[0] → in_reply_to → message_id` | 全空 ⇒ 新 thread |
| 5 | `thread.meta()` 读 `session_id`/`workspace_path` | 无 meta ⇒ 新建 |
| 6 | 同 thread + 同 workspace + session 可用 ⇒ followup | — |
| 7 | 否则 new（cwd = workspace） | 建会话失败 ⇒ 不 ack |
| 8 | 预处理（宿主投递**之前**、目标 profile runtime **之内**） | 失败 ⇒ 不 ack |
| 9 | `host.deliver(event)` | accepted=False ⇒ 不 ack |
| 10 | accepted ⇒ 写 meta + `ack.ack([id])` | — |

预处理结果 `None`（ping/pong 被管线吞掉）⇒ **算 accepted**，但不建 session、不写 meta（必须单测，否则 ping 邮件永久 pending）。

### 5.6 `v2/ack.py`（唯一出口）

| 情形 | ack |
|---|---|
| hermes：`event._gateway_accepted is True` | ✅ |
| deerflow：`POST …/runs` 2xx 且返回 run 记录（含 `run_id`） | ✅ |
| 宿主 queued（进入 FIFO / 会话忙但已被接受） | ✅ |
| `skip('paused'/'unknown')` | ❌（保持 pending，不算 error） |
| 预处理失败 / 宿主拒绝 / 会话创建失败 / 网络失败 | ❌（算 error，按退避重试） |
| 无 binding、host absent | ❌ |

要点：只 ack 本次 accepted 的 id；只用该组 client（多 Gateway 不交叉）；ack 失败不回滚已投递事实（由 §5.8 幂等吸收）；**fail-closed**（拿不到宿主确认证据即不 ack）。

### 5.7 `v2/thread.py` + 宿主 session 映射

- `thread_root(headers)` 与 `session-router.ts` 逐字同规则；
- thread meta：`<aimail_home>/systems/<GatewaySystemId>/<clean_addr>/mail/threads/<first2>/<tid>.json`，字段 `{thread_root, session_id, workspace_path, gateway_url, updated_at}`（不含 key/正文）；
- `route(...)`：`session_id` 存在且（`meta.workspace_path` 空或相等）⇒ followup，否则 new；
- 宿主映射：
  - **hermes**：`chat_id = aimail:` + thread_root 短哈希 ⇒ session key `agent:<profile>:aimail:dm:<chat_id>`；
  - **deerflow**：`thread_id = uuid5("aimail-thread", gateway_system_id + address + thread_root)`（确定性、可重建，不依赖 meta 发现）。

### 5.8 幂等

1. **delivery 级**：`(gateway_url, delivery id)` 去重集（内存，随流生命期）；
2. **邮件级**：`email:<message_id>` 已处理标记（LRU + 落盘索引）；
3. **thread 级**：`thread_root → session_id/thread_id` 映射；
4. **宿主级**：hermes 由 `chat_id` 决定 session（重复投递进同一 session）；deerflow 由 `Idempotency-Key: aimail:<message_id>` 去重（宿主原生，§2.5）。

### 5.9 `v2/status.py`

`<aimail_home>/state/sse-status.json`（0600），内容严格按契约 §8.4；每 Gateway 一行；无 key/code/正文；每 30s 与每次 reconcile 刷新。

### 5.10 审计日志行（L2 断言口径，逐字稳定）

| 行 | 用途 |
|---|---|
| `[aimail-pull] SSE reconcile: <N> gateway stream(s) from <M> binding(s)` | S2/S4 |
| `[aimail-pull] aggregate agent:<asid> gw=<url> members=[...] (rep <addr>, a/b active)` | 诊断 |
| `[aimail-pull] no agent-scope binding — polling not started ...` | S2 排序依赖 |
| `[aimail-pull] skip <addr>: paused` / `skip <addr>: unknown target (mail stays pending)` | 排查未 ack |
| `[aimail-pull] ack <n> id(s) gw=<url>` | 交付证据 |
| `[aimail-v2] auth fatal on gw=<url> (status=401, streak=3) — stream stopped` | 认证故障 |

---

## 6. Hermes 落地设计（门禁宿主 A）

### 6.1 宿主能力声明

```text
host: hermes
address_model: single          # 一个 profile = 一个地址（契约 §11.2）
space:  profile home
session_container: profile session namespace（agent:<profile>:aimail:dm:<chat_id>）
agent_system_id: 每 profile 一个（home = profile home）
sse: 每 (AgentSystemId, Gateway) 一条，per profile
```

理由沿用 `docs/hermes-multiplex-v2-refactor-plan.md` §1：一条 gateway 服务多 profile、二级 profile 禁止 port-binding platform、profile 是隔离边界、公共插件 API 不能跨 profile 注入 ⇒ 不做 profile 内多地址聚合。

### 6.2 安装：从 CLI 改为 pip / 宿主插件机制

| 项 | 现在 | 目标 |
|---|---|---|
| 装 SDK | `aimail install`（CLI 复制树 + patch + 写配置） | `pip install agent-aimail`（wheel） |
| 让宿主发现 | CLI patch `toolsets.py` + `webhook.py` + `config.yaml` | 插件清单 + entry point：`<profile home>/plugins/aimail/plugin.yaml`，或 wheel 的 entry point 组 `hermes_agent.plugins` |
| 启用 | CLI 写 `platforms.webhook.enabled` + 端口 + secret | 宿主侧 `plugins.enabled` 白名单含 `aimail`（宿主设置/宿主 CLI） |
| 声明依赖 | 无 | `plugin.yaml` 的 `pip_dependencies`/`python_dependencies` |

```text
aimail/hermes_plugin/
├── plugin.yaml            # v2 清单，kind: platform
├── __init__.py            # register(ctx)
├── adapter.py             # AIMailAdapter(BasePlatformAdapter)
├── runtime.py             # SSE reconcile + 后台任务
├── identity.py            # profile home / AgentSystemId
└── tools.py               # register_tool 适配（schema 来自 tool-spec.json）
```

```toml
[project.entry-points."hermes_agent.plugins"]
aimail = "aimail.hermes_plugin:register"
```

实测注意：entry point 插件 manifest 是 metadata-only（`kind` 由源扫描推断）；`plugins.enabled` 是 opt-in 白名单。

### 6.3 `register(ctx)`

```python
def register(ctx) -> None:
    ctx.register_platform(
        name="aimail", label="AIMail",
        adapter_factory=lambda cfg: AIMailAdapter(cfg, ctx=ctx),
        check_fn=lambda: True, validate_config=lambda cfg: True,
        is_connected=_is_connected,       # 本 profile 有 active binding 即连接
        trusted_inbound=True,             # 服务型入站（无人类 sender）
        required_env=[], install_hint="pip install agent-aimail",
        platform_hint=_PLATFORM_HINT,
    )
    for tool in agent_toolset():          # tool-spec.json 单一源
        ctx.register_tool(name=tool["name"], toolset=TOOLSET, schema=tool["schema"],
                          handler=tool["handler"], description=tool["description"],
                          is_async=_is_async(tool["handler"]))
    ctx.on_unload(lambda: _runtime(ctx).stop())
```

`register()` **不做网络 I/O**（宿主有 10s 加载 deadline）；SSE 在 `connect()`/后台任务里起。

### 6.4 `AIMailAdapter`

| 成员 | 实现 |
|---|---|
| `async connect(*, is_reconnect=False)` | 捕获 loop；启动 SSE runtime；`_mark_connected()`；`return True`；**不 bind 端口** |
| `async disconnect()` | 停流、清 loop、`_mark_connected(False)`；幂等 |
| `async send(chat_id, content, reply_to=None, ...)` | 转成出站邮件（带 `In-Reply-To`）；写 outbound alias（§6.6）；返回宿主 `SendResult` |
| `async get_chat_info(chat_id)` | 从 thread meta 返回 `{name: <subject 摘要>, type: "dm"}` |
| 入站桥 `deliver(envelope)` | `MessageEvent(..., source=self.build_source(chat_id=..., chat_type="dm"), internal=True, allow_gateway_control=False)` → `await self.handle_message(event)` → 回读 **`event._gateway_accepted`** |

`trusted_inbound=True` 只表示豁免 pairing/allowlist，收件人 binding 与 paused 校验仍必须做。

### 6.5 runtime

```text
触发：插件加载 / binding-created hook / pause / resume / unbind / 每 10 分钟对账
动作：stop_all() → asid → iter_active(asid) → group_by_gateway → 每 Gateway start_system_pull
日志：SSE reconcile: <N> gateway stream(s) from <M> binding(s)
容错：单 binding / 单 Gateway 失败不影响其它组（契约 §12 S6）
空态：无 binding ⇒ 输出 “no agent-scope binding — polling not started …”
```

### 6.6 回复追溯

`send_mail` 与 `adapter.send()` 都必须：带 `In-Reply-To` + `References`；取回/记录出站 Message-ID；写 `outbound Message-ID → thread_root` alias。subject-only 匹配默认关闭；未知引用 ⇒ 新 thread；跨 profile/ASID 不猜 session。

### 6.7 降级路径

| 级别 | 路径 | 何时用 | 代价 |
|---|---|---|---|
| A（主） | `register_platform` + `adapter.handle_message` | 默认 | 无 |
| B | `ctx.inject_message(origin=/session_key=)` | platform adapter API 不可用 | 需 `allow_gateway_injection: true`；session authority 下不能新建 session |
| C（禁） | 本地 HTTP replay / webhook 端口 / host patch | **不使用** | 与 G1/G2 冲突 |

降级必须由夹具探针（§8.4）判定，不允许运行时静默降级。

### 6.8 删除/降级清单（Hermes）

`ensure_config.py`（webhook 分支）、`patch_webhook.py`（移出 wheel）、`register_profiles.py`（删除）、`aimail_hermes.py` 的 replay 三函数（由 `v2/*` 替代）、`toolsets.py`（由 `register_tool` 替代）、`install.py`/`sdk_ops.py` 写操作（主路径移除；只留只读）、binding 的 `webhook_*` 字段（不再写）、`aimail_system`（不注册）。

---

## 7. DeerFlow 落地设计（门禁宿主 B）

### 7.1 前置：干净的 L2 agent 环境（**E1–E8 已完成**，可与 P1 并行）

**目标**：一个可复现的 deerflow L2 宿主镜像 —— 不含我方 SDK、能真实回答 LLM、能被夹具驱动一次真实 agent turn。

| # | 事项 | 状态 | 落地物 / 证据 |
|---|---|---|---|
| E1 | 源码钉版 | ✅ | `pins.env`：`DEERFLOW_REV=fc26204d` + `BASE_DEERFLOW`；`rebuild-deerflow-base.sh --check` 校验 pins ⇄ 源码（`~/deer-flow` @ `fc26204d`，dirty=0）⇄ 本地镜像三方一致 |
| E2 | 基础镜像可重建 | ✅ | 脚本收编进仓库 `tests/agent-env/docker/rebuild-deerflow-base.sh`：`DEERFLOW_SRC` / `DEERFLOW_FLOOR_IMAGE` / `UV_CACHE_HOST_DIR` / `DEERFLOW_BASE_TAG` 全参数化、无硬编码路径；含 `auth_disabled` 验收与 CMD/ENTRYPOINT 完整性断言；`--check` 只校验对齐 |
| E3 | 宿主镜像 | ✅ | 用仓内 `Dockerfile.deerflow` 构建成功（新增 `DEERFLOW_REV` label 溯源）；`prepare.sh deerflow --home /app` + `verify.sh --offline` 全绿；`host-images.yml` 增 `deerflow` 分支（fail-closed：runner 必须有 `$BASE_DEERFLOW`，绝不回退 `:latest`） |
| E4 | 干净度审计 | ✅（并修掉一处**真污染**） | `audit-hosts.sh` 增 deerflow 探针与 `--clean-only`（无 key 也能审计）。**实测发现镜像 uv 缓存带着我方两个 PyPI 名（`aimailsdk`、`agent-aimail`）**——`Dockerfile.deerflow` 因此新增 fail-closed 清理层（清 `/root/.cache` 下我方条目，`.aimail` 运行态目录显式排除）；现审计 6 项全 ✓ |
| E5 | LLM 可用 | ✅（并修掉一处**真缺陷**） | 原 `write_deerflow` 把 `local-qwen-dummy` 当 `api_key` 写进镜像（“看着已配置”的假绿）⇒ 改为 `__LLM_API_KEY__` 占位；`set-llm-key.sh` 新增 `deerflow` 分支运行期注入真 key（容器内实测 `patched 1 api_key line(s)`）。镜像 `config.yaml` 现为 gate 端点 + 占位符 |
| E6 | 夹具驱动 | ✅（driver + 宿主无关门禁；`hosts/deerflow.sh` 留到 P5） | `lib/deerflow_drive.py`（stdlib）：`POST /api/threads`（幂等）→ `POST /api/threads/{tid}/runs`（带 `Idempotency-Key`）→ `/messages` 读回；`lib/deerflow_probe_ext.py`（**测试用**扩展：注册一个 `ModelTool`，把 `thread_id` 落 marker）；`lib/deerflow-env-gate.sh` 把 E1–E7 串成可执行门禁 |
| E7 | 鉴权 | ✅ 实测 | `DEER_FLOW_AUTH_DISABLED=1` 起 gateway：`/openapi.json`=200、`POST /api/threads`=200、`POST …/runs`=200 且返回 `run_id`+`status=pending`（即 ack 的 accepted 信号） |
| E8 | 解释器纪律 | ✅ | 门禁与 driver 用 `/app/backend/.venv/bin/python`；构建期 `verify.sh --offline` 断言 `app import ok` |
| E9 | 旧临时资产 | ✅ 决议：**不收编** | `/tmp/tmp.*/extras/*deerflow*.sh` 属 push 时代夹具（判据是旧入站 HTTP 路径），与 V2 判据冲突；新 driver + env gate 已覆盖其能力，旧脚本留作历史、不进仓库 |

**E 阶段退出判据（当前实测）**：`deerflow-env-gate.sh --no-llm` → **PASS=8 FAIL=0**：
pin 一致 / 镜像干净 / gate 端点 / 无烘入凭据 / 模型声明 / gateway 启动 / `install(registry, config)` 执行 / run 被接受。
第 7 步（模型真的调用已注册工具）需 `LLM_API_KEY`，当前 **BLOCKED**（fail-closed，不判绿）。

```bash
# 复跑（E 轨道门禁）
bash tests/host-acceptance/v2/lib/deerflow-env-gate.sh --no-llm        # 1-6 步，现在可跑
LLM_API_KEY=… bash tests/host-acceptance/v2/lib/deerflow-env-gate.sh  # 含第 7 步
```

**唯一待办**：第 7 步需要真实 LLM key（`XIAOMI_API_KEY`）在运行时注入 —— 属凭据可用性，不属环境缺口。

### 7.2 宿主能力声明（产品设计）

```text
host: deerflow
address_model: multi            # 一个 gateway 进程 + 多 assistant
space:  assistant / thread（thread 自带 workspace）
sse: 每 (AgentSystemId, Gateway) 一条，一个 SSE 覆盖多地址：address → assistant
session: thread_id = uuid5("aimail-thread", gateway_system_id + address + thread_root)
agent_system_id: 一个 deerflow 实例一个（home = DEERFLOW_HOME）
```

### 7.3 插件形态（宿主扩展，不是 MCP + HTTP）

```text
aimail/deerflow_plugin/
├── __init__.py            # install(registry, config)  ← entry point 目标
├── tools.py               # registry.plugin(PluginContribution(ModelTool(...)))
├── runtime.py             # registry.task_lifecycle(...) 里起停 SSE 循环
└── identity.py            # DEERFLOW_HOME / AgentSystemId
```

```toml
[project.entry-points."deerflow.extensions"]
aimail = "aimail.deerflow_plugin:install"
```

宿主侧启用 = `config.yaml` 的 `plugins:` 记录（`enabled: true`），由宿主 CLI `deerflow install/enable` 或容器构建期写入 —— 都是**宿主机制**，不需要我方 CLI。

工具示例（线程上下文由宿主给，不用自己猜）：

```python
def _send_mail(args, ctx: ToolContext):     # ctx.thread_id 由宿主提供
    return tools.send_mail(args)

registry.plugin(PluginContribution(tools=[
    ModelTool(name="aimail_address", description=..., input_schema=..., handler=_address),
    ModelTool(name="send_mail", ...),
    ...
]))
```

### 7.4 入站投递（SSE → run）

```text
delivery
  -> binding.fresh_target(email, gateway_url)           # 缺失/paused ⇒ skip，不 ack
  -> thread_root(headers) -> thread_id（确定性 uuid5）
  -> POST /api/threads {thread_id, metadata:{aimail_email}}      # 幂等：已存在即返回
  -> POST /api/threads/{thread_id}/runs
       body = RunCreateRequest(input=…, metadata={aimail_email, aimail_thread_root})
       header  Idempotency-Key: aimail:<message_id>              # 宿主原生去重
  -> 2xx 且返回 run 记录（run_id） ⇒ accepted ⇒ ack
     失败/4xx/5xx ⇒ 不 ack（留 pending）
```

要点：

- **幂等交给宿主**：`Idempotency-Key` + 幂等 thread 创建 ⇒ 重投不会重复开 run（比自造 `if_not_exists` 更可靠）；
- **不再使用**旧的 push 入站路由（常量 `INBOUND_PATH`）与 `aimail_inbound.py` 的 FastAPI 面；
- run 只关心“是否被接受建记录”，不阻塞等待 agent 完成（accepted 语义与契约 §8.3 一致）；
- agent 的回复仍走 `send_mail` 工具（带 `In-Reply-To`/`References` + outbound alias）。

### 7.5 与现状的差异（`pysdk/deer-flow/*`）

| 现状 | 目标 |
|---|---|
| `aimail_inbound.py`（FastAPI inbound，push） | 删除主路径；V2 用扩展内 SSE |
| thread 固定按地址 `uuid5("aimail", email)` | 按邮件 thread root（新邮件新 thread、回复同 thread） |
| `_ACTIVE_AGENT_CONFIG` / `AIMAIL_AGENT_EMAIL` 进程全局 | `contextvars` 或显式传 cfg |
| `multitask_strategy="reject"` | 不依赖该策略；用宿主 `Idempotency-Key` + accepted/ack 对齐 |
| 工具经 MCP server 注册（`install-mcp.sh`） | 扩展 `ModelTool` 注册（MCP 保留为兼容面，不进 V2 门禁） |
| 无 ASID / 无聚合 | 复用 `v2/*`，与 hermes 同核心 |

### 7.6 单例与多租户

- 一个 deerflow gateway 进程内 SSE runtime 唯一（`task_lifecycle` 单实例），多 worker 部署需 leader lock；
- `address → user → assistant` 显式映射，不跨租户；
- `Principal`/`user_id` 由宿主提供，AIMail 不自行编造（资源归属仍按 address binding）。

### 7.7 探针与未定项（必须先证，再开发）

| # | 未定项 | 判定方式 | 失败对策 |
|---|---|---|---|
| D1 | 生产态（`DEER_FLOW_AUTH_DISABLED` 关）扩展进程内触发 run 的可行面 —— **门禁形态已证可行**（auth disabled 下 threads/runs 均 200，见 §2.5）；生产态仍待定 | 探针：生产配置下用内部可信调用（`internal_auth.py`）或进程内服务对象发起一次 run | 退回“宿主运维配置提供内部凭据”，并明确记为**部署前置**（不影响 L2 门禁） |
| D2 | `task_lifecycle` 的启停语义（是否覆盖 app lifespan 与热重载） | 探针：扩展里起一个 5s 心跳日志，观察 start/stop/shutdown | 用 `routers` + lifespan hook 兜底 |
| D3 | `ModelTool` 契约（宿主如何接受/暴露扩展工具）—— **已探明**（见下方 D3 结论） | 已在 env gate 的第 5/7 步实测 | 已落地：`PluginContribution(namespace, title, enabled=True)` + `ModelTool`；工具名被宿主哈希化这一约束记入 R15 |
| D4 | run 的 `Idempotency-Key` 重复投递行为（返回同一 run 还是 409） | 探针：同一 key 连发两次 | 两种都按 accepted 处理；409 视为 accepted（已有 run） |
| D5 | 多地址（address→assistant）在 run API 上的表达（assistant_id vs 配置） | 探针：两地址两 assistant 各起一笔 run | 退化为“一地址一 deerflow 实例”（门禁仍可绿，能力降级需记录） |

---

## 8. L2 门禁接入（两个 pysdk 宿主都从 SKIP_LEGACY 变实跑 PASS）

### 8.1 门禁口径（不改动词表）

| token | 含义 |
|---|---|
| `PASS` / `RED` / `GAP` | 不变（`lib/assert.sh`） |
| `SKIP_LEGACY` | 本次交付后 **hermes 与 deerflow 都不得再出现** |

交付判据（每宿主三轮）：

```text
run-matrix.sh --sdk py --host <host> --stages "s1 s2 s3 neg"   → RED=0 GAP=0 SKIP_LEGACY=0
run-matrix.sh --sdk py --host <host> --stages "s4"             → 同上
run-matrix.sh --sdk py --host <host> --stages "s5"             → 同上
host ∈ {hermes, deerflow}
```

### 8.2 runner 扩展（最小侵入）

| 改动 | 内容 |
|---|---|
| `run-matrix.sh` | 新增 `--sdk ts\|py`（默认 `ts`）；`py` 时用 `lib/pysdk.sh` 构建 wheel；`--host` 允许 `hermes`/`deerflow`（`--host all` = `dsh pi openclaw hermes deerflow`） |
| `lib/pysdk.sh`（新增） | `python3 -m pip wheel . -w <dir> --no-deps`（实测可用，build isolation 自动取 hatchling）；导出 `SDK_FILE`/`SDK_VER` |
| `hosts/hermes.sh`（新增） | `aimail-host-hermes`：pip 装 wheel → 写 `plugins.enabled` → 起 gateway → bind prompt → binding/模式位 → 重启 arm → 注入探针 → 判 ack；`HOST_ADDRESS_MODEL=single` |
| `hosts/deerflow.sh`（新增） | `aimail-host-deerflow`：pip 装 wheel 进 venv → 写 `config.yaml` 的 `plugins:` → 起 gateway（`DEER_FLOW_AUTH_DISABLED=1`）→ 经 `lib/deerflow_drive.py` 建 thread/发 run → bind prompt → binding → 重启 arm → 注入探针 → 判 ack；`HOST_ADDRESS_MODEL=multi` |
| `cases/s4_multiaddr.sh` | 硬编码 `[ "$host" = pi ]` → 读 `HOST_ADDRESS_MODEL`（`single` ⇒ 拒绝第二地址；`multi` ⇒ 双绑定/聚合流） |
| `lib/evidence.sh` | `run.json` 增 `sdk_kind`、`host_plugin_source`（`entrypoint`\|`dropin`\|`config-plugins`） |

### 8.3 两宿主的 journey 差异（同一套 case，不同夹具）

| 阶段 | hermes | deerflow |
|---|---|---|
| 安装 | pip + `plugins.enabled` | pip 进 `/app/backend/.venv` + `config.yaml: plugins:` |
| 起宿主 | 宿主默认启动 | uvicorn（venv 解释器）+ `DEER_FLOW_AUTH_DISABLED=1` |
| 发 prompt | 宿主会话（web/URL） | `lib/deerflow_drive.py`：`POST /api/threads` → `POST /api/threads/{tid}/runs`（同一 thread 多轮） |
| 取 binding | `<profile home>/.aimail/...` | `/app/.aimail/...`（`AIMAIL_HOME` 显式导出） |
| arm 证据 | `SSE reconcile` 行 | 同（同一份 `v2/*` 代码） |
| 注入/ack | 网关 SMTP+1 → `pending_deliveries` 归零 | 同 |

夹具硬性规则（沿用已踩过的坑）：装 wheel/tarball 而非解包目录；内层 `bash -lc '…'` 不得含单引号；多轮 prompt 同一会话；等待按状态变化；断言只 grep 契约级日志行。

### 8.4 宿主能力探针（防宿主升级静默破坏）

| 宿主 | 探针 | 失败处置 |
|---|---|---|
| 通用 | 插件被识别（插件列表/日志含插件名，且工具数 > 0） | `RED`（插件没装上） |
| 通用 | 宿主进程无我方 listener（`ss -ltnp` 与基线比对） | `RED`（回了旧路径） |
| hermes | `BasePlatformAdapter` 可导入 + 4 抽象方法存在 | `GAP`（宿主版本不兼容） |
| hermes | `PluginContext` 有 `register_platform`/`register_tool`/`on_unload` | `GAP` |
| hermes | `handle_message` 源码含 `_gateway_accepted` 赋值 | `RED`（ack oracle 失效，须改判定，不得静默 ack） |
| deerflow | `deerflow.extensions` 可导入，`load_extensions` 接受我们的记录 | `GAP` |
| deerflow | `POST /api/threads` + `…/runs` 均 2xx 且返回记录 | `RED`（run 面不可用） |
| deerflow | 镜像干净度：`import aimail` 失败、无 binding 文件 | `RED`（环境被污染） |
| deerflow | 无签名 POST 不再 CSRF 403 | `RED`（底座陈旧，E7） |

### 8.5 S1..S5/neg 的断言差异

| case | hermes | deerflow |
|---|---|---|
| S1 | 无 `webhook_*`、`agent_system_id`=`asid_*`、`workspace_path`=profile home | 同上（`workspace_path`=`/app` 或 assistant workspace） |
| S2 | `SSE reconcile: 1 gateway stream(s) from 1 binding(s)` | 同（单 Gateway） |
| S3 | `emails>=1` → pending → drained | 同；额外证据：run 记录存在（`Idempotency-Key` 命中同一 run） |
| S4 | `single`：第二地址被拒、无第二条流 | `multi`：两绑定共存 + `from 2 binding(s)` + 两条 address→assistant 映射可用 |
| S5 | pause/unbind 生效、流数变化 | 同 |
| neg | 非法 code 不写 binding/不 arm | 同 |

---

## 9. 分阶段实施（含并行轨道）

```text
        ┌─ 轨道 A（产品/E1..E9 环境）──┐
P0 ─┬───┤                              ├─→ P5 deerflow 门禁
    │   └──────────────────────────────┘
    ├─ P1 共享核心 → P2 hermes 插件 → P3 hermes 门禁
    └─ P4 回复追溯 → P6 legacy 移除与发版
```

| Phase | 内容 | 工期 | 退出判据 |
|---|---|---|---|
| **P0** 事实锁定与探针 | hermes 探针（§8.4）+ deerflow 探针（§7.7 D1-D5 + §8.4）；`v2/` 骨架；两宿主版本 pin 记录 | 1 天 | 探针全绿；D1/D3/D5 有结论 |
| **P1** 共享 V2 核心 | §5.1–5.6 + `_deliver_batches` 支持 skip + 激活带 ASID 头 + persist 写 ASID/workspace | 3–4 天 | ✅ **已完成**（交付记录见文末 §15）：`pytest tests/sdk` **441 passed**（含 26 项 V2 单测）；L0 gate PASS；wheel 校验 PASS |
| **P2** hermes 插件 | §6.3–6.5（adapter/runtime/tools/identity）+ wheel entry point | 4–5 天 | ✅ **代码完成**（见 §15.4）：`hermes_plugin/` 6 文件 + wheel entry point + 13 项单测；真实宿主的“装→启用→bind→收信→ack”闭环随 P3 夹具一起验（需要 runs 宿主机） |
| **P3** hermes 门禁 | `lib/pysdk.sh` + `hosts/hermes.sh` + `--sdk py` + `s4` 宿主能力化 | 2–3 天 | hermes 三轮 `RED=0 GAP=0 SKIP_LEGACY=0` |
| **E**（可并行，尽早启动）| §7.1 E1–E9：基础镜像可重建、pin、CI 分支、干净度审计、journey driver | 3–5 天 | 干净镜像能跑一轮真实 agent turn（模型真的调了测试工具） |
| **P5** deerflow 产品 | §7.3–7.5：扩展（tools/runtime）+ SSE→run 投递 + `hosts/deerflow.sh` | 5–6 天 | deerflow 三轮 `RED=0 GAP=0 SKIP_LEGACY=0` |
| **P4** 回复追溯 | `v2/outbound.py` + `send_mail`/`adapter.send` 接入 + 未知引用策略 | 2 天 | 外部回复命中原 thread；新邮件新 session/thread |
| **P6** legacy 移除与发版 | §6.8 清理 + 版本 `0.2.9 → 0.3.0` + README/发布说明 + L0/wheel/L2 全绿 | 2 天 | §13 全勾 |

关键路径：P1 → P2 → P3；E 与 P1/P2 并行，是 P5 的前置。总工期约 **3 周**（单人串行约 4 周）。

---

## 10. 测试矩阵

| 层 | 用例 | 覆盖 |
|---|---|---|
| 单测 | ASID 派生确定性/变化/seam | C4 |
| 单测 | V1/V2 判定、foreign ASID 跳过、paused、去重 | C5/C1 |
| 单测 | 聚合 client 路径+头（B2 回归） | C5/§8.1 |
| 单测 | 分组 rep 选择、全 paused 不建流、禁用不建流 | C5/C6 |
| 单测 | `skip` 不 ack 不记 error；throw ⇒ 不 ack 记 error | C9 |
| 单测 | `thread_root` 与 tssdk 逐字段相等；`route` followup/new | C8 |
| 单测 | ping/pong 被吞 ⇒ accepted 且不建 session | §5.5 |
| 单测 | status 形状与脱敏 | §8.4/C11 |
| 单测 | 多 Gateway 不交叉 ack | C6 |
| 单测 | deerflow：`Idempotency-Key` 与实际 message_id 一致；thread_id 确定性 | §5.8 |
| 契约 | `check-contract-single-source.py` | 常量单一源 |
| 跨语言 | py/ts 分组与 thread_root 一致性 | G4 |
| L2 | hermes：s1..s5+neg | G5a |
| L2 | deerflow：s1..s5+neg | G5b |
| L2 | tssdk 回归 `--sdk ts --host dsh`（不得被连带弄红） | 回归 |
| 真实宿主 | 重启恢复、并发（会话忙）、回复 followup | C8/C10 |

---

## 11. 发布与回滚

### 11.1 发布门禁顺序

```bash
# 1) L0
bash tests/sdk/l0-gate-tests.sh
# 2) wheel 冒烟
bash tests/sdk/release/l2-verify-wheel.sh
# 3) L2 —— 两个 pysdk 宿主 × 三轮
for h in hermes deerflow; do
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host $h --stages "s1 s2 s3 neg"
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host $h --stages "s4"
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host $h --stages "s5"
done
# 4) tssdk 回归
bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s2 s4 neg"
# 5) 发版 0.3.0（wheel + sdist）+ 变更记录
```

L3（发布后、验证方）：pysdk 迁移完成后 hermes 不再因“未迁移”被排除；是否纳入 L3 矩阵由验证方按 `tests/l3/GATE.md` 决策，**开发不以 L3 作为交付依据**。

### 11.2 回滚开关

| 开关 | 作用 |
|---|---|
| `AIMAIL_PULL_MODE=poll` | 强制 long-pull（SSE 故障 kill switch） |
| `AIMAIL_V2_DISABLED=1` | 插件不建聚合流（仅 legacy 单地址拉取），灰度回退 |
| 插件卸载（`plugins.enabled` / `plugins:` 记录移除 + 重载） | 完全摘除入站 |

### 11.3 双消费禁止

V1 per-address pull 与 V2 aggregate SSE 不得同时消费同一地址：V2 binding 只进聚合组；V1 binding 才走 legacy 单地址流；两者同时命中 ⇒ 日志 `refusing to double-consume <addr>`，只保留 V2 组。

---

## 12. 风险与对策

| ID | 风险 | 影响 | 对策 |
|---|---|---|---|
| ~~R7~~ | ~~网关 ING-1 未发版~~ **已关闭** | — | 见 §2.6：v1.0.12 起修复、pin v1.0.13、S3 已实跑 PASS |
| R1 | hermes 升级后 `_gateway_accepted` 改名/移除 | ack oracle 失效 | §8.4 探针 RED；代码 fail-closed |
| R2 | hermes 插件 `register()` 超过 10s deadline | 插件不加载 | `register()` 只注册启动，不做 I/O |
| R3 | 多 profile 下 `os.getenv` 读到启动 profile 的 env | 串 profile | 只读 `ctx.get_config`/profile 作用域 |
| R4 | hermes session authority（FIFO）模式下 adapter 直投被吞 | 投递不落 session | 走 adapter 真入站路径；P2 末尾专项手测；L2 S3 的 ack 证据会暴露 |
| R5 | `handle_message` 异步返回、agent 未跑完 | 误判 accepted | accepted 只到“已进 session FIFO”，与契约 §8.3 一致 |
| R6 | 同一 ASID 两条流（多 worker/多进程） | 网关单槽 409 | leader 锁；hermes 单进程天然满足，deerflow 用 `task_lifecycle` 单实例 |
| R8 | deerflow 扩展安装面复杂（`uv add`/`--locked`/锁文件/网络） | E 阶段卡住 | 镜像内预置扩展依赖 + 运行时只 `pip install` 我方 wheel；探针先证 |
| R9 | deerflow 生产态 run 触发面未定（D1） | P5 无法交付 | P0 探针必须给结论；兜底 = 部署前置（内部凭据）+ 记录 |
| R10 | deerflow base 重建脚本不在 repo、旧临时资产散在 `/tmp` | 环境不可复现 | E2/E9 收编进仓库并参数化 |
| R11 | deerflow 多地址能力（D5）不达标 | C1/C5 在 deerflow 降级 | 探针先证；退化方案 = 一地址一实例，并在门禁记录“能力降级” |
| R12 | wheel 缺新模块 | 运行时 ImportError | force-include 清单 + `l2-verify-wheel.sh` 显式 import 两宿主插件模块 |
| R13 | 旧 binding（无 ASID）行为回归 | 老用户掉线 | 保留 V1 判定走 legacy 流；V2 需重新激活（契约 §5.3），不静默补写 |
| R14 | 夹具写 `plugins.enabled`/`plugins:` 被误当“SDK 写宿主配置” | 口径争议 | 两处都是宿主配置面；SDK 只提供包，不写宿主配置 |
| R15 | **deerflow 扩展工具名被宿主哈希化**（`ext_<namespace>_<name>_<sha256[:12]>`，实测于 `extensions/plugin_tools.py:56`）⇒ 契约 §3.1“工具名就是 SKILL 触发词 `aimail_address`”在扩展路径上不成立 | 激活 SKILL 触发词 miss（假绿家族） | P5 先定工具面：优先 **MCP**（宿主有 `_raw_mcp_tool_name` 去前缀逻辑，裸名可期），扩展只做 SSE 传输；若必须走扩展，则需 Owner 裁定的别名方案。门禁新增断言：模型可见工具名 == 契约名 |
| R16 | deerflow 扩展 `install()` 静默失效（`PluginContribution` 缺 `namespace/title` 或 `enabled != True` ⇒ `build_plugin_tools` 直接跳过，日志只有一行 warning） | 以为是“已注册”，实际模型看不到工具 | 探针断言 `registry.plugin()` 返回 True 并落 marker；env gate 已按此 fail-closed（本轮实测曾因此假绿一次） |
| R17 | ~~Hermes 不服务插件平台~~ **已更正（2026-11-24）：宿主本就支持，判断有误**。官方路径 = `PluginContext.register_platform()` → `platform_registry` → `Platform._missing_`；实测钉版 0.23.2 **未打 patch、pip-only（entry point）** 即 `✓ aimail connected / Gateway running with 1 platform(s)`。原症状由**夹具把插件装成目录插件**引起；夹具已改为真实安装路径，`lib/hermes-host-patch.py` 已删除 | 无（已解除，且门禁侧也不再 patch 宿主） | §15.5 / §15.11 实测记录 |

---

## 13. 验收清单

```text
[x] G1 全流程无 SDK 自带 CLI（安装 = pip / 宿主插件机制）
      └ 门禁全程只用 `pip install wheel` + 宿主 entry point；`aimail install` 已退役，
[x] G2 无 push/webhook 端点依赖；**无 host 源码 patch（产品侧与门禁侧同时成立）**；无 per-profile 端口
      └ 2026-11-24 实测推翻 R17：宿主本就支持插件平台，`lib/hermes-host-patch.py` 已删除；
        hermes 门禁只做 `pip install` + entry point 发现，仍 PASS=14 + s5 PASS=2
[x] G3 每 (AgentSystemId, Gateway) 一条 SSE；SSE reconcile 行稳定
      └ `SSE reconcile: 1 gateway stream(s) from 2 binding(s)`（S4 实测）
[x] G4 与 tssdk 同契约/同端点/同 ack 语义/同幂等（含跨语言一致性测试）
      └ L0 含 tssdk `pnpm build` + 孤儿/生成物守卫 + vitest；跨语言 thread_root/分组一致性用例在 tests/sdk
[x] G5a hermes:  s1 s2 s3 s4 neg → PASS=14 RED=0 GAP=0 SKIP_LEGACY=0（0.3.0 wheel 实测）
[x] G5b deerflow: s1 s2 s3 s4 neg → PASS=15；s5 → PASS=2；RED=0 GAP=0 SKIP_LEGACY=0
[x] G5c tssdk dsh 回归未变红 → `--sdk ts --host dsh --stages "s1 s2 s3 s4 neg"` = PASS=16 RED=0 GAP=0（tssdk 0.5.2 tgz）
[x] G6 0.3.0 wheel 构建 + 变更记录（`pysdk/README.md` §0.3.0 breaking）
      └ 上传 PyPI 属 owner 动作；回滚开关 = `AIMAIL_LEGACY=1` 显式执行 legacy 套件
[x] E-env deerflow 干净环境可重建、已 pin、干净度审计通过（§7.1 E1-E9）

契约 C1  单地址宿主拒绝第二地址                              [x] hermes S4 + 单测
契约 C2  激活仅 code、返回 GatewaySystemId                   [x] hermes/deerflow S1
契约 C3  目录按 GatewaySystemId 分组                          [x] S1/S4 路径断言
契约 C4  ASID 稳定 / 变化                                    [x] 单测
契约 C5  单 Gateway 聚合一条流                                [x] S4（2 bindings → 1 stream）
契约 C6  多 Gateway 各一条流、不混 ack                        [x] 单测（不交叉 ack）
契约 C7  同 Gateway 多 GatewaySystemId 正确路由               [x] 单测
契约 C8  同 thread followup / 新 thread 新 session，空间=cwd  [x] 产品路径（P4 单测+runtime 级）
      └ 宿主侧"回复我们的回复"尚无 s6 夹具阶段 ⇒ 实机证据待补（诚实记录）
契约 C9  成功投递后才 ack                                     [x] S3 `queued then acked`；失败仅 warning 不 ack
契约 C10 重启：有 binding 即 arm                              [x] S2
契约 C11 无 raw key/code 日志                                 [x] S1 `has_key` + 日志审查
契约 C12 解绑停路由/聚合/pointer                              [x] deerflow S5
契约 C13 move/resume 不误伤                                   [x] 单测

[x] 无跨 profile / 跨 ASID / 跨 Gateway 串线                  [x] 单测 + 分目录 evidence
[x] legacy 代码只在显式开关下可达，且不注册工具、不建入站      [x] 0.3.0 wheel 不含 patch 时代模块（守护测试）
```

---

## 14. 附录

### A. 文件级改动清单

| 文件 | 动作 |
|---|---|
| `pysdk/v2/{__init__,agent_system_id,binding,pull_client,system_pull,delivery,thread,outbound,ack,status}.py` | 新增 |
| `pysdk/hermes_plugin/**` | 新增（plugin/adapter/runtime/identity/tools） |
| `pysdk/deerflow_plugin/**` | 新增（install/tools/runtime/identity） |
| `pysdk/aimail_tools.py` | 改：client 路径/头覆盖、`skip` 语义、激活带 ASID 头、persist 写 ASID/workspace 且不再写 webhook |
| `pysdk/aimail_base.py` | 改：`is_agent_scope_binding` 支持 V2、replay 退出主路径（保留 legacy）、审计行 |
| `pysdk/hermes/{ensure_config,patch_webhook,register_profiles,toolsets}.py` | 降级 legacy（不进出包主路径） |
| `pysdk/deer-flow/{aimail_inbound,aimail_deerflow,manage}.py` | 降级 legacy（V2 走扩展） |
| `pysdk/install.py`、`pysdk/sdk_ops.py` | 主路径移除；只留只读 |
| `pyproject.toml` | 版本、force-include 新模块、两个 entry point（`hermes_agent.plugins` / `deerflow.extensions`） |
| `tests/sdk/test_v2_*.py` | 新增单测（§10） |
| `tests/host-acceptance/v2/lib/pysdk.sh` | 新增（wheel 构建） |
| `tests/host-acceptance/v2/hosts/{hermes,deerflow}.sh` | 新增 |
| `tests/host-acceptance/v2/lib/{host-probe-hermes.py,host-probe-deerflow.py,deerflow_drive.py}` | 新增 |
| `tests/host-acceptance/v2/{run-matrix.sh,cases/s4_multiaddr.sh,lib/evidence.sh}` | 改 |
| `tests/agent-env/docker/rebuild-deerflow-base.sh` | 新增（从 `/tmp` 收编、路径参数化） |
| `tests/agent-env/docker/Dockerfile.deerflow` | 改（基础镜像引用 `BASE_DEERFLOW`，与 rev pin 对齐） |
| `tests/agent-env/pins.env` | 改（`DEERFLOW_REV`、`BASE_DEERFLOW`；GATEWAY 保持 v1.0.13） |
| `.github/workflows/host-images.yml` | 改（agents 默认/分支增加 deerflow） |
| `docs/v2-release-notes.md`、`pysdk/README.md`、`tests/l3/GATE.md` | 改（发版口径） |

### B. 与 tssdk 的常量/字段对照

| 概念 | tssdk | pysdk（目标） |
|---|---|---|
| ASID 前缀 | `asid_` | `asid_`（同） |
| ASID 派生种子 | `aimail-agent-system-v1` | 同（跨语言同值） |
| 聚合 stream 路径 | `/api/v1/agent-system/pending/stream` | 同 |
| 聚合 pending / ack | `/api/v1/agent-system/pending`、`/ack` | 同 |
| 聚合头 | `X-Agent-System-Id` | 同 |
| binding 字段 | `agent_system_id`、`gateway_url`、`system_id`、`api_key`、`paused`、`workspace_id/path` | 同 |
| 审计行 | `SSE reconcile: <N> gateway stream(s) from <M> binding(s)` | 同 |
| thread root | `references[0] → in_reply_to → message_id` | 同 |
| skip 语义 | `{status:'skip', reason:'paused'\|'unknown'}` | 同（Python dict） |
| 环境变量 | `AIMAIL_PULL`、`AIMAIL_PULL_MODE`、`AIMAIL_PULL_INTERVAL_MS`、`AIMAIL_PULL_LIMIT`、`AIMAIL_AGENT_SYSTEM_ID`、`AIMAIL_AGENT_HOME`、`AIMAIL_HARDWARE_FINGERPRINT` | 同 |

### C. 命令手册

```bash
# ── 单元 + 契约（发布前必跑）
bash tests/sdk/l0-gate-tests.sh

# ── wheel 冒烟（干净 venv，覆盖两宿主插件可导入）
bash tests/sdk/release/l2-verify-wheel.sh

# ── L2：pysdk 两宿主（每宿主三轮，按 journey 作用域拆开）
export LLM_API_KEY=…
for h in hermes deerflow; do
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host "$h" --stages "s1 s2 s3 neg"
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host "$h" --stages "s4"
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host "$h" --stages "s5"
done

# ── 回归：tssdk 不得被连带弄红
bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s2 s4 neg"

# ── deerflow 干净环境（E 阶段）
DEERFLOW_SRC=$HOME/deer-flow bash tests/agent-env/docker/rebuild-deerflow-base.sh   # 产出 src-<rev>
docker build -t aimail-host-deerflow:latest \
  -f tests/agent-env/docker/Dockerfile.deerflow <build-context>
# 干净度审计（必须全绿）
docker run --rm --entrypoint bash aimail-host-deerflow:latest -lc \
  'python3 -c "import aimail"; echo rc=$?'   # 期望 ModuleNotFoundError
```

### D. 与旧版方案的差异（本文件取代旧汇总）

| 旧版说法 | 现在的事实 / 决定 |
|---|---|
| “Hermes plugin platform adapter API 版本差异”列为开放风险 | 已实测：`register_platform` + `BasePlatformAdapter`（4 抽象方法）+ `handle_message` 均存在（§2.3） |
| “`handle_message` 返回 session 信息”未知 | 返回 `None`；accepted 证据是 `event._gateway_accepted`（§2.3、§5.6） |
| “先修 B1/B2（tssdk）再谈 pysdk” | tssdk B1/B2 已在 0.5.1 关闭；pysdk 等价缺陷由 Phase P1 修 |
| **“deerflow 不作为本次门禁”** | **已改：deerflow 是门禁宿主 B（G5b）**，前置 = 干净环境 E1–E9（§7.1） |
| **“网关 ING-1 未发版是阻塞项（R7）”** | **已撤销：v1.0.12 修复、pin v1.0.13、S3 实跑 PASS（§2.6）** |
| 只说“每 profile 一条 SSE” | 明确 per (ASID, Gateway)、rep 选择、foreign ASID 跳过、all-paused 不建流 |
| 未提 L2 夹具如何支持 python | `--sdk py` + `lib/pysdk.sh` + 两宿主 adapter + `HOST_ADDRESS_MODEL`（§8.2） |
| 未定 ack 判定 | ack 唯一出口 + fail-closed + 宿主侧证据（hermes `_gateway_accepted` / deerflow run 记录） |
| 未定发版门禁顺序 | §11.1（L0 → wheel → 两宿主三轮 → tssdk 回归 → 发布） |

---


## 15. 交付记录（滚动）

### 15.1 E 轨道（deerflow 干净环境）—— 完成，7/7

```text
bash tests/host-acceptance/v2/lib/deerflow-env-gate.sh        # 需要 LLM_API_KEY
  → PASS=9 FAIL=0 · VERDICT: PASS
```

实测证据要点：`install_ok` marker（扩展被宿主接受）→ `POST /api/threads` 200 →
`POST …/runs` 200 返回 `run_id` → 真实模型调用注册工具，marker 含
`{"event":"call","value":"env-gate-ok","thread_id":"aimail-env-gate-…","user_id":"default"}`。

顺带修掉的两处真问题：镜像 uv 缓存带我方两个 PyPI 名（`aimailsdk`/`agent-aimail`）；
`write_deerflow` 把 `local-qwen-dummy` 当 `api_key` 烘进镜像。

### 15.2 P1（共享 V2 核心）—— 完成

新增 `pysdk/v2/`（`agent_system_id` / `binding` / `pull_client` / `system_pull`），并改造：

| 改动 | 落点 | 回归 |
|---|---|---|
| 聚合 client 唯一构造函数（聚合三路径 + `X-Agent-System-Id` 三条腿都带） | `v2/pull_client.py` | B2 回归：`test_aggregate_client_carries_paths_and_header`；legacy client 路径不变 |
| V2 判定 = `agent_system_id` 存在（V1 前缀保留） | `aimail_base.is_v2_agent_scope_binding` | B1 回归：`test_v2_binding_is_agent_scope_even_without_v1_prefix` |
| 分组 = `(asid, gateway_url)`，rep = 首个 active，all-paused 不建流，foreign ASID 跳过 | `v2/binding.py` + `v2/system_pull.start_reconcile` | `SSE reconcile` 行断言 |
| delivery 新增 `skip`（paused/unknown 不 ack、不算 error、下轮重评） | `aimail_tools._deliver_batches` | `test_delivery_acks_accepted_only_and_leaves_skips_pending` |
| 激活带 `X-Agent-System-Id`；persist 写 `agent_system_id`/`workspace_path`，V2 不再生成 webhook secret | `aimail_tools.activate_address_code(_persist)` | 既有 441 项全绿（V1 两参 seam 不变） |
| 空值不再回写云端 domain（防 V2 绑定向网关推空 webhook 覆盖旧配置） | `aimail_tools` 激活对账段 | 既有 activation 测试全绿 |
| wheel 打包 `aimail/v2/*` | `pyproject.toml` force-include | `tests/sdk/release/l2-verify-wheel.sh` PASS |

验证命令与结果：

```text
pytest tests/sdk                          → 441 passed（含 26 项 V2 单测）

### 15.3 下一步（P3）

L2 夹具接入：lib/pysdk.sh（构建 wheel）、hosts/hermes.sh（容器内 pip 装 wheel、写 plugins.enabled、
bind prompt、重启 arm、注入探测、判 ack）、run-matrix.sh 的 --sdk py、s4_multiaddr.sh 的
HOST_ADDRESS_MODEL。目标：hermes 三轮 RED=0 GAP=0 SKIP_LEGACY=0。

### 15.4 P2（Hermes 插件，代码完成）

`pysdk/hermes_plugin/`（6 文件，全部随 wheel 发布）：

| 文件 | 职责 | 关键约束 |
|---|---|---|
| `plugin.yaml` | `kind: platform` v2 清单，`config_schema` 只列 4 个 `AIMAIL_PULL_*` 设置 | 不写 host 配置；设置只经 `ctx.get_config` 读 |
| `__init__.py` | `register(ctx)`：`register_platform` + `register_tool` + `on_unload` + binding-created 热加流 | `register()` 内不做网络 I/O（宿主 10s deadline） |
| `adapter.py` | `AIMailAdapter`（`connect`/`disconnect`/`send`/`get_chat_info` + 入站桥） | 不 bind 端口；accepted 只信 `event._gateway_accepted` |
| `runtime.py` | `HermesRuntime`：reconcile → 预处理 → `bridge.deliver`；`None`（ping/pong）⇒ accepted 但不建 session | 宿主拒绝必须抛错（否则共享 client 会 ack） |
| `identity.py` | profile home → AgentSystemId / workspace | 宿主 ctx 优先、env 兜底（多 profile 安全） |
| `tools.py` | 共享 tool-spec → `ctx.register_tool`；`handler(**kw)` → `handler(dict)` 单点适配 | 工具面单一源（15 个），不重抄 schema |

`pyproject.toml` 同时声明 entry point `[project.entry-points."hermes_agent.plugins"] aimail = "aimail.hermes_plugin:register"`
—— `pip install` 即安装，宿主按 `plugins.enabled` 启用，全程无 SDK CLI、无 host patch。

验证：`pytest tests/sdk` **480 passed**（新增 13 项 fake-host 单测）、L0 gate **PASS**（64 passed）、
wheel 校验 **PASS**（wheel 含 `aimail/hermes_plugin/*`，`entry_points.txt` 有 `[hermes_agent.plugins]`）。
真实宿主“装→启用→bind→收信→ack”闭环归 P3（需要 host 容器 + `hosts/hermes.sh`）。

### 15.5 P3（L2 夹具，部分完成）

已落地（可运行）：

| 文件 | 作用 |
|---|---|
| `tests/host-acceptance/v2/lib/pysdk.sh` | `pysdk_build`：从工作树 `pip wheel` 出 dev wheel，导出 `SDK_FILE/SDK_VER` |
| `tests/host-acceptance/v2/run-matrix.sh` | 新增 `--sdk ts\|py`（`py` 时走 wheel 构建、`--host all` 展开为 hermes/deerflow）、`run.json` 增 `sdk_kind` |
| `tests/host-acceptance/v2/hosts/hermes.sh` | 一次容器完成：装 wheel → 建 profile（`plugins.enabled` + drop-in 插件目录，uid 10000）→ 真实 agent turn 里 bind / pause / unbind → 收集 binding/模式位/计数 → 起 messaging gateway 记录是否 attach |
| `tests/host-acceptance/v2/cases/s4_multiaddr.sh` | 用 `HOST_ADDRESS_MODEL` 取代硬编码 `host=pi`（single/multi 分支） |

实跑结论（gateway v1.0.13 · `aimail-host-hermes:latest`）：

* **载体可用**：`hermes -z "<prompt>"` 会加载已启用插件，`aimail_address` 在真实模型 turn 里被调用并打到网关——S1 首跑 6 项 PASS（binding 写出/ `system-*` / 无 webhook / 有 key / 0600），
  `NEG`（非法码不写 binding）PASS。这一次运行直接暴露了产品缺口：binding 缺 `agent_system_id`/`workspace_path`（工具层没把宿主上下文传给 persist），已修（`aimail_base.set_binding_context_provider` + 插件在 `register()` 注入），并补了单测。
* **s2/s3 未判定**：host gateway 不服务插件平台（R17），fixture 落 `gateway_state=no_platforms` 且不注入探针——按“未 attach 不判绿”处理。
* **夹具加固已落地**：阶段 marker（`/out/step`）、每 turn 独立日志（`/out/turn_<name>.log`）、
  negative 独立输出目录（不再覆盖 journey 证据）、`gateway stop` 先清理、binding 发现覆盖
  agent 真实 home（`/opt/data/.aimail`）。
* **新发现（宿主行为，均为实测）**：
  1. `-z` turn 经宿主特权降权 shim，**`AIMAIL_HOME` 不传递给 agent**（binding 落到 `/opt/data/.aimail`）；
  2. 同名 drop-in 插件目录会**压制** entry point 那一份，导致工具不出现 ⇒ 夹具只用 entry point；
  3. **entry point 插件的加载不可复现**：同一镜像、同一 `pip install` + `plugins.enabled`，一次 `hermes plugins list` 显示 `enabled user 0.3.0 aimail`（工具可用），下一次显示 `(aimail not listed)`（工具缺失）。
     这是 s1 无法稳定转绿的直接原因，也是当前唯一未解的宿主行为。
* **根因已定位（本轮）**：Hermes 把插件代码放在**项目 venv**（`/opt/hermes/.venv`）里执行，
  而 `hermes` 启动器用的是 `tools/python-3.14`。只往启动器解释器装 wheel，插件注册就会
  `ERROR: Plugin registration failed: No module named 'aimail'`（`hermes plugins doctor <dir>` 直接可见），
  ⇒ 工具不进 session。用 `uv pip install --python /opt/hermes/.venv/bin/python <wheel>` 之后
  `doctor` 报 `registrations: 15 tool(s)`，turn 里也能看到 `aimail_address`。
  夹具已据此改造：**双解释器安装** + `hermes plugins doctor` 自检（`registered_tools`），
  并恢复**目录插件**（本版宿主的发现只认 `<home>/plugins/<name>/`，entry point 不被列出）。
* **又有三处宿主契约细节被实测纠正（都已在代码里修掉）**：
  1. **工具按位置传参**：`handler(args_dict)`，不是 `handler(**kwargs)`。原包装器是仅关键字签名，
     真实调用立刻报 `Tool execution failed: TypeError: _adapt.<locals>.call() takes 0 positional
     arguments but 1 was given`（所有工具都会中招）。
  2. **工具必须返回文本**：返回 dict 被宿主拒绝，`{"error": "Tool handler returned unsupported
     result type: dict", "error_type": "tool_result_contract"}` ⇒ `tools._emit()` 统一序列化成
     JSON 字符串（模型看到的仍是同一份 JSON）。
  3. **turn 预算是真需求**：真实 bind 一跳实测 27–70s，对着活网关的一次激活约 2 分钟；原先 300s
     会把输出截断成一个空日志，看起来像“工具不存在”。夹具已改 420s，并在提示词里加
     “exactly once, do not retry”（否则模型会对我们 `retryable: true` 的 NETWORK 信封反复重试）。
* **现在这一跳是通的**（无网关条件下的决定性验证）：
  `hermes -z "Call aimail_address exactly once … gateway_url=http://127.0.0.1:1"` → 67s →
  返回我们自己的信封 `{"ok": true, "success": false, "error_code": "NETWORK", …}` 原文。
* **并发隔离已解决（本机可与 L3 并行跑）**：gateway 的单实例锁就是**一个 PID 文件**，默认落在
  `$TMPDIR/aimail-gateway.pid`（gateway `src/core/cli/mod.rs` `PidFile::default_path`），所以任意两个
  gateway 会互相锁死。`--pid-file` 是全局 CLI 选项 ⇒ `lib/gw.sh` 改为**每个 run 用自己的 pid 文件**
  （+ 自己的端口/存储）。实测：两个 gateway 同时健康运行，全局 pid 文件未被触碰（因此不会再杀别人的进程）。
* **夹具已加 `doctor` 硬门**：注册 0 工具 ⇒ 落 `fixture_error`，不再让“工具缺失”读成产品 RED。
* **L2 结果（`--sdk py --host hermes`，gateway v1.0.14）**：

```text
--stages "s1 s4 s5 neg"  →  PASS=11 RED=0 GAP=0 SKIP_LEGACY=0   VERDICT: PASS
  S1 activation      binding written / system_id_is_gateway / agent_system_id /
                     workspace_path / no_webhook / has_key / mode 0600
  S4 multi-address   second address rejected (single-address model, hermes)      ← C1
  S5 lifecycle       pause marked the binding / unbind removed the binding (1→0)
  NEG invalid code   no binding written for an invalid code
```

  其中 S4 的通过来自**新实现**：`aimail_address(bind)` 在单地址宿主上先校验、
  **不消耗激活码**直接返回 `reason=second-address` / `error_code=SINGLE_ADDRESS_HOST`（契约 C1/§11.2），
  宿主能力经 `set_binding_context_provider` 的 `address_model` 声明（hermes=`single`）。
* **R17 结论已更正（2026-11-24，实测推翻原判断）**：宿主**本来就支持**插件平台，不需要 patch。
  官方路径是 `PluginContext.register_platform()` → `gateway/platform_registry.py` →
  `Platform._missing_` 查注册表 → 配置解析时即可解析 `aimail`。实测（钉版宿主 0.23.2，**未打 patch**、
  **只 pip 装 wheel 走 entry point**、不建插件目录）：`gateway.run: Connecting to aimail...` →
  `✓ aimail connected` → `Gateway running with 1 platform(s)`，且 `hermes plugins list --user`
  显示 `aimail … enabled … entrypoint`、`plugins doctor` 报 `registrations: 15 tool(s)`。
  原先的 `No messaging platforms enabled.` 是**夹具自己造成的**：当时把插件装成**目录插件**（复制到
  `$HERMES_HOME/plugins/aimail`），而目录插件的 manifest 名称与 `platforms.<name>` 的解析时序没被覆盖到；
  改成真实安装路径（pip entry point）后问题不复存在。`lib/hermes-host-patch.py` 已**删除**，
  夹具现在测的就是用户真实安装面 —— 这同时把 G2「无 host 源码 patch」从"仅产品侧成立"提升为"门禁侧也成立"。
  同一轮还修了**我们自己的两处宿主 API 误用**（gateway 日志证据）：
  `BasePlatformAdapter.__init__(config, platform)` 需要两个参数（原来只传 config）；
  `_mark_connected(*, listener_base=None)` / `_mark_disconnected()` 没有布尔参数（原来传 `False`）。
* **L2 现状（`--sdk py --host hermes`，gateway v1.0.14）**：

```text
"s1 s4 s5 neg" → PASS=11 RED=0 GAP=0 SKIP_LEGACY=0   VERDICT: PASS
"s2"           → PASS=2  RED=0 GAP=0 SKIP_LEGACY=0   VERDICT: PASS   （restart 后 armed）
"s3"           → PASS=2  RED=1                       mail 已入库(emails=1)、
                 armed 已观察到，但 pending 未 drain：交付链未走通（见下）
```

  S3 曾卡在“流已 arm 但一轮都不返回”。根因是**夹具时序**，不是产品：夹具在流 attach 之后才注入，
  与网关的 push-on-insert 竞态。夹具改为**两阶段**（一个容器，`docker exec` 交错）：
  phase 1 安装+激活 → **宿主在主机停机期间白名单+注入** → phase 2 打宿主 patch、启动 gateway，
  流 attach 后把已入队的邮件拉走并 ack。另把 `AIMAIL_HOME` 统一到 agent 真正写入的
  `/opt/data/.aimail`（`-z` 经降权 shim 会丢 env），drain 窗口 30s → 180s（长拉取 hold 50s）。
  产品侧补了三行诊断（`stream start` / `delivery id=` / `stream ended`），让“armed 但静默”可定位。

### 15.6 hermes L2 结论（**门禁宿主 A 已完成**）

```text
bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host hermes --stages "s1 s2 s3 s4 neg"
  → PASS=14 RED=0 GAP=0 SKIP_LEGACY=0   VERDICT: PASS
bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host hermes --stages "s5"
  → PASS=2  RED=0 GAP=0 SKIP_LEGACY=0   VERDICT: PASS
```

覆盖：激活产物（含 `agent_system_id`/`workspace_path`/0600/无 webhook）、重启 arm（`SSE reconcile`）、
白名单→入库→入队→宿主取走→ack、单地址拒绝（C1）、pause/unbind、非法码不写 binding。
`SKIP_LEGACY` 已从 hermes 消失 ⇒ G5a 达成。

下一步（按优先级）：
1. 先弄清 Hermes 的 entry-point 插件发现为何不可复现（候选：plugin toolset 缓存
   `cache/plugin_toolset_keys.json`、`hermes plugins list` 与加载路径不同、或降权 shim 的
   环境重建影响了 `importlib.metadata` 的搜索路径）；
2. s1/s4/s5/neg 稳定绿；
3. s2/s3 待 R17 有宿主侧支持后再判。
bash tests/sdk/l0-gate-tests.sh           → PASS（51 passed + 契约 + 文档一致性 + zero-bridge）
bash tests/sdk/release/l2-verify-wheel.sh → PASS（wheel 含 aimail/v2/*）
```

已知环境噪声（与本轮无关）：`test_atomic_write_private_keeps_host_dir_mode_when_none`
在 `umask 077` 下失败、`umask 022` 下通过——既有测试对 umask 敏感。

### 15.7 P5（DeerFlow 插件，产品侧完成；夹具首跑定位到端口假设）

`pysdk/deerflow_plugin/`（4 文件，随 wheel 发布 + `deerflow.extensions` entry point）：

| 文件 | 职责 | 实测约束 |
|---|---|---|
| `__init__.py` | `install(registry, config)`：注册工具 + 启动 inbound runtime | `PluginContribution` 必须 `enabled=True`，否则宿主静默丢工具 |
| `tools.py` | 共享 tool-spec → `ModelTool`（15 个），结果按宿主约定返回 | 宿主把模型可见的工具名**哈希化**（R15）⇒ 描述里写意图 |
| `runtime.py` | SSE → `POST /api/threads`（幂等）+ `…/runs`（`Idempotency-Key`）→ 2xx 即 accepted | 宿主拒绝必须抛错（否则共享 client 会 ack） |
| `identity.py` | 实例级 ASID（`DEERFLOW_HOME`/`/app`）、`address_model=multi` | 空间按 thread 走，不经 cwd |

单测 9 项 + 全量 `pytest tests/sdk` **490 passed**；两项 entry point 均在 wheel 里
（`hermes_agent.plugins` / `deerflow.extensions`）。

**真实宿主首跑（`aimail-host-deerflow`）已证明扩展生效**：

```text
[aimail-deerflow] mail tools: 15 (namespace=aimail)
[aimail-pull] SSE reconcile: 0 gateway stream(s) from 0 binding(s)
[aimail-deerflow] armed 0 stream(s) for agent:asid_8cdd…
Extensions loaded: 1/1 (aimail.deerflow_plugin:install)
```

S1 仍 RED，原因已定位且已修**夹具假设**：宿主自己决定监听端口（日志 `Starting API Gateway on 0.0.0.0:8001`，
`--port` 不生效），而夹具按 `DF_API_PORT` 驱动 ⇒ 连不上、turn 没跑。夹具改为**从宿主日志读回真实端口**
再驱动。下一步就是重跑 `--host deerflow --stages "s1 s2 s3 s4 neg"` 并处理余下问题。

### 15.8 DeerFlow L2 全绿（**门禁宿主 B 已完成**）

`--host deerflow`：`s1` **PASS=7**、`s2 s3 s4 neg` **PASS=8**、`s5` **PASS=2**，
合计 **17 PASS / RED=0 / GAP=0 / SKIP_LEGACY=0**。三个真实缺陷依次定位并修复：

| # | 现象 | 根因 | 修复 |
|---|---|---|---|
| 1 | bind turn `status=error`、只有 human 消息、`messages` 无工具调用 | 宿主日志 `401 Invalid API Key`：deerflow 只读 `config.yaml: models[].api_key`，**不读** `$LLM_API_KEY`；镜像里是占位符 `__LLM_API_KEY__`，夹具从没替换过 | 夹具 phase 1 先跑 `set-llm-key.sh deerflow`（运行时注入、不烘焙），并**自检占位符**——残留则 `boot_error` 直接失败，不再"跑到 turn 才 401" |
| 2 | S3 `observed max=1 final=1`：投递被拉走但**永不 ack** | `runtime.api_base` 默认 `127.0.0.1:8001`（镜像默认端口），本部署 uvicorn 在 `--port 18001` ⇒ 自调用 `POST /api/threads` 连接被拒 ⇒ 抛错 ⇒ 不 ack（共享 client 只在 accepted 后 ack） | 夹具导出 `AIMAIL_DEERFLOW_API=http://127.0.0.1:$端口`（phase 1/2 同一端口）；产品侧新增 `_post_when_ready()`：**只在"没人监听"(status 0) 时**按 0.25→4s 退避重试到 60s 截止——扩展是在宿主 lifespan 启动过程中装载的，首封邮件可能早于 uvicorn 可连接 |
| 3 | 修复 2 后 `observed max=0 final=0`（"没排队"的假象） | ack 发生在**第二次启动的瞬间**，启动后才开始轮询的探针只看到 0→0，无法区分"已 ack"与"从未入队" | 探针改成**跨启动观测**：phase 2 之前起 watcher，要求"至少见过一次 pending 且随后归零"才写 `acked_after_poll` |

顺带补一处可观测性：`_deliver_batches` 里 `on_email` 抛错此前**静默计数**（只 `stats["errors"] += 1`），
于是"宿主没接受"在日志上与"宿主没收到"完全同形——这正是缺陷 2 花了两轮才定位的原因；现改为
`logger.warning("[aimail-pull] delivery id=… not accepted: …")`。

夹具还有一条通用教训（已写进注释）：`docker -lc '...'` 体内**不得出现撇号**（`host's` 会截断外层单引号，
报 `unexpected EOF`），且 `$DF_API_PORT` 这类宿主变量必须用 `'"$VAR"'` 转义，否则在容器内被 `set -u` 判为未定义。

验证：`pytest tests/sdk` **489 passed / 1 failed**（唯一红项 `test_v2_contract_surface.py` 读兄弟仓
`src/core/api/activation.rs` 文案，与本次改动无关）；顺带修掉一个 umask 敏感的测试
（`mkdir(mode=…)` 被 runner 的 `umask 077` 削成 0700，改用显式 `os.chmod`）。

### 15.9 P4 回复追溯（`v2/outbound.py`）—— 代码完成

**问题（一句话）**：外部方回复"我们的回复"时，`References[0]` 是**我们自己分配的 Message-ID**，
而 `thread_root` 取 `references[0]` ⇒ 每一轮往返都换一个 thread root ⇒ 每封回复都开新 session
（契约 C8 直接破）。

**落地**：新增 `pysdk/v2/outbound.py`（与 tssdk `runtime/session-router.ts` 对齐）

| 机制 | 行为 |
|---|---|
| alias 存储 | `outbound-aliases.json` 写在**该 binding 自己的目录**（`config_path` 的父目录），`{aliases:{mid→root}, order:[…]}`，`atomic_write_private`（0600），LRU 上限 `ALIAS_LIMIT=512` |
| 写入点 | `aimail_tools.send_mail` 生成 Message-ID 后调 `_record_outbound_alias()`；root 取值顺序与 `thread_root` 一致：`references[0] → in-reply-to → 被回复的 mid → 自己的 mid` |
| 读取点 | 两个插件的 `runtime.deliver()` 改用 `outbound.resolve_root(payload, headers, binding_dir(cfg))`（不再是裸 `thread_root`） |
| 追溯 | `resolve_root` 在 alias 上做**有界追链**（`MAX_HOPS=8`，带环检测），未知 id 原样作 root ⇒ 稳定但确实是新 thread |
| 出站头 | `outbound.threading(root, parent_mid)` 产出 `In-Reply-To` + `References`；hermes `adapter.send()` 用"该 session 最近一封入站 mail 的 Message-ID"作父消息（`_last_inbound`，由 runtime 在投递元数据里带上 `aimail_message_id`） |
| 拒绝项（§6.6） | **不按 subject 匹配**（默认关＝永不）；**不跨 profile/ASID 猜** —— alias 索引按 binding 隔离，另一个地址的 alias 永远查不到 |

**验证**（`pytest tests/sdk` 现 **509 passed / 1 failed**，红项仍是兄弟仓文案测试）：

* `tests/sdk/test_v2_outbound.py`（18 项）：存取/幂等/LRU 剪枝/损坏文件降级；回复命中原始 thread；
  第二轮往返仍命中；链式追链与环；新邮件＝新 thread；未知引用＝新 thread；**跨 binding 不串**；
  `threading` 头形状；`self_check` 不漏 id；send 路径的 root 取值顺序。
* `tests/sdk/test_hermes_plugin.py`（+2 项，**走真实 runtime 代码**）：外部邮件 → 我方回复（记 alias）
  → 外部再回复，两次投递的 `chat_id` **相同**；并反向断言"没有 alias 时就会不同"（证明机制有效）；
  另一条断言无关邮件**不会**并进同一 session。

**未覆盖（诚实记录）**：真实宿主 L2 尚无 `s6` 阶段注入"回复我们的回复"（需要一个能拿到我方出站
Message-ID 的夹具步骤），故 C8 的**宿主侧**证据仍等 P6 时补；当前结论是产品代码路径已被单测钉死。

**打包陷阱（本轮真红一次，已加守护测试）**：`pyproject.toml` 用**显式** `force-include` 表做
`pysdk/x.py → aimail/x.py` 改名，新增 `pysdk/v2/outbound.py` 时忘了加行 ⇒ wheel 里没有该模块 ⇒
容器内插件装载失败（`cannot import name 'outbound' from 'v2'`）⇒ hermes L2 `s1/s2/s3` 全红
（`plugin_doctor: registrations: 0 tool(s)`）。修复后 `s1 s2 s3` 回到 **PASS=12 RED=0**。
新增 `tests/sdk/test_wheel_manifest.py`（5 项）：断言 `pysdk/v2`、`pysdk/hermes_plugin`、
`pysdk/deerflow_plugin` 下**每个模块**都在 force-include 表里，且目标都在 `aimail/` 下、无悬空源文件——
把这类"只在真实宿主里才炸"的缺口提前到单测。

### 15.10 P6 legacy 移除 + 0.3.0 —— 完成（G5c 实机回归除外）

| 动作 | 结果 |
|---|---|
| 版本 | `pyproject.toml` + `pysdk/__init__.py` → **0.3.0** |
| wheel | `agent_aimail-0.3.0-py3-none-any.whl`：`v2/` 7 模块齐全、两个 entry point（`hermes_agent.plugins` / `deerflow.extensions`）、**不含** patch 时代模块 |
| legacy 出仓（仅移出 wheel，源码留树供审计） | `hermes/patch_webhook.py`、`patch_profiles.py`、`ensure_config.py`、`register_profiles.py`、`toolsets.py` |
| 守护测试 | `tests/sdk/test_wheel_manifest.py` 增至 8 项：每个 `pysdk/v2`、`pysdk/*_plugin` 模块必须在 force-include 表；legacy 五个**不得**在表内但文件必须存在；`hermes/aimail_hermes.py`（registry 数据引用）必须仍在 |
| 干净安装冒烟 | 新建 venv 装 wheel：`aimail.__version__ == 0.3.0`、`v2.*` 可导入、两个插件可导入、`aimail.hermes.patch_webhook` **ModuleNotFoundError** |
| 发布说明 | `pysdk/README.md` 重写：安装 = `pip install agent-aimail`（无 CLI、无 patch、无 per-profile 端口）+ **0.3.0 breaking 清单** |
| L0 门禁 | `bash tests/sdk/l0-gate-tests.sh` → **PASS — all gates green**（含 docs↔impl、contract 单一源、pytest 阻断 12 套、tssdk build + vitest） |
| 两宿主（0.3.0 wheel） | hermes `s1 s2 s3 s4 neg` = **PASS=14**；deerflow `s1 s2 s3 s4 neg` = **PASS=15**、`s5` = **PASS=2**；两宿主均 `RED=0 GAP=0 SKIP_LEGACY=0` |

**顺手修掉一条长期红项**：`test_v2_contract_surface.py::test_address_activation_prompt_…` 一直把网关激活邮件的**整句**
钉死，而网关 v1.0.15（`c65823d`）有意重写了提示语 ⇒ 对方一改文案 SDK 必红。已改为断言**不变量**
（必须指向 `aimail_address` 工具、必须提 `agent-aimail` 包、必须走宿主插件机制而非 CLI、地址块不得含
`agent-self-setup.md`/`{manager}`），文案再变也不会误报。此后 L0 才真正全绿。

**未做（诚实记录）**：`G5c` tssdk dsh **实机** L2 回归未跑（`--sdk ts --host dsh`，需要跑 tssdk 打包 + dsh 容器，
约 10 分钟）；L0 的 tssdk `pnpm build` + vitest 已绿。`§6.8` 里"删除 `register_profiles.py`"与"`install.py`/`sdk_ops.py`
写操作下线"这两条按**移出 wheel + 显式开关可达**处置，文件未物理删除（删除会牵动 `install.py` 的 legacy 命令与
`hosts/registry.py` 的 `register_module` 数据）；`aimail_hermes.py` 的 replay 三函数同理保留，实际入站路径已全走 `v2/*`。

### 15.11 更正 R17：宿主本就支持插件平台，无 patch 门禁复跑全绿

**触发**：owner 质疑"理论上 Hermes 不可能不开放加入新的入站 channel，是不是没找到正确加入方式"。

**结论：owner 判断正确，原 R17 是误判。**

官方机制（钉版宿主 `HERMES_COMMIT=9dbaa77c` / 0.23.2，三处俱全）：

| 环节 | 位置 | 依据 |
|---|---|---|
| 注册 API | `hermes_cli/plugins_context.py::PluginContext.register_platform()` | 我们的插件一直用的就是它，没走野路子 |
| 运行时注册表 | `gateway/platform_registry.py::platform_registry` + `PlatformEntry` | 文件头 docstring 写明"Allows platform adapters (built-in **and plugin**) to self-register" |
| 枚举解析 | `gateway/config.py::Platform._missing_()` | 注释："Accept unknown names only for bundled or **runtime-registered** plugin adapters" |
| 安装面 | 宿主插件文档/`ENTRY_POINTS_GROUP = "hermes_agent.plugins"` | Pip plugins 是宿主文档里的一级机制 |

**实测（镜像 `aimail-host-hermes:latest`，整棵树不 patch；只 `pip install` wheel，不建任何插件目录）**

```text
hermes plugins list --user      → aimail │ enabled │ 0.3.0 │ … │ entrypoint
hermes plugins doctor <plugdir> → registrations: 15 tool(s), 0 hook(s)
gateway.log                     → Connecting to aimail... / ✓ aimail connected / Gateway running with 1 platform(s)
```

对照实验（打 patch / 不打 patch 各一次）日志逐行等价 ⇒ patch 无任何作用。

**真实原因**：旧夹具把插件复制成**目录插件**（`$HERMES_HOME/plugins/aimail` + 手写 `__init__.py`），
既不是用户安装方式，又叠着当时我们自己两处宿主 API 误用；症状被误读成"宿主不认插件平台"，
于是用 `by_platform()` 顶部补 `discover_plugins()` 的 patch 把假问题固化了。

**处置与复验**

1. 删除 `tests/host-acceptance/v2/lib/hermes-host-patch.py`（`grep -r` 在 `tests/` 已无命中）；
2. `hosts/hermes.sh`：只 `pip install`，靠 entry point 发现；自检改为
   `plugins doctor <site-packages 插件目录>` + `plugins list --user`（并记 `plugin source` 到证据）；
3. 复跑（0.3.0 wheel、无 patch）：`s1 s2 s3 s4 neg` = **PASS=14**、`s5` = **PASS=2**，
   `RED=0 GAP=0 SKIP_LEGACY=0`；`pytest tests/sdk` **522 passed**；`l0-gate-tests.sh` **PASS**。

**顺带发现（记为 0.3.1 待办）**：pip 安装路径下宿主**不读 wheel 里的 `plugin.yaml`**，而是合成 manifest
（`plugins show aimail` 无 `kind: platform`、无 `config_schema`）。功能无影响（拉取开关一直从环境变量读，
与 tssdk 同名），但"声明的配置面"与"实际读取方式"不一致，应二选一：删 `config_schema` 或改由宿主 config API 读取。

**影响**：`§13` G2 由"产品侧不 patch"升级为**"产品侧与门禁侧同时成立"**；门禁从此测的是 README 承诺的真实安装面。
