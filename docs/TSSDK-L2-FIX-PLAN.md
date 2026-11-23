# tssdk × gateway — SDK L2 修复方案与执行计划

- 日期：2026-11-23
- 范围：**`tssdk/packages/agent-aimail`（dsh / pi / openclaw）与 `aimail-gateway`**
- 目标：**tssdk 三宿主通过 SDK L2（`tests/host-acceptance/v2/`）**，进入 0.5.2 发版流程
- 依据：
  - `docs/qa/PRODUCT-REVISION-REQUEST.md`（质检修订建议书）
  - `docs/aimail-host-sse-contract-v2.md`（现行契约）
  - `tests/host-acceptance/v2/GATE.md` / `README.md` / `cases/*` / `hosts/*`
  - 2026-11-23 当前代码与 `evidence/l2/**` 最新证据

> **执行状态更新（2026-11-23）**：Phase 1 与 Phase 2 P0 已落地，Phase 3 部分落地；实际代码/验证结果见 **§9**。§0 保留为最初计划视图。
- 执行纪律：验证方不改 `src/`；本文档中的产品改动由开发执行，每项改动后跑 L0 + 对应 L2 旅程并留证据。

---

## 0. 状态总表

| 条目 | 来源 | 状态 | L2 阻断 | 本轮 |
|---|---|---|---|---|
| REQ-1 入站被转发 relay | QA | 结案：误判（夹具端口 off-by-one，已修） | 否 | 不改产品 |
| REQ-13 pull 模式台账行被删 | QA | 已修并发版 `v1.0.12`；`pins.env` 已 pin | 否 | 只验证 pin |
| REQ-11 `typebox` 打包冲突 | QA | 待修 | ✅ pi S1 | 执行 |
| REQ-3 OpenClaw 读到邮件但未交付 session | QA | 部分（日志有，V1 前缀判断仍在；S2 日志不匹配） | ✅ openclaw S2/S3 | 执行 |
| REQ-2 预处理早于 workspace preflight | QA | 待修 | 否 | 执行 |
| REQ-4 入站结果类型化 + ack 规则 | QA | 待修 | 否 | 排期 |
| REQ-5 工具结果回传 `raw_key` | QA | 复核：成功路径已不回传，仅需断言 | 否 | 执行（测试） |
| REQ-6 会话工具面可诊断 | QA | 待修（arm 日志部分见 REQ-3） | 部分 S2/S4 | 执行 |
| REQ-7 `agent-level` `onEmail` 逃生口 | QA | 待修 | 否 | 排期 |
| REQ-8 aggregate handle stats 恒 0 | QA | 待修 | 否 | 执行 |
| REQ-9 多地址路由边界 | QA | 待修 | 否 | 执行 |
| REQ-10 `listen.test.ts` 端口断言偶发 | QA | 待验证 | 否 | 排期 |
| REQ-12 SDK 仍留 CLI 入口面 | QA | 待修 | 否 | 排期 |
| CR-1 ASID 用 `os.homedir()`，同机实例碰撞 | Code Review | 待修 | 否 | 执行 |
| CR-2 DSH binding 缺 `agent_system_id`/`manager_address` | Code Review | 待修（S1 当前放宽） | ✅ S1 契约 | 执行 |
| CR-3 已有 binding/归档阻止重新激活 | Code Review | 待修 | 否 | 排期 |
| CR-4 DSH 无 code + domain key 路径 / `aimail_system` 残留 | Code Review | 待修 | 否 | 排期 |
| CR-5 邮件快照/附件 0644/0755 | Code Review | 待修 | 否 | 执行 |
| CR-6 DSH hub 聚合分组不严（V1/别的 ASID 被当代表） | Code Review | 待修 | 否（L2 干净 HOME） | 执行 |
| CR-7 `status.json` 形状不符合 §8.4 | Code Review | 待修 | 否 | 执行 |
| CR-8 路由未按 `gateway_url + recipient` | Code Review | 待修 | 否 | 执行 |
| CR-9 unbind 不停止/更新聚合 | Code Review | 待修 | 否 | 执行 |
| CR-10 move 不校验目标/DSH 重建 SSE | Code Review | 待修 | 否 | 执行 |
| CR-11 外部预写 binding 被当作激活 | Code Review | 待修 | 否 | 排期 |
| CR-12 gateway ASID slot/归属清理/注册失败吞错 | Code Review | 待修 | 否 | 下一 gateway 版本 |
| CR-13 legacy `/activate-address` 无 `system_id`；410 映射；`session_id`；ledger；模板 | Code Review | 部分 | 否 | 部分执行 |

