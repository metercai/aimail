# SDK L2 / L3 测试框架重构——发展方案与实施计划书（评审稿）

- 作者：验证方（质量把关）
- 日期：2026-11-23
- 状态：**评审稿（未执行）**
- 基线 tag：`qa-l2l3-baseline-2026-11-23`（三仓同名，已推 origin）
  | 仓 | commit |
  |---|---|
  | metercai/aimail | `14ce537` |
  | metercai/aimail-gateway | `d2af10a`（= tag v1.0.11） |
  | metercai/aimail-advanced | `e270a48` |

---

## 0. 目标、范围与边界

**目标**：把跨三仓的 SDK 验收测试收敛成**一套可复跑、可归因、按 SSE-V2 契约判定的 L2 + L3 夹具**，
dsh / pi / openclaw（同构 tssdk）三宿主全绿；旧的 push/CLI/webhook 夹具清理干净。

**范围**
- 在范围内：SDK L2（宿主级验收）、L3（发布件跨平台矩阵）、两者依赖的环境/夹具/CI 接线。
- 明确不在范围：**CLI L2**（`tests/cli/`、`aimail-advanced/tests/cli/` 保持原样）、advanced 域测试（`tests/advanced/`、`tests/gateway/`）、pysdk。

**"标红不做结论"的对象**
- `hermes`（pysdk 0.2.9，legacy push，未迁移 SSE-V2）
- `deerflow`（dsh 底座 + pysdk 接线，未迁移）
→ 仍可执行，但输出 `SKIP_LEGACY`，**不参与** V2 判定，也不允许用 skip 冒充绿。

**旧契约要素的清除目标（硬要求）**
1. 任何夹具不得通过 CLI 完成地址绑定（`register-cli`、`aimail` 二进制、`aimail_gateway.json` 写入）。
2. 任何夹具不得引用 `agent-self-setup.md` 作为地址绑定入口；绑定唯一路径 = 契约 §3.2
   `aimail_address({action:'bind', code, email, gateway_url})`（宿主内工具调用）。
3. 不得使用宿主外预写 binding / 伪造 webhook 的方式"造绿"。

---

## 1. 现状盘点与取舍判定

### 1.1 metercai/aimail（主仓）

| 路径 | 现用途 | 判定 | 处置 |
|---|---|---|---|
| `tests/contract/` | L0 契约单真源棘轮（字面量/零桥/文件归属/docs） | **有用，保留** | 不动 |
| `tests/sdk/`（45 文件 + `l0-gate-tests.sh`） | L0 pytest/vitest、V2 surface、错误信封、lifecycle | **有用，保留** | 不动（仅补 B3/B4 的单测） |
| `tests/sdk/release/` | tarball/wheel 发布件抽查 | 有用 | 归入 L3 前置步骤 |
| `tests/agent-env/`（pins/docker/llm-config/set-llm-key） | 环境唯一真源 | **有用，保留并扩展** | 新增 `GATEWAY_VERSION`/`GATEWAY_SHA256` |
| `tests/host-acceptance/harness.mjs` | **fake host context** 直调 SDK | 误用为 L2 | **降级**为 L1.5「SDK 直调负例」，不再计入 L2 |
| `tests/host-acceptance/run-l2-local-gateway.sh`、`run-l2-two-gateways.sh`、`two-gateway-harness.mjs` | 网关侧隔离/聚合断言 | 部分有用 | 抽出网关侧 case，迁入 `v2/lib` |
| `tests/host-acceptance/run-l2-fixture.sh` | 旧 push 夹具 | **无用** | 删除 |
| `tests/host-acceptance/run-m6-a34-pi.sh` | 一次性 pi 手测 | **无用** | 删除 |
| `tests/host-acceptance/run-pi-l2-activation.sh`、`run-openclaw-l2-activation.sh`、`run-real-host-arming.sh`、`run-l2-dsh-real-host.sh` | 真实宿主碎片（B3/B4 期间产物） | 有用但零散 | **合并**为三个宿主适配器 |
| `tests/host-acceptance/run-v2-release-gate.sh` | 现有 V2 总门禁（含 fake-host L2） | 结构可用、口径要改 | 重写为 `v2/run-matrix.sh` |
| `tests/host-acceptance/HANDOFF-SSE-*.md` | 过程交接 | 历史价值 | 归档 `tests/host-acceptance/archive/` |
| `tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md` | 当前 QA 结论真源 | **有用** | 保留（B1–B5 判定链） |
| `tests/l3/run-l3.sh` | L3 六段式，含 hermes/dsh/pi 分支 + 取 `releases/latest` | 逻辑有用、**未钉版** | 拆 `run-v2.sh` / `run-legacy.sh`，钉版 + evidence |
| `tests/l3/dsh-v2.sh` | dsh V2 旅程 [0]-[7] | **有用** | 保留为旅程实现，接口对齐新 lib |
| `tests/dsh-acceptance/`（11 脚本 + harness + config） | SSE 早期单宿主门禁、claim 脚本、rotation/loop/lifecycle | 部分有用 | SSE 交付断言并入 dsh 适配器；rotation/base 作为网关 case 保留；CLI/push 相关删除 |
| `tests/cli/` | CLI L2 | 不在本次范围 | 不动 |
| `tests/qa-2026-11-15/` | 历史 QA 报告 | 历史 | 归档 `docs/qa/archive/` |

