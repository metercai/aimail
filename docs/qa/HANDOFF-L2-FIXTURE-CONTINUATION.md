# 交接（L2 夹具续做 + 发版前置条件）：契约 v2.3 之后的落地清单

日期：2026-11-24
交接方：开发（PI-Desktop / tssdk+pysdk 轨道）
接收方：质检 / verifier
范围：`docs/aimail-host-sse-contract-v2.md`（v2.3）+ `tests/host-acceptance/v2/`（L2 夹具）+ 两处产品字段

> **本文用途**：本轮按 owner 口径把契约的 §9（入站映射）与 §11（单/多地址判定）重写了，并同步改了部分夹具。
> 剩下两条义务（C16/C17）缺宿主钩子，只能由质检继续。本文给出：现在真实状态、续做步骤（可直接执行）、
> 发版前置条件（硬门禁 vs 可豁免）、以及本会话踩过的坑。

---

## 0. 一句话结论

```text
契约 v2.3 已落地（§9 三条映射边 / §11 三层判定），产品补了 failed=wake 字段，夹具改了 s1/s4/s6 并新增 s7。
L0        PASS（tssdk vitest 568、pytest tests/sdk 531、契约单一来源 PASS、docs 一致性 PASS）
L2 现状   s1/s4/s6 断言已对齐 v2.3，但**尚未在所有宿主重跑**（这是质检第一件事）
C17       s7 阶段就绪，**全部宿主 GAP**（没有 host_s7_session_gone）
C16       无夹具（需要跨锚点场景），**未证明**
```

---

## 1. 本轮改了什么（按"产品 / 契约 / 夹具"三块）

### 1.1 产品（2 个文件 + 2 条断言）

| 文件 | 改动 |
|---|---|
| `tssdk/packages/agent-aimail/src/core/address-code.ts` | 单地址拒绝新增 `failed: 'wake'`（C18：拒绝必须指明"第一不满足层"） |
| `pysdk/aimail_tools.py` | 同上，`"failed": "wake"` |
| `tssdk/packages/agent-aimail/test/core/address-single-host.test.ts` | 断言 `res.failed === 'wake'` |
| `tests/sdk/test_v2_binding_and_identity.py` | 断言 `out["failed"] == "wake"` |

为什么是 `wake` 而不是别的：单地址宿主**有**锚点（如 cwd / profile），缺的是"把消息送进指定既有会话"
的能力；把这一点写进结构化结果，用户/运维才能区分"这台机器永远只能一个地址"和"没有容器可绑"。

### 1.2 契约（`docs/aimail-host-sse-contract-v2.md`，v2.1 → v2.3）

| 章节 | 现在的规则 |
|---|---|
| §9 | 三条映射边：**E1 直属（硬，锚点内）> E2 thread（软，可重挂）> E3 分配（偏好新会话）**；未命中只向下一级；ack 收敛成一句 |
| §9.2 | I1 直属回复必须回原会话（权限申请场景）；I2 禁止跨锚点，且由"键含锚点 + Gateway"结构性保证 |
| §9.3 | 降级表：`anchor-moved` / **`session-gone`（新建会话 + thread 重挂，仍需 ack）** / `thread-corrupt` / `space-fallback` |
| §11.1–11.3 | 三层判定：L1 锚点、L2 隔离、**L3 唤起（硬性）**、L3 创建（**偏好**）；无创建仍是 M，只让 E3 落到当前会话 |
| §11.5 | `import` 是"创建"这一偏好项的实现方式，不参与 M/S 判定 |
| §17 | 宿主实现义务（中立，不点名产品） |
| 附 E / 附 F | 参考宿主实例 / 参考实现状态（**正文不再出现具体产品名**） |

新增验收项：**C16 锚点隔离 / C17 降级 / C18 判定可解释**（§16 表）。

### 1.3 夹具（`tests/host-acceptance/v2/`）