> 说明：`L2 阻断` 指不修就无法让对应宿主 L2 变绿；`排期` 项仍有效，但不阻塞本次 L2 交付。

---

## 1. 复核基线

| 项 | 结果 |
|---|---|
| L0 | 已实测 `bash tests/sdk/l0-gate-tests.sh` → **PASS** |
| tssdk build | `pnpm build` → 15 tools + `tsc` 通过 |
| gateway pin | `tests/agent-env/pins.env:31` → `GATEWAY_VERSION=v1.0.12` + 四平台 sha256 |
| gateway REQ-13 | 本地源码 HEAD `166b6bf` 含 `flows.rs` pull-mode ledger 保留逻辑；`storage.rs` 有 `count_pending_for_mail_id` |
| 最新 DSH L2 | S1/S2/S3/S4 PASS；NEG 为 GAP（夹具 “no web url”），gateway v1.0.12 |
| Pi L2 | QA 判定被 REQ-11 阻断；需先修打包 |
| OpenClaw L2 | 最新日志已见 `pull entry armed ...`，但 S2 正则不匹配；S3 交付未闭环 |
| 本机 L2 | 未执行（环境无 `LLM_API_KEY`；宿主 docker 镜像已存在） |

**结论**：gateway 本轮无 L2 阻断改动；L2 红点集中在 tssdk 的 pi 打包、openclaw dispatch、三宿主 arm 日志、dsh binding 字段。

---

## 2. L2 通过口径

每个宿主分三次跑，避免 stage 相互污染：

```bash
# 投递
bash tests/host-acceptance/v2/run-matrix.sh --host dsh      --stages "s1 s2 s3 s4"
bash tests/host-acceptance/v2/run-matrix.sh --host openclaw --stages "s1 s2 s3 s4"
bash tests/host-acceptance/v2/run-matrix.sh --host pi       --stages "s1 s2 s4"

# 生命周期
bash tests/host-acceptance/v2/run-matrix.sh --host <host> --stages "s5"

# 负例
bash tests/host-acceptance/v2/run-matrix.sh --host <host> --stages "neg"
```

判定：`results.txt` 中 `RED=0` 且 `GAP=0` 才算该 run 绿。  
当前 QA 侧 GAP（不由 tssdk/gateway 修）：

- DSH `neg`：`no web url`（新宿主进程未起来）
- Pi `s3`：`hosts/pi.sh` 未接长驻 pull 进程

---

## 3. 执行阶段

### Phase 0 — 基线冻结（0.5 天）

1. 冻结 `aimail` SHA 与 gateway `v1.0.12`。
2. 记录 L0 输出。
3. 用第 2 节命令建立每宿主 baseline，标注 PASS/RED/GAP。
4. 把 QA GAP 单独列账，不混入产品修复。

### Phase 1 — L2 阻断修复（2–3 天）

| 步骤 | 内容 | 文件 |
|---|---|---|
| 1.1 | 移除 typebox 运行期依赖，schema 改纯 JSON Schema | `core/typebox-params.ts`、`pi/tools.ts`、`package.json` |
| 1.2 | OpenClaw V2 dispatch 判据 + 失败日志 | `openclaw/inbound.ts` |
| 1.3 | Pi/OpenClaw 标准 `SSE reconcile` arm 日志 | `pi/index.ts`、`openclaw/inbound.ts` |
| 1.4 | DSH binding 写 `agent_system_id`/`manager_address` | `dsh/address.ts` |
| 1.5 | 单测 + build + L0 + L2 三宿主 | `test/**` |

### Phase 2 — 发布前 P0 硬化（2 天）

