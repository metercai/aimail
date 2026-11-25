# 回复质检：C17 三问（Q1 落盘 / Q2 挂点 / Q3 不 ack）

日期：2026-11-25
对象：`docs/qa/HANDOFF-TO-DEV-C17-OPEN-QUESTIONS.md`
结论：**三问全部有答案，其中 Q1/Q2 已改代码（含根因修复）；Q3 协议允许，可判定性按宿主分层**。

---

## Q1 `session_id` 为什么没有落盘到 `agentmail.json`？

### 答案：它本来就不是"路由指针"，而且被两处主动抹掉

1. **写得晚、删得早**：`session_id` 只在**新会话投递**时写入，并在该轮 idle 后被删——
   `src/dsh/inbound.ts:197`（`updateAgentConfig(..., { session_id: sessionId })`）→
   `src/dsh/inbound.ts:201-207`（`whenIdle` 后把它从 binding 里删掉）。
   它存在的唯一目的是让**本轮的工具调用**能通过 `resolveBySessionId` 找到凭据（`src/mail/*.ts:79-89`），
   不是给下一封信做路由。
2. **更关键：每封信的 preprocess 都会整条重写 meta，把 session 归属冲掉**——
   `src/core/meta.ts` 的 `saveLocalMeta` 原来是"替换写"：`processInboundMail` 每次都调它，
   于是 `stampSessionMeta` 写进去的 `session_id`/`session_key`/`workspace_path` 全部丢失。
   这就是你探针看到的"配置在、键不在"的真正机制（不只是 idle 删除）。

### 已改（这句是根因修复）

- `src/core/meta.ts::saveLocalMeta`：改为**读-合并-写**，保留 `session_id` / `session_key` / `workspace_path`，
  其余字段（references/thread_id/direction/at）照常刷新。→ 每封信不再抹掉"这条 thread 归谁"。
- 回归测试：`test/core/meta.test.ts` 新增 1 项（`an inbound rewrite must not erase the session that owns the thread`）。

### 可复现

```bash
cd tssdk/packages/agent-aimail && npx vitest run test/core/meta.test.ts   # 16 passed
```

> 关于你列的判据（"`updateAgentConfig({session_id})` 之后文件里非空"）：**该判据仍然不成立，且我不打算让它成立**——
> `agentmail.json` 的 `session_id` 是"当前正在跑的那一轮"的凭据指针，idle 即失效；把它当路由键正是 Q2 要改的东西。
> 新的路由键在 `mail/meta/<first2>/<root>.json` 的 `session_id`（下一节）。

---

## Q2 产者挂在哪条实际生效的路径上？

### 选择：(b) 挂 thread/meta —— 并顺带把 `runtime/routing.ts` 真正接上线

你指出"`decideInboundDelivery` 无宿主调用"是准确的；本轮 dsh 改为**用它路由**，因此那条"未接线"的问题一并消失。

| 改动 | 文件:行号 | 为什么改这里会生效 |
|---|---|---|
| 路由改为按邮件血缘：E1 直属 → E2 thread → E3 新建 | `src/dsh/inbound.ts:170-205`（`decideInboundDelivery` + `deliverTo`） | dsh 以前按 `cfg.session_id`（=上一轮那个会话）投递，导致所有 thread 共用一个会话，且产者无触发点 |
| 投递尝试本身即存在性探测：live → followup；cold → resume；resume 失败 → 新建 + `session-gone` | `src/dsh/inbound.ts:196-215`（`deliverTo`）、`:176-186`（失败转 `deliverFresh('session-gone: …')`） | 不需要额外 API；`resume` 失败就是"该会话不可恢复"的宿主语义 |
| 投递成功后回写 thread 指针 | `src/dsh/inbound.ts:217-229`（`stampThread` → `stampSessionMeta`） | 下一次同 thread 才能解析到同一会话（E2）；也给你提供了"会话被换掉"的观测点 |
| `session_id` 仍只在轮内写入（工具凭据用） | 保持原样 | 与路由解耦，避免"一个会话服务所有 thread" |

### 验收（我方已跑）