| 文件 | 改动 | 影响 |
|---|---|---|
| `cases/s1_activation.sh` | 补 §7.1 的 MUST 字段 `email`/`domain`；`expires_at` 是 SHOULD ⇒ 只打 `NOTE` 不判红 | 若某宿主 binding 缺 `domain` → **RED**（这是产品缺陷，不是夹具问题） |
| `cases/s4_multiaddr.sh` | 证据分三级：`failed=wake\|L3` ⇒ PASS(C1+C18)；仅模型转述 ⇒ PASS 但注明"C18 层不可见"；无拒绝证据 ⇒ RED | 单地址宿主必须能看到拒绝；静默双绑定仍 RED |
| `cases/s6_threading.sh` | 头注释改 E1/E2/E3 口径；末段由"space=cwd"改为"三个会话都在 binding 的**锚点**内" | 断言本身未放松 |
| `cases/s7_session_gone.sh` | **新增阶段**：读 `out/s7_gone.json`，判 ack / 落到新会话 / 原因含 `session-gone` / thread root 未丢；无文件 ⇒ GAP | 目前全宿主 GAP（缺钩子） |
| `run-matrix.sh` | 接 `s7)` 分支（默认 stages 仍是 `s1 s2 s3`，s7 须显式请求） | 不影响现有默认跑法 |
| `GATE.md` | stage map 增 s6/s7 与 s4/s6 新口径；已知项改为 `S7-HOOK-1/2` | — |
| `README.md` | cases 清单更新 | — |
| `hosts/{deerflow,hermes,pi}.sh` | 契约引用改为层名（`§11.2` 判定 / `L3-wake`） | 仅注释 |

---

## 2. 现在真实状态（质检的起点）

| 阶段 | dsh | pi | openclaw | hermes | deerflow |
|---|---|---|---|---|---|
| s1（+domain 校验） | 待重跑 | 待重跑 | 待重跑 | 待重跑 | 待重跑 |
| s2 / s3 / s5 / neg | 待重跑 | 待重跑 | 待重跑 | 待重跑 | 待重跑 |
| s4（C1 + C18） | 待重跑 | 待重跑（应出 `failed=wake`） | 待重跑 | 待重跑（应出 `failed=wake`） | 待重跑 |
| s6（C8 / §9） | 钩子已有 | **无钩子 ⇒ GAP** | 钩子已有 | 钩子已有 | 钩子已有 |
| s7（C17） | **GAP** | GAP | GAP | GAP | GAP |

已确认：`host_s6_threading` 在 deerflow / dsh / hermes / openclaw 四个适配器里都有；`pi.sh` 里**没有** s6 钩子
（`grep -c s6 hosts/pi.sh` = 0），所以 pi 的 s6 必然 GAP。s7 的钩子**五个宿主都没有**。

---

## 3. 请质检继续做（按优先级，含命令与判读）

### P0 — 接一个 `host_s7_session_gone`（把 C17 从 GAP 变绿）

这是唯一"契约已定、夹具已就绪、只差宿主适配"的一项。步骤：

1. 在某个宿主的适配器里实现：先让 s6 的三封信跑通（复用 s6 的地址与 thread），然后
   **删除第 1 封信落地的会话**（宿主自己的 API/文件），再发一封 `In-Reply-To` 指向第 1 封的回信；
2. 把结果写成 `/out/s7_gone.json`：

```json
{ "acked": true, "deleted": "<被删会话 key>", "used": "<实际落地的会话 key>",
  "reason": "session-gone: thread re-attached", "root_ok": true }
```

3. 跑：

```bash
bash tests/host-acceptance/v2/run-matrix.sh --host dsh --stages "s1 s4 s6 s7"
```

**判读**：`s7` 四条全 PASS = C17 成立；`acked=false` 或 `used==deleted` = **RED**（产品没有降级，会把
owner 的审批回信永久挂住）；没有 `s7_gone.json` = GAP（钩子没写，不是产品问题）。

> 建议优先 `pi`：它的会话是文件，删一个会话的代价最小、最好验证；其次是 dsh（`s6_drive.py` 已经能读
> session-root，改造成本能低）。

### P1 — 决定 C16（跨锚点隔离）怎么办

目前**没有夹具**能证明"E1 目标在别的锚点 ⇒ 必须放弃 E1 并在当前锚点重排"。两条路，需 owner 书面选一条：

- **建夹具**：需要宿主能把同一地址在锚点 A 绑定、同时在锚点 B 有一个会话，然后发一封直属回复；
  期望：信**不**落到 B，落到 A 的会话并记 `anchor-moved`。