| 步骤 | 内容 | 文件 |
|---|---|---|
| 2.1 | ASID 按宿主/实例 home 派生（含迁移说明） | `core/agent-system-id.ts`、三宿主入口 |
| 2.2 | DSH hub 只聚合当前 ASID 的 V2 binding；V1 走 legacy 兜底 | `dsh/mail-hub.ts` |
| 2.3 | workspace preflight 前置，避免脏写 | `dsh/mail-hub.ts` |
| 2.4 | 邮件/附件/索引 0600/0700 | `core/meta.ts`、`core/search.ts` |

### Phase 3 — P1 契约/可观测性（2 天）

| 步骤 | 内容 |
|---|---|
| 3.1 | `status.json` 对齐 §8.4（`agentSystemId` + `gateways[]`） |
| 3.2 | 路由按 `gateway_url + recipient` 查找 |
| 3.3 | unbind 停聚合/停 SSE；move 校验目标且不重建 SSE |
| 3.4 | Pi/OpenClaw aggregate stats 真实计数 |
| 3.5 | 未命中 recipient 不再回退 pointer（openclaw；pi 仅单地址允许并 log） |
| 3.6 | `mail tools: N` 日志；SDK 410 错误映射 |
| 3.7 | REQ-4 typed result / REQ-7 raw escape hatch / REQ-8 stats 完整化 |

### Phase 4 — 发版（0.5 天）

1. 版本 `0.5.1` → `0.5.2`；同步 `test_v2_contract_surface.py` 版本断言与 release notes。
2. `rm -rf dist && pnpm build && npm pack`；检查 tarball：
   - 无 `dependencies.typebox`
   - `dist` 无 `from "typebox"`
   - 无 `register-cli` / `cli-op-entry`（若 REQ-12 一并执行）
3. 跑 L0 + L2 全矩阵，保存 `evidence/l2/<ts>-<host>/`。
4. 发布 npm；L3 由验证方在发布后执行。

---

## 4. 关键改动说明

### A1 — REQ-11：typebox 运行期依赖

**策略**：先走方案 2（纯 JSON Schema，不 import typebox）；若 pi 运行期拒绝，再切方案 3（pi 专用入口 + `peerDependencies.typebox: "*"`，dsh/openclaw 走纯 JSON Schema）。

- `core/typebox-params.ts`：删除 `Type/TSchema` 静态 import；输出：
  ```ts
  { type: 'object', properties: { ... }, required: [...], additionalProperties: false }
  ```
  enum → `{ type:'string', enum:[...] }`；optional → 不进入 `required`。
- `pi/tools.ts`：删除 `import type { TSchema } from 'typebox'`；使用本地 `JsonSchema`；在 `pi.registerTool` 边界做一次类型断言。
- `package.json`：删除 `dependencies.typebox`；`devDependencies.typebox` 仅用于解析宿主 `.d.ts`。
- 新增测试：schema 形状、required/optional、enum/default、tarball 无 runtime typebox。

**注意**：QA 已验证“只把 typebox 移到 peer”会让 dsh `Cannot find package 'typebox'`；因此必须同时去掉共享 core 的静态 import，不能只改 package.json。

### A2 — REQ-3：OpenClaw dispatch

- `resolveDispatchAgentId` 判据改为 `Boolean(cfg.agent_system_id) || 旧前缀`；地址 local part 不在 `configuredAgentIds()` 时回退 `resolveAgentId()`，最终 `main` 兜底。
- `deliverInbound` 失败日志补 `agentId`、`sessionKey`、`status`、`detail`、`address`。
- 保持 `deliverPulled` 在 `!ok` 时 throw（不 ack）。

### A3 — arm/reconcile 日志

Pi/OpenClaw `startInboundPull` 在拿到 handles 后输出：

```text
[aimail-pull] SSE reconcile: <streams> gateway stream(s) from <bindings> binding(s)
```

- `streams = handles.length`
- `bindings = 当前 ASID + 当前 gateway 下 active（!paused、有 api_key）的 binding 数`
- DSH 已有同类日志（`mail-hub.ts`），三宿主格式统一。
- 这样 S2 的 `SSE reconcile: [1-9]` 与 S4 的 `from [2-9] binding` 都能命中。

### A4 — DSH binding 字段

