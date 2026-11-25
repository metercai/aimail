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

## 附：本轮 L2 回归发现（跑完后补全）

> 见文末表格；本轮命令：`/tmp/l2-v23-batch.sh`，证据 `/tmp/l2-v23/*.log`。
> 判据：每宿主 `RED=0 GAP=0 SKIP_LEGACY=0`。