- **书面豁免**：明确"C16 由单元测试（`src/runtime/routing.ts` / `src/desktop/inbox.ts`）覆盖，L2 暂不证明"，
  并写进 `GATE.md` 已知项（现有 `S7-HOOK-2` 已占位）。

不允许的做法：把它当"已经证明"或悄悄从 C 表删掉。

### P2 — 收紧 C18 的证据来源

`s4` 现在接受两种证据：结构化 `failed=wake`（强）与模型转述（弱，会打上"（paraphrased）"字样）。
若质检认为弱证据不够，可改成：**在宿主侧 dump 工具返回的 JSON**（而不是读模型日志），那样 `failed` 字段
一定能看到。改法与 s6 一致：宿主写 `out/s4_refusal.json`，case 读它。

### P3 — 全宿主回归（本轮改动后的第一次跑）

前置：`run-matrix.sh` 要求运行时注入模型 key（`: "${LLM_API_KEY:?...}"`），**只注入、不打印、不落盘**；
本机取自 `~/.hermes/profiles/verifier/.env` 的 `XIAOMI_API_KEY`（`export LLM_API_KEY=…`）。

```bash
# 三次独立旅程，别合并（见 GATE.md "Journey scoping"）
bash tests/host-acceptance/v2/run-matrix.sh --host dsh       --stages "s1 s2 s3 s4 s6"
bash tests/host-acceptance/v2/run-matrix.sh --host openclaw  --stages "s1 s2 s3 s4 s6"
bash tests/host-acceptance/v2/run-matrix.sh --host pi        --stages "s1 s2 s3 s4"
bash tests/host-acceptance/v2/run-matrix.sh --host dsh       --stages "s5"
# pysdk 两宿主（gated,不是 skipped）
bash tests/host-acceptance/v2/run-matrix.sh --host hermes   --stages "s1 s2 s3 s4 neg" --sdk py
bash tests/host-acceptance/v2/run-matrix.sh --host deerflow --stages "s1 s2 s3 s4 neg" --sdk py
```

**必须满足**：`RED=0` 且 `GAP=0`（`SKIP_LEGACY=0`）。
**重点看三处新断言**：

1. `s1` 的 `domain`：缺了就是 RED —— 说明绑定没按 §7.1 写；
2. `s4` 单地址宿主：要有 `failed=wake`（或至少 typed refusal，会标注 paraphrased）；
3. `s6`：三个会话必须在 binding 的锚点内（`workspace` 字段为空 ⇒ GAP）。

---

## 4. 发版前置条件

### 4.1 硬门禁（缺一不发）

| # | 条件 | 判据 |
|---|---|---|
| R1 | L0 全绿 | `bash tests/sdk/l0-gate-tests.sh` → `[L0] PASS`（含 tssdk build/vitest、pytest、契约单一来源、docs 一致性、zero-bridge、file-ownership） |
| R2 | L2 全宿主 `RED=0 GAP=0` | §3 的 P3 命令；`SKIP_LEGACY=0` |
| R3 | C18 在**每个单地址宿主**上可见 | `s4` 出现 `failed=wake`（或质检认可的等价结构化证据） |
| R4 | C17 至少在一个宿主上证明 | `s7` 四条 PASS（本轮的 P0） |
| R5 | 契约与代码一致 | 契约 v2.3 的 §9/§11 断言与实现一致；`tests/sdk/test_v2_contract_surface.py` 绿 |
| R6 | 无宿主 patch、无 CLI 安装路径 | `docs/aimail-host-sse-contract-v2.md` 与 `docs/v2-release-notes.md` 中不含 `agent-install.sh` / `Follow the step-by-step`（`tests/sdk/test_v2_contract_surface.py` 已守）；安装只走宿主插件机制 |

### 4.2 可豁免项（需 owner 书面签字，写进 `GATE.md` 已知项）

| 项 | 豁免条件 |
|---|---|
| C16 跨锚点夹具 | owner 选择"由单测覆盖 + L2 不证明"，并签字；`S7-HOOK-2` 保留 |
| 其余宿主的 `s7` 钩子 | 仅需 1 个宿主证明 C17，其它宿主可后续补 |
| pysdk 的 L2 覆盖 | hermes/deerflow 已 gated；若某阶段出 GAP，须记录原因与归属 |

