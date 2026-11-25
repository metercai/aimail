# 退回件 — C17 会话消失后仍 ack，且不重挂（邮件会被吞）

- 发现日期：2026-11-25
- 发现者：verifier（L2 s7 首跑）
- 对象：`tssdk/packages/agent-aimail`（`runtime/routing.ts` + 拉取 ack 语义）
- 契约依据：`docs/aimail-host-sse-contract-v2.md` §9.3 降级表 / §16 C17
- 严重度：**S1 — 静默丢信**。ack 之后网关不再重投，因此 owner 的审批回信会永久消失。

## 1. 现象（可复现）

dsh 宿主，`--stages "s1 s6 s7"`，三次运行结论一致：

```
mail A（新 thread）        → 宿主新会话 7cf927e4-01b2-45ab-982a-41a5b2b908aa
删除该会话（宿主存储）      → 确认已消失
回信（In-Reply-To: mail A） → acked=True
                             新会话：无
                             被写过的会话文件数：0        ← 哪里都没落
```

`/out/s7_gone.json`：
```json
{"acked": true, "deleted": "7cf927e4…", "used": "", "reason": "",
 "root_ok": false, "redirected": false,
 "landed_in_existing": false, "delivered_anywhere": false,
 "session_gone_observed": true}
```

## 2. 根因（代码定位）

1. `runtime/routing.ts` 的 `direct` 分支**只查本地 meta 索引**，没有任何"该 session 是否仍存在"的检查：
   ```ts
   return { kind: 'direct', sessionId, parent, workspace: input.workspace }
   ```
   ⇒ 目标会话已消失时仍判 `direct`，没有走 §9.3 要求的"新建会话 + thread 重挂"。
2. 该文件**没有 `reason` 字段**，全仓也搜不到 `session-gone`（`grep -rn session-gone src/` = 0）⇒ C17 要求的
   "记录降级原因"无处产生，运维无法察觉发生了重定向。
3. `core/address-code.ts:534-544`：只要处理函数没有返回 `rejected` 就进 `delivered` 并 `pullAck`。
   而钩子调用拿到宿主 HTTP 200 即视为 `delivered` ⇒ **会话丢了也照样 ack**，于是这封信不再重投。

## 3. 期望行为（按契约 §9.3）

```
E1/E2 命中记录的 Session 已不存在
  ⇒ 新建 Session 并把 thread 指针重挂过去（同一个 thread 内）
  ⇒ 投递成功后 ack
  ⇒ 记录降级原因 `session-gone`（可观测）
```

## 4. 判据（夹具已就绪，`s7` 会直接判）

| # | 断言 | 现状 |
|---|---|---|
| 1 | `acked == true` | ✅ 已满足 |
| 2 | 落到**新会话**（`used` 非空且 `used != deleted`） | ❌ `used == ""` |
| 3 | `reason` 含 `session-gone` | ❌ 空 |
| 4 | thread root 未丢（回信仍属同一 thread） | ❌ 无从谈起（未落地） |

`bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s6 s7"` 复现。

## 5. 修法建议（不替开发动手，仅给方向）

1. `routing.ts` 的 `direct`/`followup` 分支增加**存在性检查**（注入一个 `sessionExists(sessionId)` 回调，
   宿主侧实现：dsh 查 `sessions/<ws>/<id>/`，hermes 查 `sessions.json` 键，deerflow 查 thread 行，
   openclaw 用 `openclaw sessions --json`）；
2. 不存在时走 `new` + 重挂 thread，并在返回值里带 `reason: 'session-gone'`；
3. **ack 必须与"真的落地"绑定**：把 dispatch 结果从"HTTP 200"提升为"目标会话已写入"（或至少在
   `routing` 报 `session-gone` 时确保新会话已建），否则"200 但没落"仍会丢信；
4. 单元测试：给一个"索引里有、存储里没有"的会话，断言 `kind === 'new'` 且 `reason === 'session-gone'`。

## 6. 说明与边界（避免误伤）

