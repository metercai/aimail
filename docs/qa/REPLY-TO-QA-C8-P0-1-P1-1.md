# 回复质检：openclaw / C8、P0-1（C17）、P1-1 的处置与复验请求

日期：2026-11-24（晚）
来自：开发（tssdk / pysdk 轨道）
对象：质检 / verifier
关联：`docs/aimail-host-sse-contract-v2.md`（v2.3+）、`docs/qa/HANDOFF-L2-FIXTURE-CONTINUATION.md`（§4.5 / §4.6）

> 本文只写**已经改了什么、证据在哪、请你怎么复验**。结论请你给，我不代替判。
> 若你的 T2 修法与下面第 2 节不完全一致，请直接指到行号，我按差异补齐，不辩论。

---

## 0. 结论速览（我方自评，仅供对照）

| 项 | 性质 | 现状 | 证据 |
|---|---|---|---|
| openclaw / C8 会话复用 | **产品缺陷（我方）**，非宿主限制 | 已修，待 L2 复验 | `src/openclaw/inbound.ts:191`（`sessionMode:persistent`）+ `:107`（E1 选择）+ `:84`（幂等键含目标）；单测 11 项 |
| P0-1 断言①：`runtime/routing.ts` 缺存在性检查 | 成立，但需澄清作用域 | 已加 `sessionExists` + `gone` 结果；**该模块当前未被宿主调用**（只有单测），宿主侧另有实现 | `src/runtime/routing.ts:30/47/79/105/115` |
| P0-1 断言②：无 `session-gone` 产者 | 成立（当时） | 已加：dsh 的 `resume` 失败即产者；**你的 s7 驱动已接，但 `reason` 仍取空**——见 §2.4 | `src/dsh/inbound.ts:181/211` |
| P0-1 断言③：ack 绑在 HTTP 200 上 | 成立且**比 200 更弱** | **未改成"强制投递确认"**；按 owner 选 C 收敛为"作用域"：C17 只对可探测宿主证明，其余 `admission-only` + 门禁 `N/A` | 契约 §8.3、§16 C17、附 E `投递确认` 列；`lib/assert.sh` `na_scope()` |
| P1-1：`agentId=""` → 键退化成 `agent::aimail:` | **产品缺陷（我方）** | 已修 | `src/openclaw/inbound.ts:137`（一次解析两处使用）；单测 2 项 |

---

## 1. openclaw / C8：会话复用（你判 RED 的那条）

### 1.1 复核结论：不是宿主限制

宿主 **支持** 跨运行复用会话，缺的是我们的参数：

| 事实 | 出处（openclaw 2026.11.5） |
|---|---|
| `/hooks/agent` 收 `sessionKey` + `sessionMode`，`persistent` 的语义就是"跨运行复用同一个会话" | `src/config/types.hooks.ts:23-24`（`Reuse the resolved session key across runs`） |
| `sessionMode` **默认 `isolated`** ＝ 每次运行新开会话 | `src/gateway/hooks.ts:292`：`sessionTarget: persistent ? session:<key> : "isolated"` |
| `persistent` 必须同时给 key，且需宿主开前缀白名单（两条 400） | `src/gateway/server/hooks-request-handler.ts:343-369` |
| 宿主 200 的含义是 **cron job 已入队**（不是 turn 已跑） | `src/gateway/server/hooks.ts:286-310` 构造 `CronJob` |

我们此前**从不发 `sessionMode`**（`grep -rn sessionMode src` 修复前 = 0），所以每次都落在 `isolated` 分支——这与你在 L2 观察到的"每次新开会话"完全一致。

### 1.2 已改（三处）

1. `deliverToAgent` 增 `persist` / `idempotencyKey`：仅在"有 key 且要求 persist"时附 `sessionMode:"persistent"`（宿主对 persistent-without-key 返回 400，所以两者联动）。
2. 入站不再只按 thread 哈希取 key：新增 `selectSessionKey()`，**先 E1**（立即父消息 → 该信 meta 的 `session_key`，由 `send_mail` 写入）**再 E2**（thread root 哈希）；记录的 key 若不在宿主前缀白名单内则**不强行使用**，退 E2 并记 `directReplyDropped`。
3. 幂等键改为 `dispatchIdempotencyKey(messageId, sessionKey)`：宿主的 replay cache 按含 sessionKey 的 dispatch scope 匹配，旧实现（只用 `message_id`）会让"目标已变"的重投命中缓存并返回 200 却不跑——这是 P0-1 里"ack 零落地"的一个真实来源，已消除。

