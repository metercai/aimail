# 产品修订建议书（tssdk + gateway）

- 日期：2026-11-23（第二次修订：REQ-1 结案改写、新增 REQ-13、补齐 L2 自检包与门禁口径）
- 范围：**`tssdk/packages/agent-aimail`（dsh / pi / openclaw 三宿主）与 `aimail-gateway`**
- 明确不含：pysdk 的 SSE-V2 改造（hermes / deerflow 在 L2 中计为 `SKIP_LEGACY`，不参与判定）
- 最终目标：**tssdk 三宿主通过 L2（`tests/host-acceptance/v2/`）**。L3 不在本文件范围，也不在开发职责内。
- 执行纪律：验证方不改 `src/`；每项修完按"判据"在框架内自测，再交回复验（新 SHA，全量重跑）

---

## 0. 本轮状态总表

| 条目 | 状态 | 谁动 |
|---|---|---|
| REQ-1 入站被转发 relay | **结案：判定错误**。产品路径不存在；症状真因是我方夹具端口 off-by-one，已由验证方修复 | 已关闭 |
| REQ-13 入站台账行被删（pull 模式） | **已修并已发版**：`0c46b38`（逻辑）→ `v1.0.12`；四资产已核 sha256，`pins.env` 已 pin v1.0.12 | 已完成 |
| REQ-11 pi `typebox` 打包冲突 | 仍有效，未修 | 开发 |
| REQ-12 SDK 仍留 CLI 入口面 | 仍有效，未修 | 开发 |
| REQ-2…REQ-10 | 仍有效（P1/P2/P3 不等） | 开发 |
| L2 自检包 | 已就绪；gateway v1.0.12 起 `s3` 也纳入默认自检 | 验证方（已完成） |
| SDK L1 | **不存在（放空）**，不新建；SSE-V2 覆盖由 L0 契约 + L2 宿主实测承担 | 已定 |
| SDK L0 判定范围 | SSE-V2/契约/文档/tssdk 构建为判定项；pysdk+CLI 套件 `SKIP_LEGACY`（清单 `tests/sdk/legacy-suites.txt`） | 验证方（已完成） |

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

**发版记录（已完成）**
- `0c46b38` 曾因新测试块未 rustfmt（`flows.rs:563`/`:572`）导致 `cargo fmt --all --check` rc=1；
  已做**仅格式**修正并证明语义等价：`tr -d '[:space:]' | sha256sum` 修正前后同为
  `0b76d967…b052fe77`（TOKEN_STREAM_IDENTICAL），提交 `166b6bf`，`fmt/clippy/test` 全绿（415 passed）。
- 已发 **`v1.0.12`**；release run 全绿（gate / windows check / 4 build / release），四资产验证：

| 资产 | 字节 | sha256 |
|---|---|---|
| `aimail-gateway-linux-amd64` | 17,786,552 | `3ba969132067c03785fad26f29ed53382fe6b10a5b72b848529a18ec8881e628` |
| `aimail-gateway-linux-arm64` | 14,714,832 | `e33f108c29fdc7acbfb3bc3526bd64eb6a0b84dc62d686d0807de3cde6244618` |
| `aimail-gateway-macos-arm64` | 14,470,656 | `b3007f87552856c0201a442179f0e9e9c1fb489bc61f14aa246fa481539db853` |
| `aimail-gateway-windows-amd64.exe` | 19,561,984 | `f28935db095f937131207791d5dcbc0d483c07c355caafc0c1be1bc80b3538db` |

  `./aimail-gateway-linux-amd64 --version` → `aimail-gateway 1.0.12`；`tests/agent-env/pins.env`
  已更新为 `v1.0.12` + 上述四值（L2 日志会打印 `published gateway v1.0.12`，可据此核对）。

**发版后判据（L2 S3，三宿主各一份）**：
入站 → `emails`≥1 且 `status='delivered'` + `pending`≥1 → 宿主取走 → `pending=0`、台账行保留至 retention
→ relay spool 无该入站原信。

**发版后我方动作（已完成）**：`pins.env` 已 pin v1.0.12 + 四平台 sha256 → 重跑 L2。

---

## 自检入口（只有这几条，别找别的）

| 层 | 命令 | 判定范围 / 说明 |
|---|---|---|
| **L0** | `bash tests/sdk/l0-gate-tests.sh` | SSE-V2 / 契约 / 文档 / tssdk 构建 = 判定项；pysdk+CLI 套件按 `tests/sdk/legacy-suites.txt` 逐条打印 `SKIP_LEGACY`，**不计绿**（`AIMAIL_LEGACY=1` 才执行，仅供排查） |
| **L1** | **无（放空）** | 本仓不存在 L1 runner，**也不新建**。SSE-V2 的黑盒覆盖由 L0 契约闸 + L2 宿主实测承担 —— 不要找 L1，也不要为它准备任何东西 |
| **L2** | 见下 | 交付判据 |