```bash
cd tssdk/packages/agent-aimail && npx vitest run test/dsh   # 5 passed
```

测试文件 `test/dsh/inbound-session-gone.test.ts`，5 项：

1. thread 指向的会话 `resume` 失败 ⇒ `ok:true` + **新会话**（id≠旧）+ `status: session_gone_redirected` + detail 含 `session-gone` 与旧 id；
2. `resume` 成功 ⇒ `resumed`，**不**新建、不标 gone；
3. 会话 live ⇒ 直接 followup（不做 resume 往返）；
4. **直属回复**：thread 有会话，但这封信回答的是另一个会话发的信 ⇒ 进**发信那个会话**（E1 优先）；
5. 首次出现的 thread ⇒ 新会话，并把 thread 指针钉到它。

### ⚠ 你的驱动要跟着改一处（否则仍触发不了）

`s7_drive.py` 目前改写的是 **binding 的 `session_id`**；而路由已经不读它了。请改为改**thread meta**：

```bash
# 把 s6 第一封信落地的那个 thread 指向一个不存在的会话
# 路径：$AIMAIL_HOME/systems/<sid>/<addr>/mail/meta/<root 前2字符>/<root>.json
python3 - <<'PY'
import json, glob, os
home = os.environ.get("AIMAIL_HOME", "/root/.aimail")
for f in glob.glob(f"{home}/systems/*/*/mail/meta/*/*.json"):
    d = json.load(open(f))
    if d.get("session_id"):
        d["session_id"] = "s7-nonexistent-<ts>"      # 让 resume 必然失败
        json.dump(d, open(f, "w"), indent=2)
        print("rewrote", f)
        break
PY
```

改完后 s7 在 dsh 上应看到：`PASS acked` / `PASS used a new session` / `PASS reason names session-gone` / `PASS thread root survived`。

---

## Q3 `unconfirmed` 宿主能否在"未确认落地"时不 ack？

### 协议侧：**允许**，而且 SDK 已经是这个形状

- §8.3 / §9.5：Refusal ⇒ 不 ack；`route()` 返回 Target 且**投递成功**才 ack。
- 实现侧：`src/core/address-code.ts::deliverBatches` —— 回调**抛错**或返回
  `{status:'skip'|'deferred'|'rejected'}` ⇒ 该 delivery 不 ack，`seen` 回滚，下一轮重投。
  所以"不 ack 让它 pending 重投"没有协议障碍。

### 判据侧：能不能判断"没落地"，分宿主

| 宿主 | 可用的确认点 | 现在能不能"未落地 ⇒ 不 ack" |
|---|---|---|
| dsh | 投递结果本身（`get`/`resume`/`create` 都有明确失败） | **能**（本轮已做：失败即 `ok:false`/新建失败 ⇒ 不 ack） |
| deerflow | 先 `POST /api/threads` 建 thread，再 `POST …/runs`；两者都有状态码 | **可做，未做**：投递后回读 thread（如 `GET /api/threads/<id>` 或消息列表），为空/404 ⇒ 判未落地、不 ack。这是下一件具体活，我可以接着做 |
| hermes | `_bridge.deliver` 只回 `bool(event._gateway_accepted)` | **不能**：网关"接受"不等于会话存储；你实测的 accepted=true + 零写入正是这个缝 |
| openclaw | `200` ＝ cron job **入队**（`gateway/server/hooks.ts:286-310`） | **不能**：插件面无 run 状态、无会话查询 |

### 关于"写回 thread 指针算不算确认点"——**不算**

`stampSessionMeta(thread → session)` 是**我方簿记**：它只证明"我们把信交给了哪个会话"，不证明宿主把它写进了
transcript。把它当 ack 依据，就等于把"我们记了账"当成"对方收了货"。所以 §9.4 的指针写入**只能**用于下一次
路由，不能作为 ack 判据（契约 §8.3 已在正文写明"可证性取决于宿主"）。

### 因此需要 owner 决定的只剩一处

`hermes` / `openclaw` 在拿到宿主回执前无法满足"未落地不 ack"。两个选项：

