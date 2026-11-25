# 修复任务（汇总）— 2026-11-25

- 来源：verifier 复验（契约 v2.3 / L2 夹具 / s6-s7）+ 一轮全宿主 L2 回归
- 用途：**一次性交给开发**，按序号逐条修复；每条都带可复现命令与判据
- 优先级：P0 = 阻断发版；P1 = 应修（不阻断本次但会阻碍"契约全量达成"的对外表述）；P2 = 记录性
- 说明：C17 按 owner 裁定走 **T2 全合规**（T1 止血不算达成）

---

## P0-1 C17：会话被删后入站邮件被吞（S1 静默丢信）

**现象**（dsh，三次一致）
```
mail A（新 thread）→ 宿主会话 7cf927e4… ；删除该会话 ；回信（In-Reply-To: A）
→ acked=true ；新会话：无 ；被写入的会话文件：0
```
ack 之后网关不再重投 ⇒ owner 的审批回信永久消失。

**规范要求**（`docs/aimail-host-sse-contract-v2.md`）
- §9.3：「E1/E2 目标 Session 已不存在 ⇒ 新建 Session 并把 thread 指针重挂过去，投递成功后 ack｜`session-gone`」
- §9.3 末段：「重挂是**唯一允许修改 thread 指针**的时机；不要求原 Session 存在」
- §9.5：「Target 且投递成功 ⇒ ack；Refusal ⇒ 不 ack」
- §16 C17：「…重挂 thread 并投递成功（记 `session-gone`），**不得永久 pending**」

**根因**
1. `tssdk/packages/agent-aimail/src/runtime/routing.ts`：`direct`/`followup` 分支只查本地 meta 索引，
   **不校验目标 Session 是否仍存在** ⇒ 不会走"新建 + 重挂"；
2. 该文件无 `reason` 字段，全仓 `grep -rn session-gone src/` = **0** ⇒ 降级不可观测；
3. `core/address-code.ts:534-544`：非 `rejected` 即 ack，而"delivered"来自宿主 **HTTP 200** ⇒
   200 但未落地仍然 ack。

**修法（T2）**
1. `routing.ts` 注入 `sessionExists(sessionId)`；E1/E2 命中但不存在 ⇒ 改判 `new`，把
   `thread[anchor][root]` 重挂到新会话，返回 `reason: 'session-gone'`；
2. `sessionExists` 由宿主适配器提供，参考实现：
   `dsh`：`sessions/<ws>/<id>/` 存在 · `hermes`：`sessions.json` 有该键 ·
   `deerflow`：thread 表有该行 · `openclaw`：`openclaw sessions --json` 含该 id；
3. ack 与"落地"绑定：仅在目标会话确认写入后返回 Target；否则 `Refusal(no-target)` ⇒ **不 ack**；
4. 记账写进插件 dispatch 日志（`agentmail.log` 的 `reason` 字段）；
5. `pysdk` 同构（`pysdk/v2/` 路由 + 邮箱回退）；
6. 单测：
   - 索引里有、存储里没有 ⇒ `kind==='new'` 且 `reason==='session-gone'`；
   - 宿主拒绝写入 ⇒ 不 ack（不得进入 `delivered`）。

**验收（我会重跑）**
```
bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s6 s7"
→ S7 四条全 PASS：acked=true · used 为新会话 · reason 含 session-gone · thread root 未丢
```
退回件全文：`docs/qa/DEFECT-C17-SESSION-GONE.md`

---

## P1-1 openclaw：dispatch 的 agentId 为空，会话键退化

**现象**（插件自己的 dispatch 日志）
```json
{"event":"dispatch","status":"delivered","sessionKeyRequested":true,
 "sessionKey":"agent::aimail:17cab5d7ac6e1ce6fb323d5a","agentId":""}
```
宿主的键形状是 `agent:<agentId>:<surface>:<id>`，我们给的是 `agent::aimail:<hash>`（双冒号）。
**影响**：即便宿主按 caller key 去重（当前不会），空 agent 段也会把所有 agent 挤进同一命名空间，
`agent-system` 隔离形同虚设。

**修法**：`resolveDispatchAgentId()` 在拿不到绑定的 agent 时回退到宿主默认 agent（`main`），
并在日志里区分 `resolved_from=binding|default`。

**说明**：openclaw 的 C8 另一部分（同 thread 复用会话）经四组实验确认是**宿主能力缺口**
（同一 caller key 连发两次仍产生两个会话），不属本次修复范围；见 `tests/host-acceptance/v2/GATE.md`
的 s6 小节。

---

## P2-1 契约与实现的一处表述矛盾（文档）