### L2（交付判据）

前提：
```bash
export LLM_API_KEY=<xiaomi key>            # OpenAI 兼容路由
docker images | grep aimail-host-          # dsh / pi / openclaw 三个宿主镜像
# 网关不用自己起：pins.env 的已发布二进制自动下载并校验 sha256（当前 v1.0.12）
```

命令：
```bash
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2 s3 s4 s5 neg"
# 单宿主：--host dsh|pi|openclaw

# s3 与 s5 必须分两次跑（s5 的 pause/unbind 会拆掉绑定，runner 因此在含 s5 的那次旅程里
# 不做入站注入 —— 这是设计，不是夹具坏了）：
bash tests/host-acceptance/v2/run-matrix.sh --host dsh --stages "s1 s2 s3 s4 neg"   # 投递证据
bash tests/host-acceptance/v2/run-matrix.sh --host dsh --stages "s5"                 # 生命周期
```

预期（gateway v1.0.12 起）：

| 阶段 | 预期 | 说明 |
|---|---|---|
| `s1` 激活 → bind | dsh / openclaw 绿 | pi 待 REQ-11 |
| `s2` 重启后 arming | dsh 绿 | openclaw 未 arm 属已知项（REQ-3） |
| `s3` 入站投递 | dsh 绿 | `emails≥1(status=delivered)` + `pending≥1` → 宿主取走 → `pending=0`、台账行保留至 retention；relay spool 无该原信 |
| `s4` 双地址 / `s5` pause·unbind / `neg` | 按 `GATE.md` | pi 只允许单地址 |

交付时给：`evidence/l2/<时间>-<host>/`（`run.json` + `results.txt` + `host.log`），不要只贴一行"通过"。
口径：`PASS / RED（产品缺陷）/ GAP（夹具缺陷）/ SKIP_LEGACY（pysdk，不计判定）`；`RED>0 或 GAP>0` 退出码非 0。

`s3` 若在本地红，**先看 `run.json.gateway_version`**：不是 `v1.0.12` 就是 pin 没更新（表现为 `emails=0`，
即 REQ-13 未生效），不是你的改动。

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
**判据**：L2 证据里的 stats 与实际投递数一致。

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
3. 如短期必须保留，至少在包内标注 legacy 并在文档中标注"不受支持、L2 不覆盖"。

**判据**
- `grep -r "aimail install" docs/ README*` 无命中；
- 包内无 CLI 入口（bin 为空、无 register-cli 导出）；
- L2 三宿主在**纯插件安装**下全绿。

---

## 附二：SDK 门禁与 CLI 依赖（已实施）

- **L0 无 CLI 依赖**：`tests/cli/stage-resources.sh` → `tests/sdk/stage-resources.sh`（只被 L0 调用，
  内容是 tssdk 包资源 staging）；`tests/sdk/l0-gate-tests.sh` 全文无 `tests/cli/` 路径、不执行任何
  CLI 二进制、不解析任何 CLI 产物。
- **L0 判定范围 = SSE-V2**：pysdk/CLI 套件（37 个文件：31 个 `import aimail` + deerflow/hermes/
  bridge/hosts-layer/CLI-boundary 主题）统一列在 `tests/sdk/legacy-suites.txt`，逐条打印
  `SKIP_LEGACY`，不计绿；套件只能在两个集合间移动的唯一方式是改这份清单，范围不会悄悄漂移。
- **L1 = 空**：不新建、不占位。SSE-V2 的黑盒证据来自 L0 契约闸与 L2 宿主旅程。

## REQ-14（P1，advanced 文案 + 文档）激活邮件的安装提示把 npm 包名写错，agent 因此自举失败

- **现象（L3 实测，2026-11-23，linux-amd64/dsh，published 0.5.2 + gateway v1.0.13）**：
  干净环境的 agent 收到激活邮件正文后，9 步 / 185s 内查不到可安装目标并结束回合，未安装 SDK。
  它的原话：*"PyPI is reachable and `agent_aimail` exists there; npm has no such package."*
- **根因**：`src/advanced/api/email_notify.rs:149` 的正文写
  `install the `agent_aimail` plugin SDK first: from PyPI via pip for Python, or from npm for Node.js`。
  `agent_aimail`（下划线）在 PyPI 上**能解析**（PEP 503 归一化，`-`/`_`/`.` 等价，canonical 名就是
  `agent-aimail`），但 **npm 没有归一化**：`npm view agent_aimail` → 404，只有 `agent-aimail` 存在。
  dsh / pi / openclaw 都是 Node 宿主，正文却只给了 Python 侧拼写 ⇒ 字面 404。
