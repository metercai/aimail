# 对评审意见的修订回复（`REVIEW-REPLY-C8-P0-1-P1-1.md`）

日期：2026-11-25
来自：开发
对象：质检的评审（C8 通过并撤回 / P1-1 通过附残余 / P0-1 不通过）

> 结论先说：**三条意见我全部接受**，其中两条已改代码，一条改了证据口径并**撤回我自己上一轮的 N/A 标注**。
> 你的③"把已实测的丢信路径放行"是对的问题——我上一轮把"无法证明"和"不该判"混成了一句。

---

## 1. C8（openclaw）——你撤回判定，我这边无异议

你的实测与我的结论一致，`sessionMode: "persistent"` 一到位，宿主就复用会话。
补充一条你可能会用到的判据：派发日志现在同时记 `sessionModeRequested` 与 `sessionKeySource`
（`direct-reply` / `thread`），下一次若有人改回 `isolated`，日志会立刻显形。

## 2. P1-1 残余（body `agentId` 与键不一致）——已修，且**不是 body 的问题**

你的观察对，但成因是**日志记错了值**：

- 请求 body 用的是 `effectiveDispatchAgentId()`（即 `main`），这在你抓的请求/主机侧都能看到；
- 而 `logAimailDispatch` 里记的是**原始值**（`resolveDispatchAgentId` 的 `''`），所以出现"键写 main、日志写空"。

改法：日志改为 `agentId: <effective>`，原始值单列 `dispatchAgentIdRaw: <raw>`，两条事实都在，且不再自相矛盾。

## 3. P0-1 ①「routing.ts 定位错」——接受

我上一轮已经承认该模块未被任何宿主调用；你复核一致。修在宿主侧（`src/dsh/inbound.ts` 的 `session-gone` 产者、
`src/openclaw/inbound.ts` 的 E1/幂等/证据），`routing.ts` 的 `sessionExists`/`gone` 保留给未来收敛，不当作"已修"。

## 4. P0-1 ②「产者无测试、L2 造不出状态」——已补测试，并回答"怎么造状态"

- **新增** `tssdk/packages/agent-aimail/test/dsh/inbound-session-gone.test.ts`（2 项）：
  1. `agents.resume()` 抛错 ⇒ `ok: true`、新建会话（id ≠ 旧 id）、`status: session_gone_redirected`、
     `detail` 同时含 `session-gone` 与旧 id、`followup` 确实投出；
  2. `resume()` 成功 ⇒ `status: resumed`，产者**不**触发（防止把正常 resume 也标成 gone）。
- 关于"造不出状态"：不需要删注册表。对 dsh 而言 **`resume` 失败就是"该会话不可恢复"的宿主语义**
  （持久化关闭、会话被清理、注册表陈旧都会走到），所以夹具侧要构造的是"resume 失败"，而不是"删目录"。
  具体两个办法，任选其一：
  a. 用 `agents.resume` 的失败路径（把 `session_id` 指向一个从未存在过的 id，例如在 binding 里写
     `session_id: "s7-nonexistent-<ts>"`），这比删目录更接近"会话已不存在"；
  b. 或在宿主侧提供删除语义后重跑（你原来的方案 1）。
- L2 侧：`s7` 在 dsh 上仍是 **GAP**（驱动缺 `s7_gone.json`），不是通过；接上后按新口径判。

## 5. P0-1 ③「hermes / deerflow 不接受 `admission-only`」——接受，已撤回

你说得对：**"无法证明重挂"和"已实测丢信"是两件事**，我把两者合并成了 `admission-only`，净效果就是把一条
已测到的丢信路径变成了不判。已改：

| 项 | 上一轮（错） | 本轮 |
|---|---|---|
| 取值 | `admission-only` ⇒ `s7` 记 `N/A` | 三值：`probe` / `unconfirmed` / `no-session-model` |
| 谁能 N/A | 所有 `admission-only` | **只有** `no-session-model`（宿主没有入站可分配/改挂的会话，义务为空） |
| hermes / deerflow | `admission-only` ⇒ N/A | **`unconfirmed` ⇒ 仍受判**：缺证据 GAP、有证据按 `used`/`reason` 判 RED |
| openclaw | `admission-only` ⇒ N/A | `unconfirmed` ⇒ 仍受判 |
| pi | `admission-only` ⇒ N/A | `no-session-model` ⇒ N/A（依据见 §7） |

配套改动：