- **接受风险**：owner 书面签署"`unconfirmed` 宿主可能 ack 而未落地，系统不重投"；
- **要回执**：向上游提"投递回执/run 状态"需求；deerflow 可先做回读校验（不需要上游）。

在签字或实现前，这两家的 `s7` 按你的要求保持受判（缺证据 GAP、实测丢信 RED），不记 N/A。

---

## 与 `REPAIR-TASK-2026-11-25.md` P0-1（T2 全合规）的对照

你那份任务单把 C17 定为 **T2 全合规**，我按条目对一遍，避免"改了但没对上"：

| T2 条目 | 状态 | 落点 / 说明 |
|---|---|---|
| 1. `routing.ts` 注入 `sessionExists`；E1/E2 不存在 ⇒ 改判 `new` + 重挂 + `reason: 'session-gone'` | **部分**：`sessionExists`/`gone` 已在（`src/runtime/routing.ts:30-115`），**但 dsh 不提供预探针**——见下 | dsh 的"存在性"不能靠 `agents.get`：冷会话不在进程里但可 `resume`，用 live-only 探测会把可恢复的会话误判成 gone。所以 dsh 用**投递尝试本身**当探针（`get` → `resume` → 失败才 gone），行为等价、不误判 |
| 2. `sessionExists` 由各宿主提供（你给了四家参考实现） | **未做** | 需要先回答一个语义问题：hermes 的 `sessions.json` 键存在 ⇒ 仍可能不落地（你已实测）；openclaw 的 `openclaw sessions --json` 是 CLI 而插件进程不一定可调用。等这条明确后我再接 |
| 3. ack 与"落地"绑定：确认写入后才 Target，否则 Refusal ⇒ 不 ack | **dsh 已具备**（`create` 失败 ⇒ `ok:false` ⇒ 不 ack；`resume` 失败 ⇒ 新建 + ack）；**hermes/openclaw 未具备**（无落地确认点） | Q3 已列各宿主的确认点；deerflow 可做回读校验 |
| 4. 记账写进 `agentmail.log` 的 `reason` | **已完成（dsh）**：`logAimailDispatch(..., { reason: 'session-gone' \| 'new-session' \| 'thread-session' \| 'resumed' \| 'create-failed', sessionId, threadRoot })`，单测断言日志含 `"reason":"session-gone"` | `src/dsh/inbound.ts` `logDispatch()` |
| 5. pysdk 同构 | **未做** | 需要先有 pysdk 侧"落地确认点"（hermes 只能拿到 `_gateway_accepted`；deerflow 有 thread/run 接口可回读） |

**一句话**：dsh 的 P0-1 已经从"逻辑已证、路径不可达"变成"路径可达 + 记账可见"；剩下两条硬骨头是
**hermes/openclaw 的落地确认**（上游或签字）和 **pysdk 同构**（先定 pin 在设计上怎么确认）。


---

## 更新（同日，回答"这三条是不是待办"）

三条**都是待办**，但性质不同，现状如下：

| # | 事项 | 现在 | 还需谁动 |
|---|---|---|---|
| 1 | deerflow 投递后回读校验 | **已完成**：`pysdk/deerflow_plugin/runtime.py` 在 run 接受后 `GET /api/threads/<id>/runs/<run_id>`；`404` 记 `landing-unverified` 放行（老宿主），非 200 或 `failed/error/cancelled` ⇒ **不 ack**；开关 `AIMAIL_DEERFLOW_VERIFY_LANDING=0` 可关。测试 +5（该文件 14 passed）。宿主声明改 `probe`，契约附 E 同步 | —— |
| 2 | hermes / openclaw 的"落地确认"语义 | **卡在一个裁定**：什么算"落地" | owner 选口径（见下） |
| 3 | pysdk 同构 | deerflow 已有（本轮）；hermes 取决于 #2 的裁定 | 随 #2 |

### #2 已裁定（owner，2026-11-25）：**无公开 API 就接受风险**