- `dsh/address.ts` B1/B2 的 `next` 增加 `agent_system_id: await getAgentSystemId()`。
- 激活返回的 `manager_address` 写入 binding（当前被丢弃）。
- 不做启动时静默 backfill；旧 binding 缺 ASID 由 Phase 2 的 legacy 兜底/重新激活处理。

### B1 — ASID home 派生

- `getAgentSystemId()` 增加宿主 home resolver：
  - DSH：`~/.dsh/profiles/<profile>`
  - Pi：`~/.pi`
  - OpenClaw：`~/.openclaw`
  - standalone：`os.homedir()`
- 这是身份变更：旧 binding 的 ASID 会变化；必须配合 legacy 兜底或明确“重新激活”，release notes 写清楚。

### B2 — DSH hub 分组

- 只把 `isV2AgentScopeBinding(b) && b.agent_system_id === currentAsid` 的 binding 放入 aggregate group。
- 同 gateway 一条 aggregate。
- 无 ASID 的 V1 binding 不再作为 aggregate 代表：走 per-address legacy 循环，或明确跳过并 log `binding_not_v2_reactivate`。
- 不允许拿 V1 key 带 `X-Agent-System-Id` 打 aggregate（gateway 会 403，整条流起不来）。

### B3 — REQ-2 preflight

- 先 `workspaceFor(cfg)`；无 workspace 直接 `return false`（不写 meta/snapshot/FTS、不 ack）。
- 再 `processInboundMail`；需要彻底幂等时给 `processInboundMail` 增加 `persist:false`，session 接受后再落盘。

### B4 — 数据权限

- 邮件 meta/快照/附件：文件 0600，目录 0700。
- `.search` 目录 0700；附件 `copyFile` 后 chmod 0600。
- 单测在 `umask 022` 下断言。

---

## 5. 验收命令

```bash
# L0
bash tests/sdk/l0-gate-tests.sh

# build + 单测
cd tssdk && pnpm build && pnpm test

# L2（按第 2 节分宿主/分阶段）
```

发版门禁建议：`L0 PASS + 三宿主 L2 RED=0/GAP=0 + 无 P0 未关闭`。  
若 ASID home 或 legacy fallback 未做，必须在 release notes 明确“已知限制”，不能宣称“零差异”。

---

## 6. 需要 QA 配合 / 范围外

| 事项 | 归属 | 说明 |
|---|---|---|
| DSH `neg` “no web url” | QA | 最新 evidence 的 NEG GAP，不是产品 RED |
| Pi S3 长驻 pull 未接线 | QA | 当前 `hosts/pi.sh` 明确不接；需长驻 pi 或书面排除 |
| S1 `agent_system_id` 放宽断言 | QA | 产品修完后改为 `startswith("asid_")`，否则继续假绿 |
| 版本断言 `0.5.1` | QA/测试 | 0.5.2 发版前同步 |
| gateway ASID set 删除 / slot 按 ASID / legacy `/activate-address` / 模板 | 下一 gateway 版本 | 本轮不动；改 gateway 必须重新发版并改 pin |

---

## 7. 风险

| 风险 | 对策 |
|---|---|
| 纯 JSON Schema 被 pi 校验拒绝 | 1 天 spike；失败切“pi 专用入口 + peer typebox” |
| OpenClaw hook agentId 策略仍 400 | 日志带 `agentId/sessionKey`；回退 `main`；不要盲改 routing |
| ASID 身份变更导致旧 binding 不再聚合 | Phase 2 的 legacy 兜底；release notes 写迁移说明 |
| L2 LLM 抖动 | 只断言 token/ack，不断言措辞 |
| 版本断言/文档未同步 | 发版 checklist 固定包含 0.5.2 全仓 grep |

---

## 8. 变更记录

| 日期 | 变更 |
|---|---|
| 2026-11-23 | 初稿：合并 QA REQ-1..13 与代码 review CR-1..13，确定 L2 阻断/发布 P0/排期三层 |

---

## 9. 本次执行结果（2026-11-23）

> 本节是实际落地状态；§0 保留为最初的计划状态。代码已完成项均通过 `pnpm build` 与 L0。

### 9.1 已完成（代码 + 单测）