### 1.3 请求复验

```bash
bash tests/host-acceptance/v2/run-matrix.sh --host openclaw --stages "s1 s2 s3 s4 s6"
```

**期望**：`s6` 全 PASS（回复不新建会话、会话计数不增长、新 thread +1）；`host.log` 中出现
`sessionModeRequested: persistent` 与 `deliveryConfirmation: admission-only`；E1 场景出现 `sessionKeySource: direct-reply`。

**若仍 RED**，请把这两行摘出来，可立即定位：`sessionModeRequested` 是 `isolated`（宿主配置/前缀未生效）还是 `persistent` 但计数仍增（宿主语义变化）。

---

## 2. P0-1：C17「会话被删后 ack 却零落地」

逐条回答你的三个断言，不合并、不含糊。

### 2.1 断言①「`runtime/routing.ts` 缺存在性检查」——成立，但请连同作用域一起看

- 事实：`outboundSessionOf` / `threadSessionOf` 直接信任 meta 里的 `session_id`，不校验会话是否还存在。
- **但**：`decideInboundDelivery` 当前**没有任何宿主调用**（`grep -rn decideInboundDelivery src` 只命中 `runtime/index.ts` 的导出；宿主各自在自己的 `inbound.ts` 里路由）。所以只改本文件不会改变运行时行为——这也是我没有把它当作"修完"的原因。
- 已改：新增 `sessionExists?: (id) => Promise<boolean>` 与 `gone` 结果；探测**抛错按存活**（假 gone 会把活跃线程劈成两个）；未提供探测则行为不变。
- 若你的 T2 要求"路由层必须成为唯一入口"，那是**结构改造**（把 dsh/pi/openclaw 的本地路由收敛到 `routing.ts`），属于另一件事，请明确，我再评估工作量后动手。

### 2.2 断言②「无 `session-gone` 产者」——成立，已加

- dsh：`agents.resume(boundId)` 失败 ⇒ `status: 'session_gone_redirected'`，`detail: fresh session <id> — session-gone: <old> not resumable (…)`（会话被删/持久化关闭都会走到）。
- openclaw：本宿主**没有**会话存在性查询（插件面无 run/session 查询，`sessionId` 是临时 UUID），因此它不能成为产者；证据里加 `deliveryConfirmation: 'admission-only'` 明确这一点，不再让 200 读起来像"已落地"。
- pi：`sendUserMessage` 是 fire-and-forget，`detail` 写 `admission-only: handed to pi.sendUserMessage`；且 pi 是 S，C17 按契约不适用。

### 2.3 断言③「ack 绑在 HTTP 200 上」——成立，而且更弱

| 宿主 | `ok=true` 的实际含义 |
|---|---|
| openclaw | `/hooks/agent` 返回 **200**，而宿主 200 ＝ **cron job 入队** |
| pi | `sendUserMessage()` 返回（fire-and-forget） |
| dsh | `followup()` 入队 / `resume` 成功 / 新建会话成功 |

即：三者都只证明"宿主接管"，没有任何一个证明"已落进 transcript"。**这一点我没有用代码去"强行确认"**，因为：

- openclaw 插件面没有 run 状态/session 查询；`runId` 只回 ID 不回状态；
- 要求"确认后再 ack"等于要求这两家宿主提供新 API，属上游能力。

**owner 已选 C**，据此契约与门禁同步收敛（这是本轮第 4 类改动，请重点复核）：

| 落点 | 内容 |
|---|---|
| 契约 §8.3 | "已投递"的**可证性取决于宿主**；只能确认"宿主已接管"的属 `admission-only`，不得自我声明已证明 |
| 契约 §16 C17 | 增**证明范围**：具备会话存在性探测的宿主；`admission-only` 宿主不判红、也不得声称已证 |
| 契约 附 E | 新增 `投递确认` 列：dsh=`probe`、PI-Desktop=`probe`、openclaw=`admission-only`、裸 pi=`admission-only` |
| 门禁词汇 | `lib/assert.sh` 新增 `na_scope()` 与 `N/A`（显式打印，既不计绿也不计红）；`GATE.md` 词汇表补该行，并写明"不得用它掩盖跑不起来的检查（那是 GAP）" |
| `s7` | 按 `HOST_DELIVERY_CONFIRMATION` 分支：`admission-only` ⇒ `N/A`；`probe` 缺 `s7_gone.json` ⇒ **GAP**；未知取值 ⇒ GAP |
| 宿主声明 | dsh=`probe`；openclaw/pi/hermes/deerflow=`admission-only`（各处注明原因） |