裁定原文（口头，本文件记录为执行依据）："如果确认没有来自宿主公开 API 来落地确认，那就承受风险。
宿主方不可能为我们调整代码和开放接口。"

落地（已实现，可机读、可审计）：

| 层 | 落点 |
|---|---|
| 契约 §8.3 | 新增"风险接受"条：owner 可书面接受"可能 ack 而未落地、系统不重投"；该宿主 C17 记 `RISK-ACCEPTED`，**必须带签署引用 + 实测证据**，打印但不计入绿/红；未签署的仍按 GAP/RED |
| 门禁词汇 | `lib/assert.sh` 新增 `risk_accepted()` 与 summary 的 `RISK-ACCEPTED=n`；`GATE.md` 词汇表补该行，并写明只用于"宿主无法确认 + owner 签署"，不得用于夹具缺陷或可修 bug |
| `s7` | `unconfirmed` 且带 `HOST_RISK_ACCEPTED` ⇒ 打印实测（`acked/used/reason` 或"无证据"）并记 `RISK-ACCEPTED`；未签署 ⇒ 维持 GAP/RED |
| 宿主声明 | `hosts/hermes.sh`、`hosts/openclaw.sh` 加 `HOST_RISK_ACCEPTED="owner 2026-11-25: …"`；deerflow 已升 `probe`（回读 run），不在此列；pi 仍是 `no-session-model`（义务为空） |
| 已知项 | `GATE.md` 增 `C17-RISK-1`，写明两家为何无 API、签署来源、以及 deerflow 不在列 |

**空跑验证**（三种输入）：

| 输入 | 结果 |
|---|---|
| 已签署 + 证据显示 `acked` 但 `used=''` | `RISK-ACCEPTED`：`VERDICT: PASS`（`RED=0 GAP=0 RISK=1`），行内带 `measured: acked=True used='' reason=''` |
| 未签署 + 同样证据 | 仍 `RED=3`（不因"没签字"而变绿） |
| 已签署但无证据 | `RISK-ACCEPTED`，行内写 `measured: no s7_gone.json (host hook absent)` |

### 原备选（已不需要，保留作背景）

**"落地确认"是否必须来自宿主公开 API？**

| 选项 | 含义 | 后果 |
|---|---|---|
| A（严格） | 只认宿主公开 API（run 记录 / 消息回读）。读宿主私有文件、跑 CLI、抓日志都不算 | hermes、openclaw 需上游开口；在开口前 `unconfirmed`，要么签字接受风险，要么这两家 C17 记 GAP |
| B（放宽） | 允许**只读**宿主自己的持久化记录（hermes 的 `sessions/sessions.json` 与 state DB；openclaw 的 `openclaw-agent.sqlite`），写后回读比对消息数/最后一条 | 立刻可实现，两家可能当天闭环；代价：与宿主内部格式耦合，升级可能碎，且必须每次记录"用了哪个文件/哪张表" |

我的建议是 **A**，理由：B 会让 SDK 依赖宿主私有存储，出问题时既难诊断又容易在升级后静默失效；而 C17 的价值正是"审批回信不丢"，用一个会碎的探针去换"看起来闭环"不划算。若 owner 选 A，则 hermes/openclaw 的下一步是提上游需求（openclaw 需要 run 状态查询或投递回执；hermes 需要 bridge 回执），同时这两家在 s7 上保持受判。

### #3 pysdk 同构的现状

- deerflow：已按 `runtime.py` 落地（与 ts 侧 dsh 同形：投递后回读，失败即 raise ⇒ 不 ack）；
- hermes：`_bridge.deliver` 只有 `bool(event._gateway_accepted)`；按 A 口径它就是"宿主已接管"，不是落地 ⇒ 保持 `unconfirmed`；
- pysdk 的 `v2/` 路由（`binding.py`/`thread.py`）本轮未改：它只决定投给哪个 chat/thread，不参与 ack 判定。
  按 owner 的裁定（无公开 API 即接受风险），**不需要**为 hermes 造文件级探针，因此"pysdk 同构"收敛为：
  deerflow 已具备回读（与 ts 侧同形），hermes 记 `RISK-ACCEPTED`，不再有未决项。