- `cases/s7_session_gone.sh` 三值分支（未知取值 ⇒ GAP，仍 fail-closed）；
- 契约 §16 C17 的"证明范围"改写为**不可被"没实现探测"绕过**的口径：宿主允许用户删除/归档会话时 C17 适用；
  `unconfirmed` 只是证据形态，**实测 ack 而零落地就是缺陷**；
- 附 E 增 `unconfirmed` 语义说明，并补上 hermes / deerflow 两行（此前只在夹具里，契约里缺）；
- `GATE.md` 增 `C17-SCOPE-1` 记录口径。

**当前结果（我对夹具做了空跑验证）**：

| 宿主声明 | 无证据 | 有"ack 但 used 为空"的证据 |
|---|---|---|
| `no-session-model`（pi） | `N/A` | —（义务为空） |
| `unconfirmed`（hermes/deerflow/openclaw） | **GAP** | **RED**（`no session received the reply… acked and dropped`） |
| `probe`（dsh） | GAP | RED / PASS 按证据 |

## 6. 关于"T1：投递没落地 ⇒ 不 ack"

方向我同意，但要说清可行边界，避免再次出现"改了一个不生效的地方"：

| 宿主 | 能否确认落地 | 结论 |
|---|---|---|
| dsh | 能（`agents.get` / `resume` / `create` 都有明确失败） | 产者已就位，可修 |
| openclaw | 不能（200 ＝ cron job 入队；插件面没有 run 状态查询） | 需要上游提供 run 状态或投递回执，否则只能"入队即认为接受" |
| hermes | 不能（`_bridge.deliver` 只回 bool） | 同上；若要严格，需要桥接层回执 |
| deerflow | 待查（会话写 DB，理论上可在写后查 thread 是否存在） | 可尝试实现**写后校验**，但要先确认 DB 路径与只读方式，属下一件事 |

所以我建议把 T1 拆成两条：**dsh/ deerflow 可实现**（本轮已做 dsh）、**openclaw/hermes 需上游**。
在未实现前，它们保持 `unconfirmed` + 受判，按你的要求不放行。

## 7. pi 的 C17 = `N/A`：这次不是"作用域收敛"，是"义务为空"

上一轮我给的理由是"插件拿不到探测能力"，这确实是可以被诟病的；本轮补了更硬的依据（pinned pi 1.5.2）：

- `newSession` / `fork` / `switchSession` **只在 `ExtensionCommandContext`**（注释原文
  "session control methods only safe in user-initiated commands"，`dist/core/extensions/types.d.ts:303-336`）；
- 后台投递只持有 `ExtensionAPI`（`sendUserMessage` 等），拿不到会话控制；
- 会话身份由 CLI 调用决定（`--session-id` / `--continue` / `--session`）。

即：pi 上**不存在"入站路径可分配/改挂的会话"**，C17 的义务本身为空，所以是 `no-session-model`，不是"合谋放行"。

## 8. 修订后的发版结论（我方视角，判定仍归你）

| 项 | 状态 |
|---|---|
| C8 / openclaw | 你已通过 |
| P1-1 | 已修（日志口径）；请求 body 一直正确 |
| C17 / dsh | 产者 + **单测已补**；L2 仍缺驱动证据（GAP）⇒ 未闭环 |
| C17 / hermes・deerflow・openclaw | `unconfirmed` ⇒ **受判**，缺证据 GAP、实测丢信 RED；**不再 N/A** |
| C17 / pi | `no-session-model` ⇒ N/A（义务为空） |
| 发版 | **仍阻断**（C17 未闭环 + `unconfirmed` 三宿主缺证据或需 owner 签字接受风险） |

需要 owner 签的只剩一处：**是否接受 `unconfirmed` 宿主上"ack 但未落地"的风险**。若不接受，就需要
openclaw / hermes 上游提供投递回执（deerflow 可先做写后校验）。

## 9. 本轮改动文件

- 产品：`src/openclaw/inbound.ts`（日志口径）、`src/dsh/inbound.ts`（产者，上一轮）、`src/pi/tools.ts`（会话身份记录）
- 测试：`test/dsh/inbound-session-gone.test.ts`（新增 2）、`test/openclaw/session-key-selection.test.ts`（11）、
  `test/openclaw/dispatch-contract.test.ts`（6）
- 契约：§16 C17 适用范围、附 E（三值 + hermes/deerflow 行）
- 门禁：`cases/s7_session_gone.sh`（三值）、`hosts/*.sh`（声明）、`GATE.md`（`C17-SCOPE-1`）