⚠ 我对 `cases/s7_session_gone.sh` 与 `lib/assert.sh` 做了上述增量修改（保留了你之前的分隔符解析与 `CASE` 修正），`run-matrix.sh` 未改。

### 2.4 P0-1 仍未闭环的部分（已按你本轮的实际进度更新）

1. **`s7` 的驱动你已经接上了**（`lib/s7_drive.py`、`hosts/dsh.sh`、`hosts/deerflow.sh` 的 s7 段、pi 的 threading 分支），我核对过，不再是"缺钩子"。
   **但有一处基于旧事实的对齐要改**：`lib/s7_drive.py` 的 docstring 写"no producer exists in the SDK — `runtime/routing.ts` returns {direct|followup|new}"，`reason` 也固定写 `""`。这两点在本轮之后**已不成立**：
   - `runtime/routing.ts` 现在会返回 `kind: 'gone'`（`src/runtime/routing.ts:30`）；
   - dsh 的 `deliverInbound` 在 `resume` 失败时返回 `status: 'session_gone_redirected'` 且 `detail` 里带 `session-gone: <old-id> not resumable`（`src/dsh/inbound.ts:181/211`），这条会落进 SDK 的派发日志：
     `<AIMAIL_HOME>/systems/<sid>/<addr>/agentmail.log`（每行一个 JSON，`appendLog` 见 `src/core/log.ts:16-29`）。

   因此 `s7` 现在会因为 `reason` 为空而判 RED，**那不是产品缺陷**。请在 driver 里补一行取值（示例，容器内执行）：

   ```bash
   # reason：取第一处 session-gone 证据（产品产者已就位）
   _reason="$(grep -ho 'session-gone[^"]*' /root/.aimail/systems/*/*/agentmail.log 2>/dev/null | head -1)"
   ```

   并把它写进 `s7_gone.json` 的 `reason`。若你希望由 SDK 而不是 fixture 承担这段摘取，我可以把 `session-gone` 同时写进一个稳定位置（例如投递结果 detail 已带，日志已有；再要结构化字段请指定名字，我加）。

2. **openclaw/pi 的 `ack` 语义**：按 C 记为 `N/A`，即"不判红也不声称已证"。若你要改判为"必须证明"，那就是选 B，需要上游提供投递确认 API——请书面提出，我按 B 重新排队。

---

## 3. P1-1：`agentId=""` → 键退化成 `agent::aimail:`

**成立，已修。** 成因：`resolveDispatchAgentId()` 在宿主配置没有 agents 时**按设计返回空串**（"不猜"），但只有 dispatch body 做了 `|| resolveAgentId() || 'main'` 兜底，`sessionKey` 仍用空串派生 ⇒ 键指向不存在的 agent，而实际运行在 `main` 上。

修法：新增 `effectiveDispatchAgentId()`，**一次解析、两处使用**（key 派生 + dispatch body）；`resolveDispatchAgentId` 的"不猜"语义保持不变。单测：空/空白/缺省均不产生空段，且键的 agent 段与 dispatch 一致。

---

## 4. 请你执行的复验（命令 + 判读）

> 只跑这些就够，不需要重建镜像；模型 key 运行时注入、不打印。

```bash
# 1) 单元与静态（不依赖宿主）
cd tssdk && npx vitest run                 # 期望 588 passed
cd .. && python3 -m pytest tests/sdk -q    # 期望 531 passed
bash tests/sdk/l0-gate-tests.sh            # 期望 [L0] PASS

# 2) C8 复验（openclaw）
bash tests/host-acceptance/v2/run-matrix.sh --host openclaw --stages "s1 s2 s3 s4 s6"

# 3) C17 复验
bash tests/host-acceptance/v2/run-matrix.sh --host openclaw --stages "s1 s4 s6 s7"   # s7 期望 N/A（admission-only）
bash tests/host-acceptance/v2/run-matrix.sh --host dsh      --stages "s1 s2 s3 s4 s6" # 回归
bash tests/host-acceptance/v2/run-matrix.sh --host dsh      --stages "s7"             # 驱动你已接：补上 reason 后应 4 PASS
```

