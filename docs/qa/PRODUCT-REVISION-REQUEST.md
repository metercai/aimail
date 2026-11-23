# 产品修订建议书（tssdk + gateway）

- 日期：2026-11-23（合并 `docs/tssdk-v2-revision-plan.md` 逐条复核后重写）
- 范围：**仅 `tssdk/packages/agent-aimail`（dsh / pi / openclaw 三宿主）与 `aimail-gateway`**
- 明确不含：pysdk 的 SSE-V2 改造（hermes / deerflow 在 L2/L3 中计为 `SKIP_LEGACY`，不参与判定）
- 最终目标：**tssdk 三宿主全部通过新 L2（`tests/host-acceptance/v2/`）与 L3（`tests/l3/run-v2.sh`，dsh×4）**
- 执行纪律：验证方不改 `src/`；每项修完按"判据"在框架内自测，再交回复验

---

## 0. 复核结论（原 `tssdk-v2-revision-plan.md`）

| 原条目 | 现状（0.5.1 实测/读码） | 处置 |
|---|---|---|
| 3.1 `isAgentScopeBinding` V1 前缀 | **已修**：现同时接受 V1/V2；dsh 实测 `SSE reconcile: 1 gateway stream(s)` | 关闭 |
| 3.2 pull client 缺 aggregate path/header | **已修**：新增 `buildAgentSystemClient` + `startAgentPullWithClient` | 关闭 |
| 3.3 OpenClaw 读到邮件未进 session | 未验证（旧夹具证据，需在 ING-1 修复后复测） | **并入 REQ-3** |
| 3.4 DSH 预处理早于 workspace preflight | **仍有效**：`dsh/mail-hub.ts:322 processInboundMail` 先于 `:324 workspaceFor` | **并入 REQ-2** |
| 3.5 ack 语义未类型化 | **仍有效**：仍以 boolean 表达投递结果 | **并入 REQ-4** |
| 3.6 DSH 会话工具面 | **撤回为缺陷**（真因是我方夹具：装解包目录 + 未设审批模式）；保留"可诊断性"诉求 | **并入 REQ-6（P2）** |
| 3.7 `agent-level` onEmail 逃生口 | **仍有效**：`core/agent-level.ts` 仍接受 `onEmail` 绕过预处理 | **并入 REQ-7** |
| 3.8 OpenClaw stats 恒 0 | **仍有效**：`openclaw/inbound.ts` `stats: () => ({pulled:0,acked:0,errors:0})` | **并入 REQ-8** |
| 3.9 多地址路由边界 | **部分有效**：已有 `resolveByRecipient`（exact → persona 兜底），仍需"不得静默取首 binding" | **并入 REQ-9** |
| F1 bind 返回 `raw_key` | **仍有效** | **并入 REQ-5** |

---

## REQ-1（P1，gateway 阻断）SMTP 入站被转发到出站 relay，未入库

**现象**：外部发件人 → 本地 pull 地址，SMTP 返回 250，但 `emails` 0 行、`pending_deliveries` 0 行；
同一封信出现在 `[relay] smtp_server` spool。

**按规范四层排查（逐层实证）**：① 发件人非内部 host 地址（内部会被 `550 Internal sender …` 拒，未出现）；
② 白名单两条在库（manager 自动 + 外部发件人）；③ `emails` 0 ⇒ 入站未完成；④ `pending_deliveries` 0。
环境侧已确认正确：`system_domains` 含域与地址、`domain_addr_meta` 有 manager、`agent_system_addresses` 有归属、
`[smtp] hostname`=测试域、域 id=域名、注入到网关 SMTP 监听口。

**判定**：系统设计不存在"SMTP 入站 → SMTP 出站"路径，属缺陷（Owner 判定）。

**修法**：入站（RCPT 命中本地地址）只走 `create_inbound` + 本地队列；`relay.smtp_server` 仅用于出站（含 DSN/回复）。

**判据（L2 S3 / L3 welcome）**：入站后 `emails` 出现 1 行、`pending_deliveries` 出现 1 行 → 宿主流取走 → 行消失且 ack 晚于 turn；
**sink spool 不得出现该入站原信**。

---

## REQ-2（P1，tssdk·dsh）预处理早于 workspace preflight

**位置**：`src/dsh/mail-hub.ts:319-331`（`deliver()`）

```text
processInboundMail(payload,…)   // :322 附件下载 / meta / snapshot / FTS 等副作用
workspaceFor(cfg)               // :324 目标可能不存在
routeThread(...)                // :331
```

**影响**：workspace 不存在时已经产生副作用，再返回 false 不 ack —— 数据脏、重投不可幂等。
**修法**：`binding → workspaceFor → preflight(session/workspace) → processInboundMail → session`；只有目标确定存在后才做重型预处理。
**判据（L2 S3）**：workspace 缺失场景下 `emails`/附件/索引均无新增，且不 ack；正常场景 S3 绿。

---

## REQ-3（P1，tssdk·openclaw）读到邮件但未交付 session（需先加证据再修）

**现状**：STREAM 200、`GET /contacts` 已读，但无 turn、无 ack、pending 仍在（旧夹具证据）。
**修法**：
1. `deliverInbound` 的每个失败分支记录 `status / detail / agentId / sessionKey / binding address`；
2. `logAimailDispatch` 覆盖 pull 路径；
3. `resolveDispatchAgentId` 去掉 V1 prefix 依赖，改判 `Boolean(cfg.agent_system_id)`；
4. 单测：`ok=false` 绝不 ack。
**判据（L2 S3 on openclaw）**：入站 → turn（含唯一串）→ ack；失败时必须有一条可定位的日志。

---

## REQ-4（P1，tssdk·三宿主）入站结果类型化 + ack 规则