`tssdk/.../src/openclaw/inbound.ts` 注释：
> "Omitted => host generates hook:<uuid>, each email is a clean new turn (**this integration does not need session reuse**)"

与 §16 C8 直接冲突。请改为："session reuse requires `hooks.allowRequestSessionKey` + 前缀白名单，
且当前宿主实现不按 caller key 复用会话（见 C8 例外记录）"。

---

## P2-2 版本策略（待 owner 决定，不在开发修复范围）

`agent-aimail` 的 `./desktop` 导出是本批新增但尚未发布；`.piplug` 已自带 bundle，故不发也能用。
若要让源码级 `import 'agent-aimail/desktop'` 成立，需发 `0.5.3`——**该决定与 P0-1 绑定**：
C17 未修完就发 npm，等于把"审批回信可能被吞"的路径推给所有宿主。

---

## 附 A：本轮 L2 回归结果（5 宿主，v2.3 夹具）

判据：每宿主 `RED=0 GAP=0 SKIP_LEGACY=0`。证据 `/tmp/l2-v23/*.log`、`/tmp/l2-fix/*.log`、`/tmp/oc-l2-try3.log`。

| 宿主 | 阶段 | PASS | RED | GAP | 结论 |
|---|---|---|---|---|---|
| ts / dsh | s1 s2 s3 s4 s6 neg | 26 | 0 | 0 | ✅ 绿 |
| ts / pi | s1 s2 s3 s4 neg | 18 | 0 | 0 | ✅ 绿（该组合不含 s6/s7） |
| py / hermes | s1 s2 s3 s4 s6 neg | 24 | 0 | 0 | ✅ 绿 |
| py / deerflow | s1 s2 s3 s4 s6 neg | 25 | 0 | 0 | ✅ 绿（首跑 1 RED 是夹具时序，已修） |
| ts / openclaw | s1 s2 s3 s4 s6 neg | 21 | 2 | 0 | ⚠️ RED 仅 C8 会话复用 ⇒ **宿主限制**，待 owner 豁免（见附 C） |

**产品侧本批无新增缺陷。**

## 附 B：C17（s7）跨宿主实测

| 宿主 | 结果 | 证据 |
|---|---|---|
| dsh | **RED（产品 P0-1）** | `acked=true, used='', delivered_anywhere=false` |
| hermes | **RED（产品，同签名）** | `acked=true, used='', reason=''` |
| deerflow | **RED（产品，同签名）** | 18 PASS / 3 RED |
| pi | **夹具未完成（open）** | 见附 D |
| openclaw | **N/A** | 无 delete API；会话身份由宿主分配 |

三宿主同签名 ⇒ 缺陷在 SDK 公共路由（`runtime/routing.ts`），与宿主无关。

## 附 C：openclaw 的 2 个 RED = 宿主限制（需 owner 书面豁免）

四组实验（含容器内直连 `/hooks/agent`）：
1. 插件日志 `sessionKeyRequested=true`，键 `agent::aimail:<hash>` —— 我们确实传了；
2. 宿主同一 caller key 连发两次 → 2 个会话（**不合并**）；
3. `badkey` → 400 `sessionKey must start with one of: agent:, hook:`（键本身被接受）；
4. 开启能力需白名单含 `hook:`，启动时报 `dangerous config flags`。

⇒ 宿主对每次 hook 运行新建会话，C8 的"同 thread 复用"在 openclaw 上**不可由 caller 实现**。
处置：框架仍报 RED；**owner 书面豁免后**在矩阵标 `EXEMPTED`（记入 `GATE.md` 已知项，附本文件）。未签字前保持 RED。

## 附 E：dsh 的 `session-gone` 产者在真实流程里不可达（v6 实测，2026-11-25）

**现象**（`/tmp/verify-dsh-s7-v6.log`，端口 18448）：

```
PASS  reply was acked
PASS  delivery used a new session (fced0c9a-… -> de8c58a9-…)
PASS  the thread root survived the re-attach
RED   downgrade reason '' does not name session-gone
探针：aimail_home=/root/.aimail exists=True
      systems/system-9f3a -> ['board','l2dsh_test.local']
      glob hits=1  pattern=/root/.aimail/systems/*/*/agentmail.json  <!-- contract-allowed: QA 文档引用 binding 文件名以定位证据 -->
      rewrote 0 config(s)          <- 配置文件存在，但里面没有 session_id
```

**推论（需开发确认）**：`dsh/inbound.ts:197` 的 `updateAgentConfig(..., { session_id })` 没有把
`session_id` 落进 `agentmail.json`。于是：  <!-- contract-allowed: QA 文档引用 binding 文件名以定位证据 -->

1. `cfg.session_id` 恒为空 ⇒ `agents.resume(boundId)` 分支**永不执行** ⇒ 新加的
   `session_gone_redirected` 产者**在真实流程里不可达**；