### 4.3 打包与发布（职责边界）

| 事项 | 归属 | 说明 |
|---|---|---|
| PI-Desktop 插件 `.piplug` 打包与流水线 | **质检** | 打 `tssdk/packages/aimail-desktop-plugin/dist/`；zip 必须 **store-only**、`manifest.json` 在根、≤2000 文件/≤50 MiB/无符号链接、`dist/package.json` 的 `{"type":"commonjs"}` 不能丢。**插件 id 已改为 `aimail.pi-desktop`（`aimail.desktop` 被宿主判非法，commit `ee3446e`），当前版本 `0.2.1`**；细节见 `tssdk/packages/aimail-desktop-plugin/README.md` §"Packaging belongs to QA" |
| npm 发布（`agent-aimail`） | owner | 走 `scripts/publish-npm.sh`，版本在 `package.json` |
| PyPI 发布（`agent_aimail` 0.3.0） | owner | 仓库无 pysdk 发布脚本，属 owner 动作 |
| 真机安装验证（插件导入宿主） | owner/质检执行，**开发不做自助安装** | 唯一没有实测的一环 |
| 上游诉求：宿主开放 `session.create` | owner 决定 | 不阻塞发版（`import` 已自洽，§11.5） |

### 4.4 发版顺序建议

1. 提交本轮工作树改动。当前未跟踪的 `dist-piplug/`（质检已产出的 `aimail.pi-desktop-0.2.1.piplug` + sha256）、
   `evidence/`、`tests/host-acceptance/v2/lib/racp_probe.mjs` 三个**不要**混进产品提交（前两个是工件/证据，
   第三个属探测工具，按质检自己的节奏收）；
2. 打 tag → 质检出 `.piplug` 工件 + sha256；
3. L0 → L2（§3 的 P3）→ 全绿后 owner 触发 npm/PyPI；
4. 发布后跑 L3（`tests/l3/`，由 verifier 执行，4 platform × dsh）。

---

## 4.5 openclaw / C8：**不批准豁免**（独立调研结论）

质检提出"openclaw 每次 hook 新开会话 ⇒ C8 RED ⇒ 建议 owner 豁免"。核对宿主源码后结论相反：
**宿主有能力复用会话，缺的是我们的适配器参数**。豁免会掩盖一个可修缺陷；而且"EXEMPTED 但仍算 M"
在契约上不成立（§11.2 把"唤起"列为硬性，做不到就是 S，不是有条件的 M）。

| 事实 | 证据 |
|---|---|
| `/hooks/agent` 接受 `sessionKey` + `sessionMode`，语义即"跨运行复用同一个会话" | openclaw `src/config/types.hooks.ts:23-24`（`Reuse the resolved session key across runs`） |
| `sessionMode` **默认 `isolated`** ＝ 每次运行新开会话 | `src/gateway/hooks.ts:292`（`sessionTarget: persistent ? session:<key> : "isolated"`） |
| `persistent` 必须同时给 key，且要求宿主配置前缀白名单（两条 400） | `src/gateway/server/hooks-request-handler.ts:343-369` |
| 插件工具上下文能拿到当前会话身份 | `src/plugins/tool-types.ts:28-30`（`sessionKey` / `sessionId`） |
| 我们的适配器此前**从不发 `sessionMode`** | 本轮修复前 `grep -rn sessionMode tssdk/packages/agent-aimail/src` = 0 |

**本轮修复**（小改动、可回退）：

1. `deliverToAgent` 仅在"已给 `sessionKey` 且调用方要求 `persist`"时附 `sessionMode: "persistent"`；
2. 调用侧在宿主 opt-in（`hooks.allowRequestSessionKey` + 前缀命中）时同时发 `sessionKey + persist`；
3. 附 `idempotencyKey = 邮件 message_id`，避免 pull 重试触发第二次运行；
4. 派发日志新增 `sessionModeRequested: persistent|isolated`，作为 C8 的书面证据；
5. 单测 `test/openclaw/dispatch-contract.test.ts` 扩到 6 项（含"只给 key 不带 persist ⇒ 不发 sessionMode"
   与"无 opt-in ⇒ 两者都不发"）。