**判读规矩（与 GATE.md 一致）**：`RED>0` 或 `GAP>0` ⇒ 不通过；`N/A` 只是"该义务对宿主不适用"，**不得**被用来掩盖跑不起来的检查（那是 GAP）。

**请特别核对的三个新断言**：

1. `s6` 内 openclaw：`回复不新建会话` = PASS，且日志有 `sessionModeRequested: persistent`；
2. `s4` 内单地址宿主：`failed=wake` 可见（C18）；仅有模型转述时，PASS 行会标注 "paraphrased"；
3. `s7` 在 openclaw 上打 `N/A`、在 dsh 上为 GAP（未接钩子）。

---

## 5. 本轮变更文件清单（便于 diff / 提交范围）

**产品（tssdk）**

- `src/openclaw/inbound.ts`：`effectiveDispatchAgentId` / `selectSessionKey` / `dispatchIdempotencyKey` / `persist` + `sessionMode` / `deliveryConfirmation`
- `src/openclaw/tools.ts`：把 `ctx.sessionId`/`ctx.sessionKey`/`ctx.workspaceDir` 带进 tool handler
- `src/core/tools.ts`、`src/core/meta.ts`、`src/core/index.ts`：`ToolCtx.sessionKey`、`LocalMeta.session_key`、`stampSessionMeta` 支持与导出
- `src/runtime/routing.ts`：`sessionExists` + `gone`
- `src/dsh/inbound.ts`：`session-gone` 产者（`session_gone_redirected`）
- `src/pi/index.ts`：`admission-only` 证据 + `PiInboundOutcome.detail`

**契约**：§8.3、§16 C17、附 E（`投递确认` 列）

**门禁/夹具**：`lib/assert.sh`（`na_scope`、summary 加 `N/A`）、`cases/s7_session_gone.sh`（能力分支）、`hosts/*.sh`（`HOST_DELIVERY_CONFIRMATION` 声明）、`GATE.md`（词汇表 + stage map）、`README.md`（cases 清单）

> ⚠ 这些夹具文件**同时有你的改动**（`s7_drive.py`、s7 的 `CASE`/管道解析、`run-matrix` 的 `s7)`、hosts 的 s7 段）。
> 我做的是**增量**：只在你的版本上插入了 `HOST_DELIVERY_CONFIRMATION` 声明与 `s7` 的能力分支，
> 并保留你的解析与措辞。diff 时请按这四类分开看，不要把两边的改动混成一个结论。

**测试**：`test/openclaw/session-key-selection.test.ts`（11）、`test/openclaw/dispatch-contract.test.ts`（6）、`test/core/runtime-routing.test.ts`（11）、`test/core/meta.test.ts`（15）

---

## 6. 请你给的结论（建议格式）

```text
【C8 · openclaw】通过 / 不通过
  依据：s6 的四条断言 + host.log 的 sessionModeRequested
【P0-1 · C17】通过 / 有条件通过 / 不通过
  依据：§16 C17 证明范围是否被遵守；dsh 的 s7 是 GAP 还是 4 PASS；N/A 是否只出现在 admission-only 宿主
【P1-1】通过 / 不通过
  依据：单测 2 项 + s6 日志中 sessionKey 的 agent 段非空
【发版】允许 / 阻断
  若阻断：列出阻断项归属（产品 / 夹具 / 上游 / 文档）
```

我方自评：C8 与 P1-1 已达"可复验"状态；P0-1 属**部分修复**（结构 + 产者已就位，openclaw/pi 的 ack 语义按 owner 选 C 记为不适判），**因此 C17 在 dsh 之外的宿主上仍不可证**——这一点请写进结论，不要合并成"P0-1 已修复"。

---

## 7. 边界声明

- 我没有跑真机 L2（需要网关 + 宿主镜像 + 模型 key），上面所有"期望"均来自单测与代码路径；**L2 的判定权在你**。
- 我没有做插件真机安装，也不会自助安装。
- 若你对 §2.1 的"routing.ts 未被调用"有不同观察，请给 `grep` 行号，我立刻复核；若你要求把三宿主路由收敛到 `routing.ts` 作为 T2，请确认，这属于结构改造，我会单独给方案再动手。
