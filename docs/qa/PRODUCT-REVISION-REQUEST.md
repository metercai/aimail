# 产品修订建议书（tssdk + gateway）

- 日期：2026-11-23（第二次修订：REQ-1 结案改写、新增 REQ-13、补齐 L2 自检包与门禁口径）
- 范围：**`tssdk/packages/agent-aimail`（dsh / pi / openclaw 三宿主）与 `aimail-gateway`**
- 明确不含：pysdk 的 SSE-V2 改造（hermes / deerflow 在 L2/L3 中计为 `SKIP_LEGACY`，不参与判定）
- 最终目标：**tssdk 三宿主通过 L2（`tests/host-acceptance/v2/`）**；**L3（`tests/l3/run-v2.sh`）为 dsh × 4 平台，仅验真值**
- 执行纪律：验证方不改 `src/`；每项修完按"判据"在框架内自测，再交回复验（新 SHA，全量重跑）

---

## 0. 本轮状态总表

| 条目 | 状态 | 谁动 |
|---|---|---|
| REQ-1 入站被转发 relay | **结案：判定错误**。产品路径不存在；症状真因是我方夹具端口 off-by-one，已由验证方修复 | 已关闭 |
| REQ-13 入站台账行被删（pull 模式） | **产品缺陷，已修在 gateway main `0c46b38`，未发布**；且该提交 `cargo fmt --check` 红，发版前置未满足 | 开发（fmt + 发版） |
| REQ-11 pi `typebox` 打包冲突 | 仍有效，未修 | 开发 |
| REQ-12 SDK 仍留 CLI 入口面 | 仍有效，未修 | 开发 |
| REQ-2…REQ-10 | 仍有效（P1/P2/P3 不等） | 开发 |
| L2 自检包 | 已就绪，可交付开发自跑（`s1 s2 s4 s5 neg`；`s3` 等 REQ-13 发版） | 验证方（已完成） |
| L3 矩阵 | 已收敛为 dsh × 4 平台（4 格），非 dsh 的 `L3_AGENT` 由脚本直接拒绝 | 验证方（已完成） |

---

## REQ-1（P1，原判"gateway 阻断"）SMTP 入站被转发到出站 relay —— **结案：误判**

**原判断**（本文件上一版）：外部发件人 → 本地 pull 地址，SMTP 250 但 `emails=0`/`pending=0`，
同一封信出现在 `[relay]` spool ⇒ 判为"产品把入站转发出站"。

**复核证据（开发 `docs/qa/REQ1-ingress-evidence.md` + 验证方独立活体复现）**

验证方用 pinned gateway v1.0.11（sha256 `4a3e3e88cdd6…`，`gw_fetch` 校验后启动）复跑：

```text
A. 探针发到 sink 口 12660（= GW_SMTP，relay 对端）：
   before   emails=0 pending=0 spool=3343B
   250 queued
   after    emails=0 pending=0 spool=3652B      ← 原信落进 spool
   gateway 日志 "Email received and stored via SMTP" = 0 行
   ⇒ 信根本没进网关，sink 直接收下并回 250。原报四项症状由此一条解释。

B. 同一封信发到正确入站口 12661（= GW_SMTP+1）：
   Email received and stored via SMTP  subject=req1-b-inbound
   Immediate forward (first attempt)   delivery_type="webhook"     ← 不是 smtp/relay
   Inserted pending delivery (pull mode) domain=req1v@test.local
   relay spool 中该入站主题命中 0 次（A 的探针主题命中 3 次作对照）
```

**真因（夹具缺陷，验证方责任，已修）**
`tests/host-acceptance/v2/lib/gw.sh:63` 网关入站 `[smtp] bind = smtp+1`；`:70` `[relay] smtp_server = smtp`；
`:83` sink 也起在 `smtp`。而宿主侧注入写的是裸 `$GW_SMTP`：
```diff
- tests/host-acceptance/v2/hosts/dsh.sh:89      python3 - "$addr" "$token" "$GW_SMTP"
- tests/host-acceptance/v2/hosts/openclaw.sh:103 同上
+ 两处改为 "$((GW_SMTP + 1))"（容器侧 :18 / :19 本来就正确）
```

**结论**：`smtp 入站 → smtp 出站` 的产品路径不存在（静态：`backoff.rs detect_delivery_type` 对
`direction=inbound` 恒返回 webhook；`flows.rs` 非 webhook 分支才走 relay。活体：上表 B）。
**本项不做产品改动**；`S3` 的判据不变，但复测前置见 REQ-13 与「附：L2 自检包」。

---

## REQ-13（P0，gateway）pull 模式入站台账行在入队后即被删除

**现象**（pinned v1.0.11，验证方复现）：信发到正确入站口 → `Immediate forward delivery_type="webhook"` →
`Inserted pending delivery (pull mode)`，随后 **`emails=0`、`pending=1`**。
判据"入站后 `emails` ≥1 行"因此永远不可达 —— 属产品缺陷，不是判据写错。