| 项 | 落地内容 | 验证 |
|---|---|---|
| REQ-11 | `typebox-params.ts` 改为纯 JSON Schema；`pi`/`openclaw` 不再静态 import typebox；`package.json` 移除 `dependencies.typebox`（仅保留 devDependency 供 tsc 解析宿主 d.ts） | `dist/**/*.js` 无 typebox import；`dependencies == {}`；新增 `tool-schema.test.ts` 4 项通过 |
| REQ-3 | `resolveDispatchAgentId` 增加 `Boolean(cfg.agent_system_id)` 判据；dispatch 失败日志带 `agentId/sessionKey/status/detail/address` | `dispatch-target.test.ts` 6 项通过（含新增 V2 用例）；openclaw 12 个测试文件 52 项通过 |
| REQ-3/6 | Pi/OpenClaw `startInboundPull` 输出标准 `SSE reconcile: N gateway stream(s) from M binding(s)`；新增 `countActiveAgentSystemBindings` | S2/S4 正则可命中；`v2-pointer` 4 项通过 |
| CR-2 | DSH binding 写入 `agent_system_id` 和 `manager_address` | `address-lifecycle.test.ts` 等通过（旧 `self-provision.test.ts` 已删除） |
| CR-1 | `getAgentSystemId` 增加 host-home resolver；DSH 用 `~/.dsh/profiles/<profile>`，Pi 用 `~/.pi`，OpenClaw 用 `~/.openclaw` | `agent-system-id.test.ts` 5 项通过（新增 resolver 用例） |
| CR-6 | DSH hub 只聚合当前 ASID 的 V2 binding；V1 binding 走 per-address legacy；其他 ASID 跳过并 log | `mail-hub.test.ts` 通过；S4 仍输出 `from N binding(s)` |
| REQ-2 | `deliver()` 先 workspace preflight，再 `processInboundMail` | `mail-hub.test.ts` 通过 |
| CR-5 | meta/快照/附件/索引目录 0600/0700；附件 chmod | `meta.test.ts` 13 项通过 |
| CR-9 | 新增 `onBindingRemoved/fireBindingRemoved`；unbind 后 DSH `hub.reconcile()`；Pi/OpenClaw 走已有 re-arm 入口 | `address-lifecycle.test.ts` 20 项通过 |
| CR-8 | DSH `findBinding(email, gatewayUrl)` 路由按 gateway_url + recipient | 单测通过 |
| CR-7 | DSH `statusBody()` 输出 §8.4 形状（`agentSystemId` + `gateways[]`），保留 `streams` 兼容 | 单测通过 |
| CR-10 | move 校验目标目录存在；DSH move 不再 `reconcile()` 重建 SSE | `address-lifecycle.test.ts` 通过（测试目录先创建） |
| CR-13（部分） | SDK 错误映射增加 410 `CODE_GONE`；三宿主新增 `mail tools: N` 日志 | 单测通过 |

### 9.2 验证结果

```text
cd tssdk && pnpm build                          PASS
cd tssdk && pnpm test                           61 files / 523 tests PASS
bash tests/sdk/l0-gate-tests.sh                 PASS
grep -r "from ['\"]typebox" dist --include=*.js  (empty)
node -e "pkg.dependencies"                      {}
```

为通过 L0，同步更新了 `tests/agent-env/docker/pnpm-warm.json`：运行期依赖已为空，warm set 同步为空。

测试夹具按新契约做了三处对齐（非产品放宽）：

1. `address-lifecycle.test.ts`：move 前创建目标目录（§13.3 要求目标存在）。
2. `openclaw-aimail.test.ts`：enum 断言从 TypeBox `anyOf` 改为 JSON Schema `enum`（REQ-11）。
3. `v2-pointer.test.ts`：测试宿主 `openclaw.json` 声明 `agents.entries {a,b,main}`，使地址级 dispatch 命中宿主真实 roster（REQ-3）。

### 9.3 尚未执行