### 1.2 metercai/aimail-advanced

| 路径 | 现用途 | 判定 | 处置 |
|---|---|---|---|
| `tests/SDK/docker-regression/hosts/`（38 文件） | 验证方 V1 期临时夹具（stub LLM、agent-turn-*、run-host-regression-*、dsh_activate、ws_mux、oc_*） | **已过时（V1 契约/CLI/stub）** | **整体删除**；仅 `FIVE-DIM-MATRIX.md` 归档留痕 |
| `tests/SDK/sdk-e2e/cases`（s1–s10） | SDK 直调链路 | 与 L1.5 重叠 | 评估后并入 `tests/sdk/`（不重复保留两份） |
| `tests/SDK/agent-level/`、`session-e2e/` | agent 级 API/session | advanced 域 | 保留 |
| `tests/test-fixtures/`（eml 样本） | 邮件样本 | **有用** | 保留，L2 复用 |
| `tests/advanced/`、`tests/gateway/`、`tests/cli/` | advanced/CLI 域 | 不在本次范围 | 不动 |

### 1.3 metercai/aimail-gateway

| 路径 | 判定 | 处置 |
|---|---|---|
| `tests/gate-error-mapping.sh` | 有用（网关错误面） | 保留 |
| 其余网关行为 | 由 L2 的网关侧 case 覆盖 | — |

---

## 2. 目标测试分层（重构后）

```
L0  契约单真源 + pytest + vitest            （现有，保留）
L1  单元/组件                                （现有，保留）
L1.5 SDK 直调负例（无宿主）                   （由 harness.mjs 降级而来）
L2  SDK 宿主验收 3 宿主 × 5 段 + 负例          ← 本次重建主体
L3  发布件跨平台矩阵（V2 gate 5 cell）         ← 本次重建
legacy  hermes×4 / deerflow                   （执行但标红，不计结论）
```

**归属原则**
- L2/L3 的**单一真源在 aimail 仓**（`tests/host-acceptance/v2/`、`tests/l3/`）；
  advanced 仓只保留 advanced 域测试；不得再出现两套宿主夹具。
- 环境与版本唯一真源：`tests/agent-env/pins.env`（新增 gateway 钉版）。

---

## 3. L2 重建设计（主体）

### 3.1 目录结构

```
tests/host-acceptance/v2/
  VERSION                      # 夹具版本（语义化，变更需评审）
  lib/fixture.sh               # 起停 gateway+sink、/apply 公开取码、白名单、入站投递、清理
  lib/assert.sh                # 断言原语 + 结果口径（PASS/RED/GAP/SKIP_LEGACY）
  lib/evidence.sh              # 证据收集（见 3.5）
  hosts/dsh.sh pi.sh openclaw.sh   # 宿主适配器（同构接口）
  cases/s1_activation.sh
  cases/s2_arming.sh
  cases/s3_delivery.sh
  cases/s4_multiaddr.sh
  cases/s5_lifecycle.sh
  cases/negatives.sh
  run-matrix.sh                # 3×5 + 负例；--host/--case 可单跑
```

### 3.2 宿主适配器接口（同构的关键）

每个 `hosts/<host>.sh` 只实现 6 个函数，其余全部共享：

```
host_boot            启动干净宿主进程（镜像内，pinned 版本），返回就绪
host_install_plugin  安装当前被测 tssdk 构建（L2=开发构建；L3=已发布版由 agent 自装）
host_prompt "<text>" 以"用户 turn"身份投递一条 prompt，等待 turn 结束
host_restart         重启宿主（binding 生效必需）
host_log_grep <pat>  在宿主日志里取证据
host_stop            停止 + 清理
```

> 差异化只允许出现在适配器内；case 脚本禁止出现宿主名分支。

### 3.3 五段旅程与断言（对应契约条款）