---

## 收尾（owner 裁定后，2026-11-25）

| 项 | 状态 |
|---|---|
| P0-1 / C17（dsh） | 路由按血缘 + 投递即探测 + 失败不 ack（新增用例：`create` 抛错 ⇒ `ok:false`，不进入 delivered）；`agentmail.log` 带 `reason` |
| P0-1 / C17（deerflow） | 投递后回读 run：终态失败或回读非 200 ⇒ 不 ack |
| P0-1 / C17（hermes、openclaw） | **owner 签署接受风险** ⇒ `RISK-ACCEPTED`（打印实测，不计绿/红） |
| P1-1 | 键与 body 均用 effective id；日志新增 `resolvedFrom: binding|default`（另存 `dispatchAgentIdRaw`） |
| P2-1（注释矛盾） | 你引用的那句 "this integration does not need session reuse" **已不存在**（上一轮随 `sessionMode` 一起改掉，`grep` 为空）；现注释为"默认 isolated ⇒ 必须显式要 persistent" |
| P2-2 版本策略 | 仍待 owner（是否发 `0.5.3` 以启用源码级 `agent-aimail/desktop`；`.piplug` 自带 bundle 不受影响） |
| s7 驱动 | 你已实现 `point_thread_at_missing_session()`（改 thread meta 而非 binding），与新的路由键一致，可直接重跑 |

**单元/回归**：`npx vitest run test/dsh` 27 passed（含本轮 +1）；全量见下。

## 附：本轮改动与验证

| 文件 | 改动 |
|---|---|
| `src/core/meta.ts` | `saveLocalMeta` 读-合并-写，保留 session 归属字段（Q1 根因） |
| `src/dsh/inbound.ts` | 按 §9 血缘路由（E1/E2/E3）+ 投递即探测 + `session-gone` 产者可达 + thread 指针回写（Q2） |
| `src/runtime/routing.ts` | 由 dsh 实际调用（此前"未接线"的问题消失） |
| `test/dsh/inbound-session-gone.test.ts` | 5 项（上表 1-5） |
| `test/core/meta.test.ts` | +1（重写不得抹掉 session 归属） |

验证：`npx vitest run test/dsh test/core/meta.test.ts` → 21 passed；全量 590+ 见下；`l0-gate-tests.sh` PASS（本轮结束时复跑）。

**没做的**（明确列出，不用"已登记"代替）：

1. deerflow 的投递后回读校验（Q3 唯一可自行闭环的部分，需要单独一轮，约半天）；
2. hermes / openclaw 的回执（上游）；
3. `s7_drive.py` 的改法已给出，但夹具文件在你的改动范围内，我没有直接改，避免与你的编辑冲突。

---

## 追加回答：`logDispatch` 在不在实时路径上？（2026-11-25 晚）

**在。** 调用链只有两条，都进同一个函数：

| 入口 | 路径 |
|---|---|
| 实时投递（L2 的 pull → adapter 回调） | `startInboundPull` → `startAgentPullEntries.onEmail` → **`deliverPulled()`** → `deliverInbound()` → `logDispatch()` → `logAimailDispatch()` → `appendLog()` |
| 推进入站（HTTP） | `createInboundHandler` → `deliverInbound()` → 同一条 |

你问"为什么不经过 / agentmail.log 里没有 `event:"dispatch"`"，三个原因按可能性排：

1. **你的那次运行早于这段代码**：`logDispatch` 是今天 15:0x 才加进 `deliverInbound` 的，v6 的运行早于此（`REPAIR-TASK` 14:52）——所以日志里没有它是正常的；
2. **失败确实曾是静默的（你指出的对）**：我原来写了 `.catch(() => {})`，会把写入失败吞掉。**已改**：现在失败会打一行 `[aimail] dispatch log write failed for <addr>: …` 到 stderr（仍不影响投递结果）；
3. **文件落点**：`aimailLogPath(email)` = `$AIMAIL_HOME/systems/<systemIdForEmail(email)>/<clean_addr>/agentmail.log`（`src/core/log.ts:16`）。若 `systemIdForEmail` 解析不到，会落到 `systems/_unassigned/…`——所以 grep 请用 `systems/*/*/agentmail.log`。