**根因**：`src/core/scheduler/flows.rs` `finalize_after_delivery`：pull 入队即 mark endpoint success
⇒ all endpoints 完成 ⇒ 立即 `complete()` + `cleanup_completed_email()`。pull"入队成功"≠"已投递"。

**开发已给的修法**（gateway main `0c46b38`，未发布）：入站 + webhook + 该 mail 仍有 pending 行
⇒ `mark_delivered`（台账行留 `status='delivered'`，由既有 `expired_delivered` 批处理按
`delivery_window_secs` 清理）；ack 只删 pending 行、不动台账行；其余路径行为不变。

**验证方已核**：`0c46b38` 存在、改动落在 `flows.rs` + `storage.rs`（`count_pending_for_mail_id`）、
带 4 个单测；`cargo test --lib` **415 passed / 0 failed**；`cargo clippy --all-targets -D warnings` **干净**。

**发版阻塞（必须先清）**：`0c46b38` 上 `cargo fmt --all --check` **rc=1**，
命中 `src/core/scheduler/flows.rs:563`、`:572`（新加测试块的换行）。上一版 `v1.0.11` 是 fmt 干净的，
所以这是本次提交带进来的。release workflow 的 `gate` job 第一项就是 Format ⇒ 现在打 tag 一定红。

**最小清关动作**：`cargo fmt --all` → 提交 → push（**只动格式，不改逻辑**）。验证方按 §2.1 口径核对
"去空白后 token 流一致"，然后发版。

**发版后判据（L2 S3，三宿主各一份）**：
入站 → `emails`≥1 且 `status='delivered'` + `pending`≥1 → 宿主取走 → `pending=0`、台账行保留至 retention
→ relay spool 无该入站原信。

**发版后我方动作**：更新 `tests/agent-env/pins.env`（`GATEWAY_VERSION` + 四平台 sha256）→ 重跑 L2 → 重跑 L3 dsh。

---

## 附：L2 自检包（开发交付前请自跑，验证方会复核）

**前提**
```bash
# 1) LLM key（xiaomi，OpenAI 兼容路由）
export LLM_API_KEY=<xiaomi key>
# 2) 宿主镜像（本机已有则跳过；缺了用 host-images.yml 构建）
docker images | grep aimail-host-      # dsh / pi / openclaw
# 3) 网关不用自己起：pins.env 的已发布二进制会自动下载并校验 sha256
```

**命令**
```bash
# 三宿主全量（今天可判定：s1 s2 s4 s5 neg）
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2 s4 s5 neg"
# 单宿主
bash tests/host-acceptance/v2/run-matrix.sh --host dsh --stages "s1 s2 s4 s5 neg"
```

**今天的结果预期（务必区分三类，不要互相顶替）**

| 阶段 | 预期 | 若不符合 |
|---|---|---|
| `s1` 激活 → bind | 三宿主绿（pi 受 REQ-11 阻塞，见下） | 产品缺陷，退回 |
| `s2` 重启后 arming | dsh 绿；openclaw 未 arm（已知项） | 产品缺陷，退回 |
| `s4` 双地址 / `s5` pause·unbind / `neg` | 按 GATE.md | 产品缺陷，退回 |
| `s3` 入站投递 | **在 pin=v1.0.11 时必然 RED**（REQ-13 未发布） | **不是你的 bug**，不要改夹具 |

**已知产品阻塞**：pi 在 0.5.1 上 `typebox` 打包冲突（REQ-11），L2 pi 无法通过 —— 交付时请注明"pi 待 REQ-11"。

**结果口径**：`PASS / RED（产品缺陷）/ GAP（夹具缺陷）/ SKIP_LEGACY（pysdk，不计判定）`；
`RED>0 或 GAP>0` 退出码非 0。每次运行把 `PASS/RED/GAP` 行落到 `evidence/l2/<时间>-<host>/results.txt`，
`run.json` 记 host / SDK 版本 / gateway pin —— 交付时请把这个目录一并给出，不要只贴一行"通过"。

**L3 不在开发职责内**：L3 只能由验证方在发布件上执行（dsh × 4 平台，口径见 `tests/l3/GATE.md`），
开发不得运行 L3，也不得以 L3 结果作为交付依据。

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

**诉求**：宿主 adapter 启动/建会话时打印"该 session 是否拿到 mail toolset（15 件）"，并把 session tools 列表落到日志；
不要把"会话创建成功"当成"工具可用"。
**原因**：S4/S5（第二地址绑定、pause/unbind）曾连续两轮无 tool 帧，无法从日志区分"工具面没注入"与"模型没调用"。
**判据**：日志中能直接看到 `mail tools: 15` 或 `mail tools: 0`。

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