**期望**：用新构建重跑 `--host openclaw --stages "s1 s2 s3 s4 s6"`，`s6` 的"回复不新建会话"应为 PASS，
且日志里 `sessionModeRequested: persistent`。若仍 RED：看该字段是 `isolated`（配置未生效）还是
`persistent` 但会话仍变（宿主语义变化），两种都有明确下一步。

**E1（直属回原会话）也已补齐**（同一轮，不是"下一迭代"）：`send_mail` 现在把**发信会话**的可路由句柄
写进该信的本地 meta（`session_key`；OpenClaw 的 `sessionId` 是临时 UUID，不能用来寻址，所以两者都记），
入站投递先走 E1 再退 E2：

| 环节 | 改动 |
|---|---|
| 发送 | `ToolCtx` 增 `sessionKey`；openclaw 工具装配时把 `ctx.sessionId` / `ctx.sessionKey` / `ctx.workspaceDir` 带进 handler；`send_mail` 落 meta（`meta.ts` 增 `session_key`，`stampSessionMeta` 支持） |
| 入站 | 新纯函数 `selectSessionKey()`：立即父消息 → 读父信 meta 的 `session_key` → **E1**；否则 thread root 哈希 → **E2**；记录的 key 若不在宿主前缀白名单内 → 不强行使用，退 E2 并记 `directReplyDropped` |
| 证据 | 派发日志新增 `sessionKeySource: direct-reply｜thread`；投递成功后把实际落地的 key 记到这封信的 meta，嵌套回复继续走 E1 |
| 单测 | `test/openclaw/session-key-selection.test.ts` 6 项（含"只认 In-Reply-To / References 末项"、"前缀不允许时降级并记录"）；`test/core/meta.test.ts` 补 `session_key` 往返 |

因此现在两条都成立：
- hook 投递进来的会话发信 ⇒ 回复回同一会话（C8）；
- **用户在聊天里**让 agent 发信 ⇒ 回复回到**那个用户会话**（E1，以前是错的）。

**给质检的处理建议**：C8 **不要记 EXEMPTED**；先重跑 s6 取新证据，失败再按上面两条分支定位。

## 4.6 质检 P0-1 / P1-1 的核查与修复（2026-11-24 晚）

### 4.6.1 P0-1「C17：会话被删后 ack 却零落地，三宿主同签名」

逐条核对（**两条成立、一条需要澄清**）：

| 质检断言 | 核查结果 | 证据 |
|---|---|---|
| `runtime/routing.ts` 缺存在性检查 | **成立**（但需澄清：该模块当前**没被任何宿主调用**，只有单测用；真实路由在各 host 适配器里） | `src/runtime/routing.ts` `outboundSessionOf`/`threadSessionOf` 直接信 meta 里的 `session_id`；`grep -rn decideInboundDelivery src` 只命中 `runtime/index.ts` 的导出 |
| 无 `session-gone` 产者 | **成立** | 修复前 `grep -rn "session-gone" tssdk/packages/agent-aimail/src` = 0 |
| ack 绑在 HTTP 200 上 | **成立（比 200 更弱）** | openclaw：`deliverInbound` 以 `hooks/agent` 的 **200** 判成功，而宿主 200 的含义是"**cron job 已入队**"（`gateway/server/hooks.ts:286-310` 构造 `CronJob`），不是"turn 已跑"；pi：`sendUserMessage()` 是 fire-and-forget 后立即 `ok:true`；dsh：`followup()` 入队即 `ok:true` |

**本轮修复**（都可单测、可回退）：

1. `runtime/routing.ts` 增加 `sessionExists?: (id) => Promise<boolean>` 输入与 **`gone`** 结果
   （`{ previousSessionId, parent?, root, workspace }`）：E1/E2 命中的记录若探测为不存在 ⇒ 返回 `gone`，
   调用方据此"新建会话 + thread 重挂 + 再 ack"，而不是投进虚空。
   - 探测**抛错**按"存活"处理（假 gone 会把活跃线程劈成两个）；
   - 未提供探测 ⇒ 行为保持不变（宿主不具备该能力时不得假设）。