**现状**：boolean 表达投递结果，`delivered`/`intercepted`/`failed`/`no-binding` 不可区分。
**修法**：

```ts
type InboundResult =
  | { status: 'accepted' }
  | { status: 'intercepted' }
  | { status: 'deferred' }
  | { status: 'rejected'; detail?: string }
```
ack：`accepted/intercepted/deferred → ack`；`rejected → 不 ack`。
**判据（L2 S3）**：`rejected` 场景 pending 保留；其余三态 ack；三种情况各有断言。

---

## REQ-5（P2，tssdk）`aimail_address` 工具结果回传 `raw_key`

**修法**：工具结果只回 `{success, action, email, system_id, agent_system_id, workspace_path}`；密钥仅落 binding（0600）。
**判据**：bind 结果与宿主日志无 `raw_key`/`api_key` 明文；binding 仍 0600 可用（L2 S1 保持绿）。

---

## REQ-6（P2，tssdk·dsh）会话工具面可诊断（不是缺陷，是可见性）

**诉求**：宿主 adapter 启动/建会话时打印"该 session 是否拿到 mail toolset（15 件）"，
并把 `session tools 列表` 落到日志；不要把"会话创建成功"当成"工具可用"。
**原因**：本次 S4/S5（第二地址绑定、pause/unbind）连续两轮无 tool 帧，无法从日志区分"工具面没注入"与"模型没调用"。
**判据**：日志中能直接看到 `mail tools: 15` 或 `mail tools: 0`；据此判定 S4/S5 归属。

---

## REQ-7（P2，tssdk）`agent-level` 的 `onEmail` 逃生口

**修法**：标注为 raw escape hatch（生产禁用）；或改名 `onRawDelivery` 且必须显式 opt-in。
**判据**：默认路径不再能绕过 `processInboundMail`。

---

## REQ-8（P2，tssdk·openclaw）aggregate handle 的 stats 恒 0

**修法**：透传 `system-pull` 实际统计（至少 round / delivery / ack 计数）。
**判据**：L2/L3 证据里的 stats 与实际投递数一致。

---

## REQ-9（P2，tssdk·pi/openclaw）多地址路由边界

**现状**：已有 `resolveByRecipient`（exact → persona 兜底），但仍存在"未命中回退 pointer 地址"的路径。
**修法**：pi 只允许单个地址；openclaw 多地址必须按 recipient 命中，未命中显式报错，**不得静默取第一个 binding**；日志记录 recipient/binding/agentId。
**判据（L2 S4）**：两地址各自只收到自己的邮件；未命中场景有明确错误码。

---

## REQ-10（P3，tssdk 测试）`listen.test.ts` 端口断言偶发

**修法**：断言放宽到契约允许的端口集合，或改为"端口来自配置"的语义断言。
**判据**：连续 20 次单测无红。

---

## 附：框架内自测方式（开发交付前请自跑）

```bash
# 开发只跑 L2（dev 构建，三宿主，本机平台）
export LLM_API_KEY=…
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2 s3 s4 s5 neg"
```

**L3 不在开发职责内**：L3 是发版后回归，只能由验证方在发布件上执行；
开发不得运行 L3，也不得以 L3 结果作为交付依据。

结果口径：`PASS / RED（产品缺陷）/ GAP（夹具缺陷）/ SKIP_LEGACY（pysdk，不计判定）`；
退出码在 `RED>0 或 GAP>0` 时非 0。**不接受 skip 冒充绿**。

---

## REQ-11（P1，tssdk·打包冲突：pi 与 dsh 对 `typebox` 的要求相反）`typebox` 被列入 dependencies，pi 1.5.2 直接拒绝加载插件

**现象**（L2 pi 实测，0.5.1）
```
Host-provided extension packages must be declared in peerDependencies with a "*" range,
not dependencies: typebox.
```
pi 随后报 `No API key found for the selected model` 并**不加载插件** → 会话里没有任何邮件工具 → 绑定不可能发生。

**证据**：`tssdk/packages/agent-aimail/package.json`
```json
"dependencies": { "typebox": "^1.3.6" }
```
**回归**：同版本流程在 0.5.0 的 pi cell 上是绿的（工具注册 14 件、绑定成功）；0.5.1 把 `typebox` 放进 dependencies 后 pi 端失效。

**修法**：把 `typebox` 移到 `peerDependencies: { "typebox": "*" }`，并加 `peerDependenciesMeta.typebox.optional = true`；
若确实需要运行期依赖，则改为宿主注入（pi 的 extension loader 明确禁止自带 host-provided 模块）。

**判据（L2 pi S1/S2）**：`pi install <tgz>` 不再报该错；宿主日志出现工具注册；S1 8 条断言全绿。

**验证方已实测该修法并回滚（重要）**：把 `typebox` 从 `dependencies` 移到
`peerDependencies: "*"`（+optional）后：
- **pi**：插件正常加载，L2 `S1 7/7 + S2 2/2 + NEG 2/2 = 11 PASS / 0 RED` ✅
- **dsh**：立刻回归 —— `S1 no binding`、日志 `Cannot find package 'typebox'` ❌（dsh 不提供该宿主模块）

⇒ 单纯移动声明**不可发布**。可选修法（请开发选一并实现）：
1. **打包内置**：构建时把 `typebox` 内联进 `dist`（当前是 `tsc -b`，需加 bundler/或对该模块做内联步骤）；
2. **去掉运行期依赖**：`typebox-params.js` 改为直接产出 JSON Schema（不 import typebox）；
3. **双入口**：`pi` 专用入口声明 peer，`dsh/openclaw` 入口保留 dependencies。

**判据**：pi L2（S1/S2/NEG）与 dsh L2（S1/S2/S4/S5/NEG）同时保持绿。