- **实测事实**：
  ```
  PyPI  agent-aimail  → canonical agent-aimail 0.2.9
  PyPI  agent_aimail  → canonical agent-aimail 0.2.9   （同一工程，非两个包）
  npm   agent-aimail  → 0.5.2
  npm   agent_aimail  → 404
  仓库  pyproject name = "agent-aimail"；npm name = "agent-aimail"（两处本来就是连字符）
  ```
- **修法（不新增包、不改名、不改 import）**：正文统一用 `agent-aimail`，并按宿主给命令：
  ```
  dsh       → dsh plugin add agent-aimail
  pi        → pi install agent-aimail
  openclaw  → openclaw plugins install agent-aimail
  Python    → pip install agent-aimail      （PyPI 归一化，连字符同样可装）
  ```
  Python 的 **import 名仍是 `aimail`**（`import aimail`），与发行名无关，不要动。
- **不要**为了"统一"去 npm 发一个 `agent_aimail` 别名包：两套供应链、两个版本号、两份审计面，归一化已经足够。
- **判据**：L3 中 agent 在**第一轮**就定位到正确包并完成安装（transcript 出现对应宿主的安装命令）；
  邮件正文里的包名在 npm 与 PyPI 上都能直接命中。
- **同源待同步**：`docs/aimail-host-sse-contract-v2.md:599`、`docs/v2-qa-release-handoff.md:92`
  抄了同一句错误文案，改代码时一并改；`tests/sdk/test_v2_contract_surface.py:58` 目前断言源码里含
  `agent_aimail` 这个拼写，需要复核它锁的到底是 import 名还是发行名。

## REQ-15（P1，advanced + gateway 文案）激活邮件无法稳定驱动 agent 自举安装 SDK —— 附可直接替换的文案

- **现象（CI，干净 runner，2026-11-24，run 45286838741）**：
  ```
  dsh linux-amd64 : ✗ the agent never installed agent-aimail（激活邮件驱动下未发生安装）
  pi  macos-arm64 : ✗ the agent never installed agent-aimail
  pi  linux-amd64 : ✓ 成功      dsh macos-arm64 : ✓ 成功
  ```
  同一文案、同一旅程，不同格结果相反 ⇒ 不是平台能力问题，是**指令不足以稳定驱动**（模型行为有波动）。
- **现有文案（`advanced/src/advanced/api/email_notify.rs:149`，base gateway 有一份同源拷贝）**：
  > …install the `agent-aimail` plugin SDK first: from PyPI via pip for Python, or from npm for Node.js. Then retry `aimail_address` with the same parameters.
- **四个具体缺陷**（每条都有实测支撑）：
  1. **没给宿主对应的安装命令**。三个宿主各有自己的插件机制（`dsh plugin add` / `pi install` / `openclaw plugins install`），只给"from npm"等于让 agent 自己猜；CI 上它选择结束回合。
  2. **"the same parameters" 要求 agent 反推调用参数**。应直接给出 `action=bind, code=…, email=…, gateway_url=…`。
  3. **没说要重启宿主**。插件在会话中途装上不会生效（dsh/pi 都是"下一次启动才加载"），agent 装完不复启就会判定自己失败并停手。
  4. **没说明"你自己执行，不要交给用户"**。实测有 agent 回复"我在终端装好了，你继续"——把动作推给用户，自举中断。
- **建议文案（短版，可直接替换；4 行，不再解释背景）**：
  ```
  Activate and bind my AIMail mailbox address {address} with activation code {code}
  at gateway {gw} using the `aimail_address` tool.

  If `aimail_address` is missing, install the SDK yourself (do not ask me to run it),
  restart your host, then call `aimail_address` again with the same code:

      dsh: dsh plugin add agent-aimail | pi: pi install agent-aimail
      openclaw: openclaw plugins install agent-aimail | Python: pip install agent-aimail

  The package is `agent-aimail` (hyphen) on npm and PyPI.
  ```
- **为什么不是只写 `npm install agent-aimail`**：实测 dsh/pi 都不会因为工作目录里多了一个 npm 包而加载插件
  —— dsh 的插件要进 profile（`dsh plugin add`），pi 的要进扩展注册（`pi install`）。dsh 格真实发生的安装动作是
  `plugin_manager {"action":"install_bundle","target":"agent-aimail"}`，pi 格是它自己的安装通道；
  单写 `npm install` 会让 agent 装到一个不生效的目录，然后判定失败。宿主命令本身就是 npm 的封装，
  长度只多几个字符，但决定了装完能不能用。
- **判据（L3 可自动判）**：4 平台 × {dsh, pi, openclaw}，在**不可读本仓库文档**的干净环境里，
  `[3]` 首次尝试（含用户一句"你自己装"）内出现 agent 自己的安装动作，且 `[5]` bind、`[7]` persona/signature 成立。
- **同源约束**：这句话在 base gateway 与 advanced 各有一份；两处必须改同一字符串，并由一条契约检查锁住
  （避免再出现"一处改成 agent-aimail、另一处还是 agent_aimail"）。