## REQ-11（P1，tssdk·打包冲突）`typebox` 被列入 dependencies，pi 1.5.2 直接拒绝加载插件

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
**回归**：同流程在 0.5.0 的 pi cell 是绿的；0.5.1 把 `typebox` 放进 dependencies 后 pi 端失效。

**修法**：把 `typebox` 移到 `peerDependencies: { "typebox": "*" }`，并加 `peerDependenciesMeta.typebox.optional = true`；
若确实需要运行期依赖，则改为宿主注入。

**验证方已实测该修法并回滚（重要）**：移到 peer 后 —— **pi** 插件正常加载，`S1 7/7 + S2 2/2 + NEG 2/2 = 11 PASS`；
**dsh** 立刻回归（`Cannot find package 'typebox'`，dsh 不提供该宿主模块）。⇒ 单纯移动声明**不可发布**。可选修法（请选一）：
1. **打包内置**：构建时把 `typebox` 内联进 `dist`（当前 `tsc -b`，需加 bundler 或内联步骤）；
2. **去掉运行期依赖**：`typebox-params.js` 直接产出 JSON Schema（不 import typebox）；
3. **双入口**：`pi` 专用入口声明 peer，`dsh/openclaw` 入口保留 dependencies。

**判据**：pi L2（S1/S2/NEG）与 dsh L2（S1/S2/S4/S5/NEG）同时保持绿。

---

## REQ-12（P1，tssdk 架构收敛）SDK 仍保留 CLI 入口面（register-cli / cli-op-entry）

**背景（Owner 裁定）**：SSE-V2 的 SDK **对 CLI 无依赖**，宿主只通过"插件/扩展"方式安装；
`aimail install` 这类 CLI 安装入口已不存在，不得再作为验收路径。

**现状（0.5.1 读码）**
```
src/dsh/register-cli.ts        （CLI-spawnable 绑定入口，注释指向 CLI 平台注册表）
src/pi/register-cli.ts
src/openclaw/register-cli.ts
src/core/cli-op-entry.ts       （`node <register-cli.js> --op <op> --args '<json>'`，契约 v1.0 §4.1(2)）
src/core/sdk-ops.ts:749        opAssemble/opUpdate/opTeardown 仍由 register-cli 调用
package.json                   bin: null（未发布 CLI 二进制，这一点已符合）
```

**影响**：门禁的安装路径已改为宿主插件安装（`dsh plugin add` / `pi install` / `openclaw plugins install`），
不再调用任何 SDK CLI；但产品里仍留 CLI op 入口，任何人按旧文档仍会走 `aimail install`，形成"两套安装语义"。

**修法（建议）**
1. 删除三个 `register-cli.ts` 与 `core/cli-op-entry.ts`，并把 `sdk-ops.ts` 的 op* 收敛为内部 API（仅宿主插件调用）；
2. README/契约文档同步：安装只有"宿主插件机制"一种；
3. 如短期必须保留，至少在包内标注 legacy 并在文档中标注"不受支持、L2/L3 不覆盖"。

**判据**
- `grep -r "aimail install" docs/ README*` 无命中；
- 包内无 CLI 入口（bin 为空、无 register-cli 导出）；
- L2 三宿主 + L3 dsh 在**纯插件安装**下全绿。

---

## 附二：SDK 门禁（L0/L1）与 CLI 依赖 —— 本轮查验结论

**已清（验证方，机械依赖）**
- `tests/cli/stage-resources.sh` → `tests/sdk/stage-resources.sh`：它是 tssdk 包资源 staging 的辅助脚本，
  只被 SDK L0 调用，放在 `tests/cli/` 下造成"SDK 门禁依赖 CLI 域"的误读。调用点已同步，
  `tests/sdk/l0-gate-tests.sh` 全文再无 `tests/cli/` 路径、无 CLI 二进制调用。

**待裁定（会影响门禁认证范围，需 Owner 一句话确认）**
`tests/sdk/` 下 44 个 pytest 文件中，31 个直接 `import aimail`（pysdk），另有一批以宿主为主题的
pysdk 用例（`test_deerflow_*`、`test_hermes_patch_roundtrip`、`test_sdk_cli_boundary`、
`test_bridge_route_side` —— 其中 `test_bridge_route_side.py:19,80-92` 断言的就是
`AIMAIL_BIN → ~/.aimail/bin/aimail → PATH` 的 CLI 解析链）。这些用例锁的是 pysdk/CLI 行为，
与 SSE-V2 无关。**我不擅自把它们从判定里摘掉**（摘掉=改变门禁认证范围），
请裁定：是否把它们统一标为 `SKIP_LEGACY`（打印条数、不计绿、`AIMAIL_LEGACY=1` 才执行）。
裁定后我按同一口径改 `l0-gate-tests.sh` 并在 `GATE.md` 记录。