| 项 | 原因 / 下一步 |
|---|---|
| L2 实机矩阵 | 当前环境无 `LLM_API_KEY`；代码已具备 L2 阻断修复，需 QA 机器按 §2 命令跑三宿主并留 evidence |
| B8 重新激活语义 | 涉及 `already_bound`/`restore` 行为变更与迁移说明，建议单独 PR |
| B9 无 code/domain key 路径、`aimail_system` 清理 | 行为删除，需 release notes + 旧用户迁移说明 |
| C1 typed result、C3 stats、C4 pointer fallback | 非 L2 断言，排期 Phase 3 后续 |
| C5 REQ-12 CLI 入口清理、C6 listen 测试 | 排期 Phase 4 |
| D2 gateway ASID slot/归属清理/注册失败 | 下一 gateway 版本；本轮不动 gateway，避免重新发版+改 pin |

### 9.4 下一步交接

1. QA 用第 2 节命令跑 L2；重点看：
   - dsh S1 `agent_system_id` 是否为 `asid_`（当前 S1 脚本仍放宽，建议收紧）；
   - pi S1 插件是否加载、15 工具是否可见；
   - openclaw S2/S3 的 `SSE reconcile: ... from ... binding(s)` 与 ack；
   - DSH `neg` 的 “no web url” 是否仍为夹具 GAP。
2. 若 pi 运行期拒绝纯 JSON Schema，按 REQ-11 备选方案切“pi 专用入口 + peer typebox”，不要回退到 dependencies。
3. 0.5.2 发版前同步 `test_v2_contract_surface.py` 版本断言与 release notes。

---

## 10. 第二轮补充完成（2026-11-23）

### 10.1 本轮新增落地

| 项 | 落地内容 | 验证 |
|---|---|---|
| B8 重新激活 | core `aimailAddress`：**带 code 时优先激活**，归档 restore 只在无 code 时生效；unbind 后用新 code 重新激活/迁移不再被归档短路 | `address-lifecycle.test.ts` 20 项通过 |
| B9 DSH code-only | `dsh/address.ts` 删除“无 code + domain/admin key 注册地址”的 B1 路径；无 code 且无 binding ⇒ `code_required`；激活不再做本地 system/domain/scope 校验；旧 system 仅提供 gateway URL；跨 GatewaySystemId 激活成功后归档旧 binding | `address-lifecycle.test.ts` 等通过；`system-core.provisionSystem` 与 `self-provision.test.ts` 已删除 |
| B9 工具面清理 | 删除 `src/dsh/system.ts`、`src/runtime/activation/system-tool.ts`、`src/runtime/activation/system.ts`；移除 `./system` export、`dsh/index` 的 system 导出、runtime system-tool 导出；cordis 注释同步 | `pnpm build` / L0 通过；tarball 无 system 工具入口 |
| C1 入站类型化 | `core/types.ts` 新增 `InboundResult` + `isAckedInboundResult`；DSH `deliver/deliverOne` 返回 typed result；`handleBatch` 只对 `accepted/intercepted` ack，`deferred/rejected` 留 pending；`deliverBatches` 同样只 ack accepted/intercepted，rejected 计 error | `mail-hub.test.ts`、`v2-grouping.test.ts` 通过 |
| REQ-5 密钥不回传 | `tools-activate.test.ts` 增加断言：成功结果不含 `raw_key`/`api_key`，密钥只落 binding（0600） | 6 项通过 |
| C3 aggregate stats | `startPolling` 接受外部 `stats`；`startSystemPull` 返回 live `stats()`；Pi/OpenClaw aggregate handle 不再硬编码 0 | `pull-stream-state-machine` / `v2-grouping` 通过 |
| C4 路由边界 | Pi/OpenClaw `resolveInboundTarget`/`resolvePiTarget` 删除 pointer fallback：未命中 recipient 直接 no-target（不 ack），不再静默投给 pointer 地址 | openclaw 12 文件 52 项通过 |
| C5 CLI 入口清理 | 删除 `dsh/pi/openclaw register-cli.ts`、`core/cli-op-entry.ts`、`core/cli-arg.ts`；移除 `registerCli` registry 字段与导出；`sdk-ops` op* 保留为内部 API（仅测试/宿主内部调用） | `pnpm build` / L0 通过；tarball `register-cli` 文件数 **0** |
| C6 listen 测试 | `listen.test.ts` 新增 `freeRange(n)`，所有 `p+1/p+2` 假设改为连续空闲端口块 | 7 项通过（可重复） |
| Gateway D1（源码） | base `aimail-gateway` legacy `/api/v1/activate-address` 响应补 `system_id`；base + advanced 激活邮件补 Appendix A 的 host-plugin 安装提示（未发版；L2 仍用 pinned v1.0.12） | `cargo fmt --check`；base `cargo test --lib` **415 passed**；advanced `cargo check` + `cargo test email_notify` 3 passed |