| 段 | 内容 | 断言 |
|---|---|---|
| **S1 激活** | `/apply` 公开流程（邮件验证→申请）取 `addr-` 码；宿主内 `aimail_address(bind, code, email, gateway_url)` | binding 存在且 `0600`；`system_id`=GatewaySystemId、`agent_system_id`=`asid_*`、`workspace_path`=宿主 cwd、**无** `webhook_url`；目录 `systems/<GatewaySystemId>/<addr>/` |
| **S2 拉起** | 重启宿主 | 日志 `polling enabled` + `pull scope … email=<addr>`；无 `no agent-scope binding` |
| **S3 投递** | 白名单后注入带唯一 token 的入站邮件 | turn 文本含 token；`pending_deliveries` 该行 acked，且 **ack 时间 > turn 完成时间**；未进 session 时必须**不** ack（C9） |
| **S4 多地址** | openclaw/dsh：两地址各自绑定 + 各自投递；pi：第二地址 | openclaw/dsh 两流不串（各自只收到自己 token）；pi 第二地址被拒（C1） |
| **S5 生命周期** | pause → 投递 → resume → 投递 → unbind → 投递 → 再 bind | pause 期不 ack 且 pending 保留；resume 后投递成功；unbind 后不再拉取；再 bind 成功且幂等 |
| **负例** | 无效码 / 复用码 / 缺 gateway_url / 错误 email | 410 / GONE / `NO_GATEWAY`（不重试）/ 400；且**均不写 binding、不 arm** |

### 3.4 结果口径与退出码

```
PASS          断言全过
RED           产品缺陷（附最小证据行）
GAP           夹具/环境缺陷（不计产品红，必须修夹具）
SKIP_LEGACY   hermes/deerflow（未迁移 pysdk），打印但不计结论
```
- 报告末行固定格式：`L2 host=dsh case=S3 → PASS`，
  汇总 `PASS=<n> RED=<n> GAP=<n> SKIP_LEGACY=<n>`
- 退出码：`RED>0 或 GAP>0` → 非 0；仅 `SKIP_LEGACY>0` → 0（但必须打印）
- **禁止 skip 冒充绿**：任何未执行用例必须落到 `SKIP_LEGACY` 或 `GAP`，并有原因串。

### 3.5 证据（每次 L2 落 `evidence/<ts>/`）

`{host, host_version, sdk_version(build sha), binding(脱敏), turn_token, turn_at, ack_at, llm_ms, gateway{tag,sha256}, logs/<host>.tail}`
—— 便于"全绿"可审计，也便于红时一次定位（避免本轮反复复现）。

---

## 4. L3 重建设计

### 4.1 矩阵拆分（关键决策）

| Job | cell | 判定 |
|---|---|---|
| **V2 gate** | dsh×4（linux-amd64/arm64、macos-arm64、windows-amd64）+ pi×1（linux-amd64） | 决定 V2 是否可发 |
| **legacy** | hermes×4 | 执行、标红、**不计结论**（pysdk 0.2.9 未迁移） |

> deerflow 目前无 L3 cell；如需覆盖，另立 legacy 项。

### 4.2 钉版（消除不可归因漂移）

- `pins.env` 新增：`GATEWAY_VERSION=v1.0.11`、`GATEWAY_SHA256=<四平台各自>`；
  `run-l3.sh` 不再取 `releases/latest`，改动后日志打印 `gateway tag + sha256` 并在下载后校验。
- npm 端仍按 L3 语义"装最新已发布"，但必须**记录 agent 实际安装到的版本**并写入 evidence。
- 每次 cell 记录：runner image（`$ImageOS/$ImageVersion`）、实际 host 版本（`dsh --version` 断言 == pins）、LLM 端点与 `llm_ms`。

### 4.3 cell 证据（`evidence.json` 上传为 artifact）

`{platform, host, host_version, npm_version, gateway{tag,sha256}, turn_token, turn_at, ack_at, llm_ms, binding_sha256}`

### 4.4 稳定性

- 每 cell 连续 3 次绿才计"稳定"；单 cell 重跑 ≤2 次且必须在报告中留痕。
- flake 一律进隔离清单（含负责人与截止日期），不得"重跑一次判 flake"。

---

## 5. 实施计划（阶段 / 产出 / 验收）