**并且按你的第二问"或把单测改成走实时路径"，已改**：把 `onEmail` 的闭包体抽成导出的 `deliverPulled()`（`src/dsh/inbound.ts`，`startInboundPull` 现在只调它一行），新增用例直接驱动它并断言真实日志文件：

```bash
cd tssdk/packages/agent-aimail
npx vitest run test/dsh/inbound-session-gone.test.ts   # 7 passed
```

该用例断言的正是 `agentmail.log` 里出现 `"event":"dispatch"` 与 `"reason":"session-gone"`。

**给质检的重跑命令**（新构建）：

```bash
bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s6 s7"
# 期望：s7 四条 PASS
# 容器内证据：grep -o '"event":"dispatch".*"reason":"[a-z-]*"' /root/.aimail/systems/*/*/agentmail.log | tail -5
```

## deerflow 投递后回读校验：已实现 + 已核宿主（2026-11-25 晚）

`pysdk/deerflow_plugin/runtime.py` 在 `POST /api/threads/<id>/runs` 拿到 `run_id` 之后，回读
`GET /api/threads/<id>/runs/<run_id>`，按宿主记录决定是否 ack：

| 回读结果 | 处理 | 理由 |
|---|---|---|
| `200` 且 `status ∈ {pending, running, success}` | ack | 宿主已落库并接管 |
| `200` 且 `status ∈ {error, timeout, interrupted}` | **raise（不 ack）** | 宿主自己说这次 run 不会再处理，ack 就是丢信 |
| `401 / 403 / 404` | 记 `landing-unverified` + ack | 回读不可用 ≠ 投递未落地（见下） |
| 其它非 200（5xx）或传输失败 | raise（不 ack） | 宿主刚回过 200，回读失败属真异常；pending 会重投 |

`AIMAIL_DEERFLOW_VERIFY_LANDING=0` 可整体关闭（回读路由更老的宿主）。

**在真实宿主上核过的两件事**（`l2df-1809185` 容器内源码 + 夹具脚本）：

1. **鉴权不会把我们挡在外面**：`get_run` 带 `@require_permission("runs","read", owner_check=True)`，
   但认证入口 `_authenticate → get_optional_user_from_request` 在 `DEER_FLOW_AUTH_DISABLED=1` 时给的是
   合成 `admin` 用户（`auth_disabled.get_auth_disabled_user()`），而夹具在起 uvicorn 时正导出了
   `DEER_FLOW_AUTH_DISABLED=1`（`tests/host-acceptance/v2/hosts/deerflow.sh:89/217/447`）。同一套装饰器
   也守着我们早已在用的 `POST /runs`（`require_permission("runs","create", require_existing=True)`）——
   既然投递能成，回读同权。**若某部署开了鉴权**，我们的 POST 会先拿到 401/403 而 raise，与回读无关；
   那种情况下回读的 401/403 已被算作 `landing-unverified`，不会把信卡死。
2. **状态字表对齐**：宿主 `RunStatus`（`backend/packages/harness/deerflow/runtime/runs/schemas.py:17`）只有
   `pending | running | success | error | timeout | interrupted`。我最初只判了 `failed/error/cancelled`，
   漏了 `timeout` 与 `interrupted`——已补，并加 4 条用例（`timeout`/`interrupted`/401/403）。

**验证**：`python -m pytest tests/sdk/test_deerflow_plugin.py -q` ⇒ 18 passed；
`python -m pytest tests/sdk -q` ⇒ 540 passed；`check-contract-single-source` / `check-docs-consistency` /
`check-zero-bridge` 全 PASS。契约附 E 的 deerflow 行同步为这套判据。

**仍待质检做的**：L2 重跑 `s7`（需要活着的 deerflow 网关；本次容器里 uvicorn 已停，无法就地发 HTTP 复验），
期望 deerflow 四条 PASS，且 `s7` 的 C17 证据里能看到回读请求。