### 10.2 当前验证

```text
cd tssdk && pnpm install --frozen-lockfile      PASS
cd tssdk && pnpm build                          PASS
cd tssdk && pnpm test                           59 files / 507 tests PASS
bash tests/sdk/l0-gate-tests.sh                 PASS
npm pack: dependencies {} · register-cli files 0
cd aimail-gateway && cargo test --lib           416 passed
cd aimail-advanced && cargo test email_notify   3 passed
```

### 10.3 Gateway D2（已补，待发版）

| 项 | 落地 |
|---|---|
| ASID slot 按 ASID | `http.rs` 新增 `agent_system_slot_id()`；aggregate SSE + aggregate long-poll 共用同一 synthetic 负 id slot，不再按“代表地址 key”抢占；`list_agent_system_pending` 也持有 slot |
| ASID 归属删除 | `storage.rs` 新增 `remove_agent_system_address_if_unowned(system_id,address)`；`DELETE /api/v1/api-keys/:id` 之后调用：仅当该地址没有其他 active agent key 时删除 `agent_system_addresses` 行 |
| 注册失败可观测 | `bind_agent_system_address` 的 `.ok()` 改为 `map_err(tracing::warn!)`，激活成功但归属写入失败不再静默 |
| 测试 | 新增 `agent_system_ownership_is_removed_only_after_last_agent_key`；gateway `cargo test --lib` **416 passed** |

> D2 仍未发版；`pins.env` 仍是 v1.0.12。发版后必须更新 pin 并重跑 L2/L3。

### 10.4 仍未完成

| 项 | 原因 / 下一步 |
|---|---|
| L2 实机矩阵 | 已用 verifier 的 Xiaomi key 跑完 s1/s2/s3/s4/s5/neg；结果见 §11。pi s3 是已知 fixture 限制（无 live delivery），不是产品 RED |
| `aimail_system` 文档/旧 acceptance 脚本 | 代码入口已删；旧 `tests/dsh-acceptance` 文档仍描述历史场景，需归档 |
| CR-11 外部预写 binding | 需要 gateway 侧归属校验端点；本地无法仅凭文件证明“不是外部预写” |
| CR-13 address ledger / legacy session_id | 无 ledger 文件格式定义；legacy `dsh/inbound.ts` 未挂载但未删 |

## 11. L2 实机结果（2026-11-23，已执行）

环境：本机 linux-amd64；SDK dev build **0.5.1**；published gateway **v1.0.12**；真实 Xiaomi LLM key；三宿主 Docker 镜像。

| run | 命令 | 结果 |
|---|---|---|
| DSH s1/s2 | `run-matrix.sh --host dsh --stages "s1 s2"` | **PASS=10 RED=0 GAP=0** |
| Pi s1/s2 | `--host pi --stages "s1 s2"` | **PASS=9 RED=0 GAP=0** |
| OpenClaw s1/s2 | `--host openclaw --stages "s1 s2"`（重跑） | **PASS=9 RED=0 GAP=0** |
| all s4+neg | `--host all --stages "s4 neg"` | **PASS=9 RED=0 GAP=0** |
| all s5 | `--host all --stages "s5"` | **PASS=6 RED=0 GAP=0** |
| all s3 | `--host all --stages "s3"` | **PASS=6 RED=0 GAP=1**；GAP = pi s3 known fixture（无 live delivery） |

OpenClaw 首次 s1/s2（06:39Z）出现 3 RED：容器 turn 报 gateway connection failed，gateway 日志显示脚本退出后 22s 正常 shutdown；无代码变更，06:42Z 重跑 **9/9 PASS**，证据保留为 `openclaw-s1s2-first-attempt.log`，归类为 fixture 瞬时问题。

证据目录：`evidence/l2/round2-20261123/`。最后的 `system-core` 清理后 DSH s1/s2 重跑 **PASS=10**（`dsh-s1s2-final.log`）。