| 阶段 | 内容 | 产出 | 验收 | 人日 |
|---|---|---|---|---|
| **P0 冻结** | 执行 §1 清单、`git rm --dry-run` 预览、基线 tag 确认 | 删除清单（评审签字） | 清单无异议 | 0.5 |
| **P1 公共库** | `lib/fixture.sh`（gateway/sink/apply/whitelist/mail/cleanup）+ `assert.sh` + `evidence.sh` | 可在 dsh 单跑通 S1 | S1 单跑 PASS + 证据落盘 | 1.0 |
| **P2 宿主适配器** | dsh / pi / openclaw 三个（干镜像、pinned） | `hosts/*.sh` | 三宿主各自 boot→install→prompt→restart 通 | 1.5 |
| **P3 五段旅程** | S1–S5 + 负例（11 case）+ `run-matrix.sh` | `cases/*` + 矩阵报告 | dsh 11/11（pi/openclaw 按产品状态给 RED/GAP） | 1.5 |
| **P4 CI 接线** | host-images 构建 + L2 gate job；L3 拆 job | workflow diff | PR 上跑出矩阵报告 | 0.5 |
| **P5 L3 重写** | `run-v2.sh`（5 cell，钉版+evidence）、`run-legacy.sh` | 两份 runner | 单 cell 本地通过；矩阵可 dispatch | 1.0 |
| **P6 清理与文档** | 执行删除、归档、写 `docs/qa/l2l3-refactor.md`、打 `l2l3-v2.0` tag | 干净目录 + 文档 | 目录只剩有效资产 | 0.5 |
| 合计 | | | | **6.5** |

**里程碑**
- M1 = P1+P2 完成 → 能对任一宿主跑 S1（激活闭环）
- M2 = P3 完成 → L2 全矩阵可跑（产品缺陷以 RED 呈现）
- M3 = P5 完成 → L3 V2 gate 可 dispatch
- M4 = P6 完成 → 旧夹具清零、文档与 tag 就位

---

## 6. 与产品代码的边界（本次不改产品码）

夹具改造期间若发现产品缺陷 → 记 `RED` + 出**修订建议书**交开发，验证方不动 `src/`。
已知需要开发的项（随方案提交）：

| # | 项 | 证据 | 期望动作 |
|---|---|---|---|
| R1 | OpenClaw：入站邮件被读到但未交付 agent session（无 turn，按 C9 不 ack） | 420s 窗口，`[qa-gw] GET /contacts?...ocfin@...` 后无 turn | 在 inbound→session 处加结果日志并修复 |
| R2 | DSH：真实宿主会话工具面为空（`turn/start`、`step/start` 有，无 tool 帧） | 0.5.1 前实测；需在 0.5.1 复测 | 给会话工具面最小复现口径或修复 |
| R3 | `listen.test.ts` 端口断言偶发（`expected [p,p+2]`） | 文档 §5#3 | 放宽到契约允许集合 |
| R4 | L3 hermes legacy HMAC 被 gateway 拒（`require X-Webhook-Signature-V2`） | 文档 §5#1 | 属 pysdk 迁移项；未迁移前标红 |
| R5 | 测试侧钉版（gateway 版本进 pins） | 文档 §5、本方案 §4.2 | 由验证方实现，需开发同意 CI 变更 |

---

## 7. 风险与对策

| 风险 | 对策 |
|---|---|
| R1/R2 未修 → pi/openclaw S3 必红 | 先跑"红基线"，以 RED 呈现真实状态；不阻塞夹具交付 |
| LLM 抖动导致 turn 无响应 | 断言只比对 token 与 `llm_ms>0`；超时分级（120s/240s/300s 按宿主） |
| 镜像构建慢 / 依赖漂移 | pnpm 预热 + 镜像 digest 固定；L2 用开发构建但记录 sha |
| 删除误伤 | P0 出 `git rm --dry-run` 清单，逐项签字；删除与迁移分两个 commit |
| 与开发并行改动冲突 | 夹具全部新增在 `v2/` 目录；旧文件只删除不原地改；分阶段 PR |

---

## 8. 需评审拍板的 4 件事

1. **删除清单**（§1 表内标"删除"的项）是否同意——特别是 advanced 仓 `docker-regression/hosts` 38 个文件整体删除。
2. **L3 矩阵拆分**：hermes×4 移入 legacy job（标红不判）是否同意；pi 是否需补 arm64/mac 覆盖（当前仅 linux-amd64）。
3. **钉版**：`GATEWAY_VERSION/SHA256` 进 `pins.env`、`run-l3.sh` 不再取 latest（会改变 L3 语义：从"跟最新"变成"跟钉版"）。
4. **基线 tag 命名**：本次用 `qa-l2l3-baseline-2026-11-23`（已推三仓）；重构完成打 `l2l3-v2.0`。

---

## 9. 结论

- 现状：L2 的"宿主验收"是碎片化 + 混入 fake host；L3 未钉版且矩阵掺入未迁移的 legacy cell，
  因此"L2 全绿 / L3 全绿"目前都**不成立且不可审计**。
- 本方案：以 3 宿主 × 5 段同构矩阵为 L2 唯一口径，以钉版 + evidence 的 5 cell 为 L3 V2 gate，
  legacy 明确标红；旧夹具一次性清除。
- 评审通过后按 P0→P6 执行，里程碑 M1–M4 各自可验收。