2. `dsh`：`resume` 失败即视为 **session-gone 产者**，走新建会话并在 outcome 里写明
   `status: session_gone_redirected` + `detail: fresh session … — session-gone: <id> not resumable`。
3. `openclaw`：**发现并修掉一个同族的真 bug**——`idempotencyKey` 原为 `message_id`，而 openclaw 的
   replay cache 按 dispatch scope（含 sessionKey）匹配；目标会话变化后重投会命中缓存返回 200 却**不跑**，
   正是"ack 零落地"。现 `dispatchIdempotencyKey(messageId, sessionKey)` 把目标并入键。
4. 单测：`test/core/runtime-routing.test.ts` +5（gone ×2、存活、探测抛错、无探测），
   `test/openclaw/session-key-selection.test.ts` +3（幂等键随目标变化）。

**仍未闭环的一环（需 owner/上游决定，我没有偷偷改口径）**：`ack` 的语义到底以什么为准。

- 契约 §8.3 写的是"只有**已投递进 Session** 才 ack"；
- 但 openclaw 的 200 = **入队**、pi 的返回 = **交给宿主 API**，二者都拿不到"已落进 transcript"的确认；
- 宿主没有提供"投递确认"的 API（插件面没有 session 列表/run 状态查询；`sessionId` 是临时 UUID，`/hooks`
  只回 `runId` 不暴露状态）。

**owner 已选 C**（2026-11-24）：只对**可探测**的宿主证明 C17，其余在附 E 标 `admission-only`，门禁记 `N/A`。
落地内容：

| 层 | 落地 |
|---|---|
| 契约 | §8.3 增一句"可证性取决于宿主"；§16 C17 增**证明范围**；附 E 增 `投递确认` 列（`probe` / `admission-only`） |
| 门禁词汇 | `lib/assert.sh` 新增 `na_scope()` 与 `N/A`：**显式打印、既不计绿也不计红**；`GATE.md` 词汇表补该行，并写明"不得用它掩盖跑不起来的检查（那是 GAP）" |
| s7 | 按 `HOST_DELIVERY_CONFIRMATION` 分支：`admission-only` ⇒ `N/A`；`probe` 且无 `s7_gone.json` ⇒ **GAP**；未知取值 ⇒ GAP |
| 宿主声明 | dsh=`probe`；openclaw / pi / hermes / deerflow=`admission-only`（各自注明原因） |
| 产品证据 | openclaw 派发日志加 `deliveryConfirmation: 'admission-only'`；pi 的 outcome 加 `detail: 'admission-only: handed to pi.sendUserMessage'`；dsh 用 `session_gone_redirected` 表明探测与重定向 |

因此：**C17 在 dsh 上可证（仍需接 `host_s7_session_gone` 钩子）**；openclaw/pi 不再被判红，也不会被误记为已证。

### 4.6.2 P1-1「openclaw dispatch agentId="" → 键退化成 `agent::aimail:`」

**成立，已修**。成因：`resolveDispatchAgentId()` 在宿主配置里没有 agents 时按设计返回空串（"不猜"），
但**只有 dispatch body** 做了 `|| resolveAgentId() || 'main'` 兜底，`sessionKey` 仍用空串派生 ⇒ 键指向
一个不存在的 agent（`agent::aimail:<hash>`），而实际运行在 `main` 上——键与运行目标不一致。

修法：新增 `effectiveDispatchAgentId()`，**一次解析、两处使用**（key 派生与 dispatch body）；
`resolveDispatchAgentId` 的"不猜"语义保持不变。单测 2 项（空/空白/缺省均不产生空段，且键的 agent 段
与 dispatch 一致）。

## 4.7 pi 的 C8/C17：**同意记 `N/A`**（已独立核实，2026-11-24）

质检的判断成立，依据取自 **L2 实际镜像里的 pinned pi 1.5.2**（`docker run aimail-host-pi:latest`，
`@earendil-works/pi-coding-agent@1.5.2`）：