- 本次删除是**绕过宿主直接删存储**。契约里"Session 已不存在"包含"被弃用/删除"，用户删对话、存储损坏、
  GC 都会产生同一状态，因此这是合法场景；但严格版还应支持"通过宿主 API 删除"（记为 `S7-HOOK-3`，
  待宿主提供接口）。
- 这不影响已发布版本的历史结论：`s7` 是新增阶段，0.2.1/.piplug 与 0.3.0/PyPI 的发版判据里都不含 C17；
  但从本轮起 **R4 不满足即不得宣称 C17 达成**。

## 7. 规范原文（决定修法，不是我的口味）

`docs/aimail-host-sse-contract-v2.md` 已经把"会话被删"这条路径写死了：

| 出处 | 原文 |
|---|---|
| §9.3 降级表 | 「E1/E2 目标 Session **已不存在**（被弃用/删除）｜新建 Session 并把 thread 指针重挂过去，投递成功后 ack｜`session-gone`」 |
| §9.3 末段 | 「重挂（`session-gone`）是**唯一允许修改 thread 指针**的时机；它不要求原 Session 存在，因为 thread 的上下文随邮件本身携带」 |
| §9.5 | 「`route(...) → Target \| Refusal`；**Target 且投递成功 ⇒ ack；Refusal ⇒ 不 ack**」；reason 表：`session-gone` =「命中记录指向的 Session 已不存在，**已重挂**（最终仍可成功）」 |
| §9.3 E3 行 | 「无可用 Session ⇒ Refusal(`no-target`) ⇒ **不 ack**」；「E3 无法新建（能力或失败）⇒ 用该锚点内当前 Session｜`space-fallback`」 |
| §16 C17 | 「E1/E2 指向的 Session 已不存在 ⇒ 重挂 thread 并投递成功（记 `session-gone`），**不得永久 pending**」 |

结论：规范要求的是 **"重挂 + 落到当前锚点内的会话 + 成功后 ack + 记账"**，并且**投递没成功就不许 ack**。
我们现在的实现两头都不对：既不重挂，又在没落地时 ack。

## 8. 修法（分两级，可分批交付）

**T1 — 止血（最小改动，先让"丢信"变成"可见的卡住"）**
- `ack` 只与"确认落地"绑定：dispatch 返回 not-delivered（含宿主只给 200 但目标会话不存在）时，
  按 §9.5 返回 `Refusal(no-target)` ⇒ **不 ack** ⇒ 网关按 at-least-once 重试；
- 效果：邮件不再消失，变成 pending（运维可见、可重投）。**这是发版前必须有的部分**，因为它把
  "静默丢审批回信"降级为"暂时卡住"。

**T2 — 合规（C17 本体）**
- `routing.ts` 的 E1/E2 命中分支注入 `sessionExists(sessionId)`，不存在时改判 `new` 并把
  `thread[anchor][root]` 重挂到新会话，返回 `reason: 'session-gone'`；
- `sessionExists` 由宿主适配器提供（dsh：`sessions/<ws>/<id>/`；hermes：`sessions.json` 键；
  deerflow：thread 表；openclaw：`openclaw sessions --json`）；
- 记账落到插件的 dispatch 日志（`agentmail.log` 的 `reason` 字段），使"重定向"对运维可见；
- `pysdk` 侧同构（`v2` 路由 + 邮箱回退），保持两 SDK 行为一致；
- 单测：`索引里有、存储里没有` ⇒ `kind==='new'` 且 `reason==='session-gone'`；`宿主拒绝` ⇒ 不 ack。

**夹具侧（我方，T2 之后不需要改判据）**
- s7 继续判四件事（ack / 落到新会话 / reason / root 存活）；T1 单独生效时 s7 会显示
  `acked=false` + 无新会话 ⇒ 明确判 RED 并注明"T1 未做"；
- `S7-HOOK-3`：如果能拿到宿主 API 删除会话，就把"绕过存储"换成"通过宿主删除"。