2. 同 thread 复用实际走 thread/meta 路径（s6 能过就是这个原因）；
3. 我先前"删目录 ⇒ resume 成功 ⇒ 写成空"的现象，是"没有 session_id 时"的另一种坏法。

**这与"改了不生效的地方"是同一类问题**（第一次是 `routing.ts` 未被调用）。请确认二者之一：

- (a) 为什么 `session_id` 没有写入（只在 followup 成功后写？被别的路径覆盖？）；
- (b) 或把 `session-gone` 挂到**实际生效的 thread/meta 路径**：thread 指向的会话不可用
  （文件消失 / 注册表陈旧）⇒ 新建 + 记录 `session-gone`。

在此之前，C17 在 dsh 上只能记"逻辑已由单测证明、真实流程不可达"，不得记作已修复。

---

## 附 D：pi 的 C8/C17 —— 夹具已跑通，结论是**契约适用性问题**，不是产品缺陷

**夹具现在能跑了**（第 7 次尝试，一次改对）：每封信前投递、随后驱动**一轮** `pi -p`（与 L3 同形）。
```
acked = [true, true, true]        ← 三封全部被宿主取走
session1 = 2026-11-25T04-38-43…   session2 = 2026-11-25T04-40-00…   session3 = 2026-11-25T04-45-37…
sessions 2 → 3 → 4                ← 每一轮都新建一个会话文件
```

**此前六次失败的真因（逐层排除后确定）**：pi **只在"跑一轮"的时候拉信**。
证据：网关 `emails` 表 4 条（探针 + 三封全存），但只有带 turn 的那封被 ack（`emails=4, acked=1`）；
L3 也是这么设计的（`journey-pi.sh:51-55`「one pi process per user turn」）。前几轮我假设"常驻 rpc 会话
会持续拉取"，这个假设是错的——命名管道死锁、settings 路径、流槽都只是这条错误假设下的次要症状。

**为什么 pi 的 C8/C17 记 N/A（需 owner 签字）**：pi 的会话身份由 **CLI 调用**决定，不由插件决定：
```
pi --session-id <id> | --continue | --session <path|id>    ← 会话是谁跑出来的就是谁的
```
插件（扩展）只能把邮件投进**当前这一轮**，无法新建或改挂会话。实测也正是如此：每轮一个新会话文件。
因此契约 C8「新 thread ⇒ 新 session」在 pi 上**不适用**——用户始终在同一个会话里，邮件跟随用户会话，
不存在"路由到哪个会话"的选择；C17「会话被删后重挂」同样不适用（下一轮自然会创建新会话）。

这与 openclaw 的 C8 属同一类：**宿主拥有会话身份**。区别是 openclaw 有 sessionKey 机制但不去重，
pi 则是根本没有"插件指定会话"的接口。

处理建议：`pi / C8 → N/A`、`pi / C17 → N/A`（附本节证据），记入 `GATE.md` 已知项；
未签字前矩阵保持 OPEN，不计绿。


## 附 F：dsh 的降级原因在实时路径没有落账（v9 实证，2026-11-25）

```
log files=1  lines=11
last = {"ts":"2026-11-25T07:36:07.255Z","event":"inbound","from":"ext-sender@example.com",
        "to":"l2dsh@test.local","subject":"C8 threading probe"}
dispatch_reasons=[]        # 11 行里没有 "reason" 键，也没有 event:"dispatch"
```
对照：单测 `test/dsh/inbound-session-gone.test.ts:121` 读**同一个文件**
（`fs.readFile(aimailLogPath(EMAIL))`）并断言含 `"reason":"session-gone"`，且通过。

**结论**：分支逻辑本身没问题（单测证），但**实时路径不写这一行**。
这已是同类模式的第三例：
1. `runtime/routing.ts` 无宿主调用（改在未接线处）→ 本轮已由 dsh 接线解决；
2. `cfg.session_id` 从不落盘（改在不存在的字段上）→ 本轮已由 `saveLocalMeta` 读-合并-写解决；
3. **`logAimailDispatch` 在实时路径不执行**（测在未调用处）← 本附。

**问一句就够**：L2 的 pull→adapter 回调是否经过加了 `logDispatch` 的那个函数？
- 经过 ⇒ 为什么 `agentmail.log` 没有 `event:"dispatch"` 行（`appendLog` 失败是静默的，需要打点）；
- 不经过 ⇒ `reason` 挂到真正执行的路径，或把单测改成走实时路径。

**当前判定**：dsh C17 = 行为 ✅（ack / 新会话 / root 保住）+ 证据 ❌（reason 不可见）⇒ RED（1 条），归因产品。