| 事实 | 出处 |
|---|---|
| 会话控制方法（`newSession` / `fork` / `navigateTree` / `switchSession`）只挂在 `ExtensionCommandContext` | `dist/core/extensions/types.d.ts:303-336` |
| 该上下文自带注释："session control methods **only safe in user-initiated commands**" | 同上（接口上方 docstring） |
| 后台投递（pull 定时器）拿到的是 `ExtensionAPI`，只有 `sendUserMessage` / `sendMessage` / `setSessionName` / 事件订阅，**没有**分配或改挂会话的方法 | 同文件 `ExtensionAPI` 段 |
| 会话身份由 CLI 调用决定（`--session-id` / `--continue` / `--session <path,id>`） | pi CLI 参数；插件侧无入口 |

因此：新 thread 无法在 pi 上"新开/改挂一个会话"，C8 的**分配半条**与 C17（重挂）在 pi 上没有参照物。

**签字口径（已按此落契约与门禁）**：

1. 契约 **§16 C8** 增证明范围：分配半条只对"入站路径可分配/改挂会话"的宿主成立；会话由 CLI 决定、
   插件只持有当前会话的宿主（S）不判该半条，但"邮件进入当前会话并被 ack"仍按 **C9** 判。
2. **附 E** pi 行写明依据与"不适用"范围。
3. 门禁：`HOST_CAN_ALLOCATE_SESSION=0`（pi / hermes）时 `s6` 只判 ack + thread root，其余 4 条记 `N/A`；
   `HOST_DELIVERY_CONFIRMATION=admission-only` 时 `s7` 记 `N/A`（上一轮已做）。
4. `GATE.md` 已知项新增 `PI-C8-17-1`，把依据、仍判项、`N/A` 条数写明，避免下次被当成缺陷重报。

**没有一并豁免的东西**（请质检继续判）：三封信全部被 ack（C9）、thread root 稳定、第二地址被拒（C1）、
S 宿主"入站进当前会话"的实际落地。

## 5. 本会话踩过的坑（别重复踩）

| 坑 | 现象 | 处理 |
|---|---|---|
| `tssdk/.pnpm-store`（root 所有，500 MB）在仓库内 | zero-bridge 把 store 里的二进制当 bridge 引用，L0 直接红 | 已在 `check-zero-bridge.py` 的 `REF_SKIP_DIRS` 加 `.pnpm-store`/`.pnpm`；**建议 owner `sudo rm -rf tssdk/.pnpm-store`** |
| 契约文档里写契约字面量（`agentmail.json` / `.agentmail` / toolset 名） | 契约单一来源棘轮报"新位置" | 用 `<!-- contract-allowed: 原因 -->` 标注该行；代码里必须引用 `contract.ts` 常量而不是字面量 |  <!-- contract-allowed: this row names the literals on purpose (it documents the ratchet) -->
| 文档写行号引用（`contract-v2.md:519`） | 文档一改就悬挂 | 改为引用章节号（`§16 C8 / §9`）；本轮已清 |
| `.piplug` 用 `zip -r` | 宿主拒收（只接受 store-only） | 见 4.3 表 |
| 插件 `main.js` 落在 `type: module` 包里 | 宿主 `require` 拿到 ESM，`module.exports` 丢失 | `dist/package.json` 必须写 `{"type":"commonjs"}` |
| `s6.json` 用空格分隔读值 | 解析错位导致假 RED | 已有实现用 `replace(" ","_")`；新增 case 沿用 |

---

## 6. 不在本次范围（明确边界）

- 不改 `contract/aimail-contract.json`（push 时代的**命名**契约，已登记为相邻遗留，等 V2 彻底去 push 再裁）；
- 不做真机安装、不自助装插件；
- 不动 `tests/l3/*`（归 verifier）；
- 不改产品行为去"迁就"夹具：本轮唯一的代码改动是补 `failed` 字段（契约 C18 要求）。

---

## 7. 阅读顺序

1. `docs/aimail-host-sse-contract-v2.md` §9（三条边）、§11（三层判定）、§16（C1–C18）
2. `tests/host-acceptance/v2/GATE.md`（stage map + 已知项 `S7-HOOK-1/2`）
3. `tests/host-acceptance/v2/cases/{s4_multiaddr,s6_threading,s7_session_gone}.sh`
4. `tssdk/packages/aimail-desktop-plugin/README.md`（打包约束与已知边界）
