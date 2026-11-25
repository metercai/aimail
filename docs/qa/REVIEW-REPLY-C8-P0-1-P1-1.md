# 评审意见 — 开发回复 `REPLY-TO-QA-C8-P0-1-P1-1.md`

- 日期：2026-11-25
- 对象：`docs/qa/REPLY-TO-QA-C8-P0-1-P1-1.md`（开发自评：C8 已修、P1-1 已修、P0-1 部分修复）
- 复验坐标：共享工作树当前状态（L0 绿）+ 三次 L2 实测
- 结论：**C8 通过（我此前的判定错误，撤回）· P1-1 通过 · P0-1 不通过（未验证 + 一处收敛驳回）**

---

## 1. C8 · openclaw：**通过** —— 并撤回我之前的"宿主限制"判定

开发说的对，我错了。实测（`/tmp/verify-oc-s6.log`，端口 18422）：

```
PASS  all three mails acked
PASS  the reply resolves to the same thread root
PASS  reply reused the thread's session (879f185c-0cf8-4a11-ac40-49dfd91712eb); no new session was created
PASS  host session count did not grow on the reply (3)
PASS  a new thread started a new host session (2d577441-…)
PASS  host session count grew by exactly one for the new thread (3 -> 4)
PASS  all three sessions live in the binding's anchor: /app/.openclaw/workspace
VERDICT: PASS
```
派发日志同时出现 `sessionModeRequested: "persistent"`，与开发所述"此前从不发 sessionMode、默认 isolated"
完全吻合。

**我的错误在哪**：我做了四组实验，只验证了**键本身**被接受（同一 caller key 连发两次仍两个会话、
`badkey` 400），却从未验证"我们**有没有发** `sessionMode`"。缺了这一个参数，宿主当然每次 `isolated`。
结论：这不是宿主天花板，是我们的缺参；我据此提出的 `openclaw / C8 = EXEMPTED` **作废**，无需签字。

## 2. P1-1 · `agentId=""`：**通过（附一处残余）**

键已正确：`sessionKey: "agent:main:aimail:17cab5d7ac6e1ce6fb323d5a"`（`agentId` 段 = `main`，不再是空）。
残余：同一条派发记录里 **body 的 `agentId` 仍是 `""`**，与键里的 `main` 不一致。若确为"一次解析两处使用"，
body 也应是 `main`；请确认这是日志记的原始值还是 body 真的没改。不影响 C8，登记为观察项。

## 3. P0-1 · C17：**不通过**（未验证，且一处收敛驳回）

### 3.1 断言① —— 接受，并且这是我的定位错误
`decideInboundDelivery` 在 `runtime/` 之外**无任何调用**（我复核 grep 一致）。所以我此前"改 `routing.ts`
即可修 C17"的处方打在了未接线的模块上；真正要改的是各宿主 `inbound.ts` 的投递与 ack 决策。这一点我认。

### 3.2 断言② —— 产者已写，但**没有任何测试覆盖，L2 也无法触发**

- 代码在：`src/dsh/inbound.ts:211` `status: 'session_gone_redirected'`；
- 但 `grep -rln "session_gone_redirected" test/` **只命中 `test/core/runtime-routing.test.ts`**（测的是那个
  未接线的模块），**没有 dsh inbound 的用例**；
- L2 侧仍然复现丢信（`/tmp/verify-dsh-s7.log`）：
  ```
  PASS  reply was acked (not left pending)
  RED   no session received the reply (used=''); the mail was acked and dropped
  RED   downgrade reason '' does not name session-gone
  RED   thread root changed on re-attach
  s7: {"acked": true, "used": "", "reason": "", "delivered_anywhere": false}
  ```
  驱动侧我的取值已按你的要求实现（读 `systems/*/*/agentmail.log` 的 `session-gone`，日志里确实为空）。

**为什么触发不了**：我删的是会话目录（`rm -rf`），dsh 的**注册表仍然认为该会话存在**，于是
`agents.resume(boundId)` 成功 → 你的产者分支不进入 → 写入落空 → 仍然 ack。这不是"你的修复无效"，而是
**我造不出你说的那个状态**（dsh 没有会话删除命令；注册表位置/格式未知）。所以：**C17 目前是未验证，不是
被推翻**。要闭环，需要二者之一：
1. 宿主提供一个"按宿主语义删除会话"的测试入口（或告知注册表位置与删法），我用它重跑 s7；
2. 或者补一个能直接证明 `resume` 失败 ⇒ 新会话 + `session-gone` 的测试（dsh inbound 级）。

### 3.3 断言③ / N/A 收敛 —— **部分驳回：hermes、deerflow 不接受 `admission-only`**

你们把 `hosts/hermes.sh:30`、`hosts/deerflow.sh:21` 也标成 `admission-only`，于是 s7 在这两个宿主上变成
`N/A`。这我不能接受，理由不是"我要求更高"，而是**证据形态不同**：

| 事实 | 含义 |
|---|---|
| 我在 hermes/deerflow 上实测到 `acked=true` + **零会话被写入** | 这是**丢信结果**，不是"无法证明重挂" |
| 丢信与宿主是否具备"存在性探测"**无关** | 不需要任何新 API 就能避免：投递没落地 ⇒ 不 ack（T1） |
| 契约 §9.3 的原句是"目标 Session 已不存在" | 我造的状态（注册表有、存储无）正属此类 |

把这两家标 `admission-only` 的净效果是：**一条已被实测证实的丢信路径，从"判红"变成"不判"**。这与
`na_scope` 自己的注释（"不得用它掩盖跑不起来的检查"）精神相反。

如果 owner 确实选了"作用域收敛"（C 方案），那也必须**显式签署**并写明代价：
> admission-only 宿主上，邮件可能被 ack 而未被写入，且系统不会重投 —— 该风险由 owner 接受。
未签字前，hermes/deerflow 的 s7 应保持 RED（或至少保持 OPEN），不得记 `N/A`。

## 4. 我复核过的其它声明（都成立）

| 声明 | 复核 |
|---|---|
| `sessionMode` 已在 `inbound.ts:191` 且与 key 联动 | ✅ |
| `selectSessionKey` E1→E2 + `directReplyDropped` | ✅ `:107/371/401` |
| 幂等键含 sessionKey（`dispatchIdempotencyKey`） | ✅ `:84/375` |
| `routing.ts` 增 `sessionExists` + `kind:'gone'`，探测抛错按存活 | ✅ `:30/47/80-85` |
| `assert.sh` 的 `na_scope` 独立计数、显式打印、不计绿不计红 | ✅ 机制本身没问题 |
| `s7` 默认 `probe`（未声明仍受判）、未知取值 ⇒ GAP | ✅ fail-closed，方向正确 |
| L0 绿 | ✅ `[L0] PASS`；vitest **588 passed**（与你自评一致） |

## 5. 发版结论

**阻断。** 三项归属：

| 阻断项 | 归属 | 解除条件 |
|---|---|---|
| C17 在 dsh 上未验证（产者不可触发、无测试） | 产品 + 夹具（各一半） | 给测试入口/注册表删法，或补 dsh inbound 单测 |
| hermes/deerflow 的 `N/A` 会把已证丢信路径放行 | **契约/裁定** | owner 书面签署"接受 ack-未落地"风险，或撤回该两家的 admission-only 标记并修 T1 |
| 派发 body 的 `agentId` 仍为空（与键不一致） | 产品（观察项） | 确认并统一 |

C8 与 P1-1 已可放行，不再是阻断项。
