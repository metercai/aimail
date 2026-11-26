# L2 三缺陷查验结论与修复计划

- 日期：2026-11-25 · 对象：`docs/qa/HANDOFF-L2-THREE-DEFECTS.md`
- 坐标：SDK `aaa1a3d`（工作树未提交）· gateway `ac30461`
- 结论速览：**缺陷 1 证实（产品，P0）**、**缺陷 2 部分证伪（夹具判据错 + 产品缺可观测标记，P1）**、**缺陷 3 证实（产品，P1，且同源缺陷连带 openclaw）**

| # | 质检定性 | 查验结论 | 责任面 | 严重度 |
|---|---|---|---|---|
| 1 | dsh 锚点绑定下零投递 | **证实**，且**不止 dsh**：pi/openclaw 走在同一个不带头的构造点，只因缺陷 3 未登记锚点才没红 | SDK（共用构造点 + dsh hub） | P0 |
| 2 | deerflow ack 后无线程接收 | **证伪"没重建/没落地"**；真问题是夹具把"新 id"当落地判据 + 产品没有 `session-gone` 可观测标记 | 夹具 + 产品打点 | P1 |
| 3 | pi 无 `address_anchor` 行 | **证实**，根因不是 `/apply` 分支，而是 SDK 工具包装器丢字段 | SDK（pi、openclaw） | P1 |

---

## 缺陷 1（证实）：dsh 零投递 = 拉取请求没带锚点头

### 证据链
1. 网关对**已登记锚点**的 AgentSystemId，要求请求头里出现同一 hash；缺头即 403 —— [http.rs](/home/ubuntu/aimail-gateway/src/core/api/http.rs:3453)：
   `list_agent_system_anchors` 非空 → 逐个地址比对 `parse_anchor_claims(headers)` → 不匹配记入 `bad` → 403 `anchor_mismatch`。
   （所以 A/B 里 `DELETE FROM address_anchor` 就恢复：没有登记就不进入强制分支。这与质检观察完全一致。）
2. SDK 只有一处注入锚点头：[poll-entry.ts](/home/ubuntu/aimail/tssdk/packages/agent-aimail/src/core/poll-entry.ts:111) 的 `buildAgentSystemClient()`。
3. **dsh 的出货路径不走它**：[mail-hub.ts](/home/ubuntu/aimail/tssdk/packages/agent-aimail/src/dsh/mail-hub.ts:270) 的 `startStream()` 自建 `GatewayClient`，`extraHeaders` 只有 `X-Agent-System-Id`；[mail-hub.ts](/home/ubuntu/aimail/tssdk/packages/agent-aimail/src/dsh/mail-hub.ts:416) 的 `drain()` 直接 `new GatewayClient(...)`，一个自定义头都没有。
   ⇒ 激活登记了锚点之后，SSE 流 403（`streamRound` 返回 `{auth:403}`，被当成普通失败退避），`drain` 的 `pullList` 403 后 `return` 静默返回 ⇒ 一封不取、零日志。质检说的"403 不写日志别被误导"是对的，我这边两头都静默。

### 范围修正：三条构造点，pi/openclaw 是"潜在同款"，只是被缺陷 3 掩盖了

`X-Aimail-Anchor` 只在**一个**聚合 client 构造点存在。全部拉取构造点清点如下：

| # | 构造点 | 带头？ | 谁在用 |
|---|---|---|---|
| 1 | `poll-entry.ts:112 buildAgentSystemClient` | ✅ | `startAgentSystemGroups`（无指针 pool 聚合） |
| 2 | `system-pull.ts:63 startSystemPull` | ❌ 只有 `X-Agent-System-Id` | **pi/index.ts:159**（admin_key 分支）、**pi/index.ts:253**（正常分支）、**openclaw/inbound.ts:482/567** |
| 3 | `dsh/mail-hub.ts:270 / :416` | ❌ | dsh 的 SSE 与 drain |
| 4 | `poll-entry.ts:497`（V1 逐地址） | ❌（正确：V1 无锚点行） | V1 兼容 |

所以：
- **dsh 红**：它登记了锚点行（bind 走 core 直传 `workspacePath`），且自建 hub 不带头 ⇒ 立刻 403。
- **pi/openclaw 绿**：它们也走在不带头的构造点 2 上，但**锚点行压根没写进去**（缺陷 3 的驼峰丢字段），网关走"登记为空 ⇒ 放行"分支 ⇒ 没触发。**修好缺陷 3 的当天，pi 与 openclaw 会立刻变成和 dsh 一样的零投递。** 这不是"它们没事"，是"它们还没被强制"。
- **hermes/deerflow 真没事**：Python 侧 `system_pull.py` 统一走 `build_agent_system_client`，它带 `_anchor_headers`（[pull_client.py](/home/ubuntu/aimail/pysdk/v2/pull_client.py:58)）。

结论：P0 的修法不能只补 dsh 的 hub，必须先堵构造点 2 —— 否则修完缺陷 3 立刻复发。

### 质检文档的一处笔误（必须先对齐，否则修了也不通）
质检写头名 **`X-Aimail-Anchors`（复数）**；已发布网关源码是 **`x-aimail-anchor`（单数）**，一个 AgentSystemId 的多地址用**重复同名头**承载：
```
src/core/api/http.rs:3422   for value in headers.get_all("x-aimail-anchor").iter()
```
若按复数实现，网关读不到 ⇒ 照样 403。**以单数为准**（SDK `ANCHOR_HEADER='X-Aimail-Anchor'` 本来就是单数，无需改）。建议质检同步更正该文档第 33 行。

### 修法（三处：共用构造点 + dsh hub + 缺陷 3 的字段）
0. **`startSystemPull` 自己解析成员并注入锚点头**（不靠调用方传参，否则下一个人又会忘）：`agentSystemId` 存在时，从 core 现有的 `listSystemDirs()/listAgentConfigs()` 里取该 `(agent_system_id, gateway_url)` 组的全部活跃绑定，走 `anchorHeader(members)`；`poll-entry.ts:112` 的 `buildAgentSystemClient` 抽成唯一实现，`startSystemPull` 复用它。这一步同时修掉 pi 与 openclaw（它们的 4 个调用点不用改）。
1. `reconcile()`：`v2ByGateway` 从 `Map<gw, binding>` 改成 `Map<gw, binding[]>`，聚合该 AgentSystemId 在该网关下的**全部**活跃绑定；`startStream` 增加 `members` 入参。
2. `startStream()`：`agentSystemId` 分支改用 `buildAgentSystemClient(gatewayUrl, apiKey, agentSystemId, identity, members)`（唯一构造点，杜绝 B2 类第二处自建）；V1 逐地址分支保留 `GatewayClient`，但补 `extraHeaders: { 'X-Aimail-Anchor': anchorHeader([b]) }`。
3. `drain()`：同上补 `anchorHeader([found.cfg])`。
4. **可见日志**（质检要求）：`streamRound` 返回 `{auth:403}` 与 `pullList` 返回 `status===403` 时各打一行，例如
   `[agent-aimail] anchor refused by gateway for <email>: the address is registered to a different anchor — re-activate (contract §13.3)`；
   并把该行同时写进 `lastError`，让 `/status` 能看见（现在是 `''`）。

### 验收
- `dsh s1 s2 s3` PASS，`observed max` 不再 `final>0`；`s6 s7` 恢复可判。
- 负例：把绑定 `workspace_path` 改成别的目录后拉取必须 403 且打出上面那行日志；`DELETE FROM address_anchor` 后恢复投递（质检已有的 A/B 探针 `AIMAIL_L2_DROP_ANCHOR`）。
- 单测：`dsh/mail-hub` 的 client 构造断言 `X-Aimail-Anchor` 值包含本 AgentSystemId 全部地址（新增 `test/dsh/mail-hub-anchor-header.test.ts`）；`system-pull` 的锚点头断言（新增 `test/core/system-pull-anchor-header.test.ts`）。
- **pi/openclaw 回填验证**：修完缺陷 3（有锚点行）后，`pi s1 s2 s3`、`openclaw s1 s3` 必须仍然 PASS —— 这是"构造点 2 已堵住"的证明；若它们此时转红，说明只补了 dsh。

---

## 缺陷 2（部分证伪）：deerflow 其实落地了，红的是夹具判据与"可观测标记"

### 证据链（同一份 evidence）
`evidence/l2/20261125T140246Z-deerflow-x/host.log`：
```
14:04:19 run c83917e5 accepted … (thread a078ccb0-58f0-5f5f-bcb4-91fcbe2991a6)   ← s6 首投
14:05:04 run 179b3b41 accepted … (thread a078ccb0-58f0-5f5f-bcb4-91fcbe2991a6)   ← s6 回信，同 thread
14:05:34 run 747a5fbb accepted … (thread 13cdf970-93fb-5d1c-a881-bcff9e61a322)   ← s6 新 thread（root 变了，符合 E3）
14:06:04 run 97cd5dc0 accepted … (thread a078ccb0-58f0-5f5f-bcb4-91fcbe2991a6)   ← s7 删掉线程后的回信
```
最后一行就是 s7：**回信落回了 root 推导出的同一个 thread id（`a078ccb0`，即被删掉的那个）**，重建发生了、也落地了。

那为什么 `used=''`？夹具把"落地"算成**快照 7 里新增的 id**（[deerflow.sh](/home/ubuntu/aimail/tests/host-acceptance/v2/hosts/deerflow.sh:388) `new = k7 - k6`）。重建出的 id 与被删的 id 相同 ⇒ 它出现在 k6 里 ⇒ 永远不在 `new` 里 ⇒ `used=''` ⇒ `root_ok=False`。这是判据构造性失败，不是丢信。
`reason` 更直接：[deerflow.sh](/home/ubuntu/aimail/tests/host-acceptance/v2/hosts/deerflow.sh:390) 里 `"reason": ""` 是**硬编码空串**，无论产品怎么打点都不可能通过。

### 但产品侧确实缺一块 —— 不作为 / 不谎报
- 声明是 `HOST_DELIVERY_CONFIRMATION=probe`，可 `session-gone` 这个降级事实在 deerflow 上**没有任何可观测输出**：它每次投递都无条件 `POST /api/threads`（幂等），从来不知道"线程曾经不在"。所以质检说"reason 不名 session-gone"在产品层面是真的缺，不是夹具单方面的问题。
- 另外，当前代码在回读 404 时按 `landing-unverified` 放行（[runtime.py](/home/ubuntu/aimail/pysdk/deerflow_plugin/runtime.py:215)）。这次恰好是 200 落地，但这个口子会把"ack 了但没落地"变成静默绿，属于同类风险。

### 修法
**产品（pysdk deerflow runtime）**
1. 投递前预检线程存在性：`GET /api/threads/<tid>`（宿主有该路由，[threads.py](/home/ubuntu/deer-flow/backend/app/gateway/routers/threads.py:1278)）。
   - 404 ⇒ 记 `session_gone` 标记，随后走现有 `POST /api/threads`（`create_thread` 幂等，id 相同 ⇒ 血缘天然保留）再投 run；
   - 打一行可被夹具解析的日志：`[aimail-deerflow] session-gone: thread <tid> is gone (root <root>) — re-attached in place (contract §9.3)`；
   - 401/403 ⇒ 与现有读回一致，不据此改判（测试口令下读回可能越权）。
2. 回读 404 的处理收窄：**只有**"线程预检/回读两条路由都不可用"才允许 `landing-unverified` 放行；线程存在性已确认为 404 的，必须重建成功且 run 可读，否则抛错不 ack。
3. `_landing_verification_enabled()` 继续管开关，但 `session-gone` 打点与开关解耦（它不需要读回权限）。

**夹具（tests/host-acceptance/v2，我会一并改，供质检复跑）**
1. `used` 改成"落地判定"：快照 7 里**被删的 id 重新出现**（`victim ∈ k7`）且该 thread 的 run 数 > 快照 6 时记 `used=victim`；不再用 `k7-k6`。
2. `root_ok` 改成 `used == victim`（root 推导 id 不变 ⇒ 血缘未丢）；`landed_in_existing` 真值化。
3. `reason` 从宿主日志里抓 `session-gone` 标记，不再写死 `""`；抓不到就如实红（并说明"宿主无该标记"）。
4. 去掉 `case` 里"deterministic-thread 必须出现新 id"的残留分支（第 88-99 行已部分改过，但 `used` 语义没跟上，导致仍然红）。

### 验收
`deerflow s7` 四条：acked / 落地（victim id 重现且有新 run）/ `reason=session-gone` / root 未变。

---

## 缺陷 3（证实）：pi 没有锚点行 = 工具包装器丢了 `workspacePath`

### 回答质检的提问
不是宿主差异，也不是 `/apply` 漏了 `set_address_anchor`。pi 走的就是同一个 `activate-address-code`：
```
pi/tools.ts:80      aimail_address(bind) → 注入 { workspacePath: ctx.cwd || process.cwd() }   ← 驼峰
core/tools.ts:112   aimailAddressTool → 只读 args.workspace_path（下划线）→ 转发 { workspacePath }
                    ⇒ 驼峰被丢掉 ⇒ aimailAddress 收到的 workspacePath = undefined
address-code.ts:263 anchoredWorkspace 为空 ⇒ 请求体里没有 anchor_id ⇒ 网关没东西可存
```
dsh / hermes / deerflow 直接调 core（驼峰），所以有行；pi 与 **openclaw** 走工具包装器（[openclaw/tools.ts](/home/ubuntu/aimail/tssdk/packages/agent-aimail/src/openclaw/tools.ts:54) 同样注入驼峰）⇒ 两个宿主都静默无锚点。openclaw 上一轮没进 `--only` 名单，所以没暴露。

### 修法
1. `AimailAddressToolArgs` 增加驼峰别名 `workspacePath?`（与既有 `gatewayUrl`/`profileHome` 同款"程序化别名，不进 agent schema"），`aimailAddressTool` 转发 `args.workspace_path ?? args.workspacePath`。
2. **拒绝静默**：`aimailAddress({action:'bind'})` 在绑定时如果既没有锚点又没有 `anchor` 声明，打一行 warn（`binding without an anchor — the gateway cannot enforce §7.1 for <email>`），让"没触发"在日志里可见，而不是伪装成绿。
3. 回归测试（`test/core/aimail-address-anchor.test.ts`）：驼峰注入 ⇒ 激活请求体含 `anchor_id`；并断言 `address_anchor` 侧调用的 hash 等于 `anchorId(email, anchor)`。

### 验收
`pi s1` 后 `address_anchor` 有 `l2pi@test.local` 一行；pi 的每次拉取带正确头（正例 200）；openclaw 同样补上（下一轮纳入 `--only`）。

---

## pi 的锚点计算方法（owner 要求：以整个 agent 为锚点）

### 规则
```
anchor(host) = <该 agent 拥有的、稳定的绝对目录>   // 只取路径字符串，不取内容、不取时间
anchor_id    = sha256(lower(email) + "\0" + anchor)          // 不变，网关只比这一个值
X-Aimail-Anchor: <email>;<anchor_id>[,<email2>;<id2>…]       // 单数头，可重复/逗号分隔
```
硬性约束（写进契约 §7.1 附录 + §13.3）：
- **MUST 稳定**：同一 agent 换目录以外的一切变化（新会话、新进程、重启容器外的 cwd、切分支）都不得改变锚点；
- **MUST NOT 用**：`cwd`、会话 id、会话文件路径、`$PWD`、临时目录、时间戳；
- **MUST 取 realpath**：`~/x` 与 `/home/u/x` 共用一个值；符号链接不得造成两个 hash；
- 锚点在**激活时**登记，之后改锚点 = 必须重新激活（`move` 返回 `re-activation-required`）。

### 每宿主取值表（建议值）

| 宿主 | 锚点字符串 | 为什么是它 |
|---|---|---|
| dsh | `realpath(binding.workspace_path)` | 地址绑定的就是那个 workspace；E1/C16 的隔离维度天然一致 |
| openclaw | `realpath(agent workspace)` | 同上，agent 的 workspace 是其身份 |
| **pi** | `realpath($HOME) + "/.pi/agent"` | **整个 agent**：SOUL、skills、sessions、配置全在这一个目录下；pi 单地址、无 workspace，用 agent 目录代表"这个 agent" |
| hermes | `realpath(HERMES_HOME)`（= `~/.hermes`） | profile 就是宿主级身份 |
| deerflow | `realpath(DEER_FLOW_HOME \|\| /app)` | 宿主 home |

### pi 的落地方式（具体到代码）
```ts
// pi/identity.ts —— 唯一计算点，禁止各处自行拼路径
export function piAnchor(): string {
  // realpath($HOME) 先规范化（容器里可能是 /root、宿主上可能是 /home/u），
  // 再拼 agent 目录：无论 ~/.pi/agent 是否已存在，结果都相同 ⇒ 激活与拉取两侧必然一致。
  let home = os.homedir()
  try { home = fs.realpathSync(home) } catch { /* 保留原值 */ }
  return path.join(home, '.pi', 'agent')
}
```
- 注入点：[pi/tools.ts](/home/ubuntu/aimail/tssdk/packages/agent-aimail/src/pi/tools.ts:80) 把 `workspacePath: ctx.cwd || process.cwd()` **改成** `workspacePath: piAnchor()`；
- 这样写进绑定的 `workspace_path` 就是 `…/.pi/agent`，与 `setAgentHomeResolver(() => ~/.pi)` 一致；pi 没有 workspace 路由（投递按会话键），该字段不被当作真实 workspace 使用（已核：[pi/index.ts](/home/ubuntu/aimail/tssdk/packages/agent-aimail/src/pi/index.ts:360) 用的是 agentHomeResolver，`runtime/routing.ts` 的 `spacePath` 来自会话 meta，pi 不走这条）；
- 为什么**不**给 `workspace_path` 加 `agent:` 前缀：绑定字段被 dsh/hermes 的投递路径当作真实路径使用，加前缀会把"锚点"和"工作目录"混成一个概念。前缀法需要新增第 8 个绑定字段，未发版虽可改，但收益只是"看起来更清楚"，代价是两侧镜像、7 字段校验、3 个宿主的写入点全动 —— 不值当。
- 迁移：未发版 ⇒ 无兼容包袱，直接把 cwd 锚点换掉；L2 已登记的 `address_anchor` 行是测试数据，清表重跑即可（质检的 `AIMAIL_L2_DROP_ANCHOR` 探针正好复用）。

### 由此还须改的一处：pi 的"单地址 + 无 workspace"不等于"无锚点"
C17/C8 记 N/A 的口径不变（pi 不能分配会话），但 **锚点强制在 pi 上是生效的**（正例 200）。验收时 pi 的绿必须来自"锚点带上了且网关比对了"，而不是"表里没行所以放行"。

---

## 执行顺序与验证

1. P0 缺陷 1（`startSystemPull` 共用构造点补锚点头 + dsh hub + 403 日志；pi/openclaw 顺带修好）→ `tsc --noEmit`、`tssdk vitest`
2. P1 缺陷 3（包装器驼峰 + pi 锚点 = agent 目录 + openclaw 同修）→ 回归测试
3. P1 缺陷 2（deerflow 预检打点 + 回读收窄；夹具 `used/root_ok/reason` 三处）
4. 全量：`tests/sdk/l0-gate-tests.sh`、`pytest tests/sdk -q`、`tssdk vitest`、gateway `cargo test --lib`
5. 交质检：`GW_BIN=... bash tests/host-acceptance/v2/run-release-gate.sh --only dsh,deerflow,pi,openclaw`

## 待确认（不阻塞上述修复）
- 质检文档第 33 行头名笔误（复数 → 单数）：请同步更正，避免下一个人按错的实现。
- ~~网关"未登记锚点放行"是过渡态~~ → **owner 已拍（2026-11-25）：改成硬性**，见下节"v2.5 强制锚点"。
- PI-Desktop 面（`./desktop`）用设置里的 key，不发锚点头；若要与裸 pi 一致，需要"项目路径 → 锚点"的定义，另议。

---

## 实施记录（2026-11-25 执行完毕）

### 改了什么

| # | 文件 | 改动 |
|---|---|---|
| P0 | `core/agent-system-client.ts`（新） | 唯一聚合 client 构造点：`buildAgentSystemClient`（ASID + 锚点声明）+ `resolveAgentSystemMembers`（按 `(asid, gateway)` 从绑定库解析本组成员）+ `isV2AgentScopeBinding`（从 poll-entry 迁入）。`startSystemPull` 不再依赖调用方传 `members` |
| P0 | `core/system-pull.ts` | 用上面的构造点；新增 `members?`/`fetchImpl?`（后者是传输缝，宿主没有全局 fetch 时可用）；调用方漏传时自己解析 |
| P0 | `core/poll-entry.ts` | 构造函数与谓词改为 re-export（名字与导出面不变） |
| P0 | `dsh/mail-hub.ts` | `reconcile` 改成 `Map<gw, AgentConfig[]>` 并整组下发；SSE 走 `buildAgentSystemClient`；V1/`drain` 走 `perAddressClient`（也带锚点声明）；`streamRound` 的 `{auth}` 与 `pullList` 的 401/403 各打一行 `authFailureLine`（403 = `anchor refused by gateway … re-activate (contract §13.3)`）并写入 `lastError` |
| P1-3 | `core/tools.ts` | `AimailAddressToolArgs` 增加驼峰别名 `workspacePath`，转发 `workspace_path ?? workspacePath` |
| P1-3 | `pi/identity.ts` / `pi/tools.ts` | 新增 `piAnchor()`（`realpath($HOME)/.pi/agent`）；bind 注入它；绑定后的回填也用它 |
| P1-2 | `deerflow_plugin/runtime.py` | 新增 `_thread_presence`（200=present / 404 且 detail 点名 thread=gone / 其余 unknown）与 `_detail`；投递前预检 → `gone` 就打 `session-gone: thread … re-attaching in place (reason=session-gone)` 再原地重建；回读 404 若点名 run ⇒ 抛错不 ack，未点名（无该路由）⇒ 仍按 `landing-unverified` 放行并留日志 |
| 夹具 | `hosts/deerflow.sh` | `s7` 判据改为"重挂落地"：`used = victim ∈ k7`、`root_ok = used == victim`、`reason` 从宿主日志抓 `session-gone`（不再硬编码 `""`）；落盘 `s7_hostlog.txt` 供抓取 |

### 实施中新发现的两个连带缺陷（同源，已一并修）

1. **激活没有持久化锚点值**：`activateAddressCodePersist` 把 `anchor_id` 发给了网关，却**不把锚点写进绑定**（`workspace_path` 只在宿主回填时才出现）⇒ 拉取侧算不出同一个 hash，登记完立刻 403。现在激活时把"参与哈希的那个值"一并写入（一个值、一个写者）。这也是 pi/openclaw 之前"表里有行也未必能拉"的隐患。
2. **pi 用 cwd 回填 workspace**：`pinWorkspaceToBinding(..., {id: cwd, path: cwd})` 会把锚点覆盖成会话 cwd，与 owner 的"pi 以整个 agent 为锚点"及已登记 hash 冲突。改成回填 `piAnchor()`（fill-only，若激活已写入则为 no-op）。

### 验证（本轮实跑）

```
tssdk  tsc --noEmit                                        clean
       vitest run                                          517 passed / 61 files   (510 → +7)
aimail pytest tests/sdk -q                                 441 passed              (438 → +3)
       tests/sdk/l0-gate-tests.sh                          PASS — all gates green
```

新增回归：`test/core/system-pull-anchor-header.test.ts`(4)、`test/dsh/mail-hub-anchor-header.test.ts`(1)、`test/core/aimail-address-anchor.test.ts`(2)、`tests/sdk/test_deerflow_plugin.py`(+3)。

### 还没做的（交质检）

- **L2 全量复跑**（开发未跑）：`GW_BIN=… bash tests/host-acceptance/v2/run-release-gate.sh --only dsh,deerflow,pi,openclaw`。
  期望：dsh `s1 s2 s3` 恢复 PASS（不再 `final>0`）；`s6 s7 s8` 可判；deerflow `s7` 四条全绿（落地 + `reason=session-gone` + root 不变）；pi/openclaw 在**有锚点行**的前提下仍然 PASS（这条同时证明"共用的构造点真的带上了头"）。
- 质检文档第 33 行的头名笔误（`X-Aimail-Anchors` → `X-Aimail-Anchor`）需要更正，否则下一个人会按错的实现。
- ~~是否收紧为"未登记即拒"~~ → owner 已拍：**已收紧**（v2.5，见下节），L2 需要按新语义重跑：删掉锚点行的 A/B 探针现在得到 `anchor_unregistered` 而不是"恢复投递"。

---

## v2.5 强制锚点（owner 2026-11-25 拍定，已实现并提交：gateway `v1.0.18 @ e8b6b42`）

**决定**：网关拉邮件时**强制**带锚点；没有生产部署 ⇒ 不存在兼容包袱 ⇒ 不留"未登记就放行"这一档。

### 网关（`aimail-gateway@ac30461` 之后）
`enforce_anchor_binding` 改成三段式硬拒，仍挂在三个聚合端点（stream / pending / ack），**先于载荷构造**执行：

| 403 code | 触发 | 运维要做什么 |
|---|---|---|
| `anchor_required` | 请求完全没带 `X-Aimail-Anchor` 声明 | 客户端/SDK 未上报锚点（或绑定没有锚点值）→ 重新激活 |
| `anchor_unregistered` | 该 AgentSystemId 名下某地址**从未登记锚点**（激活没带 `anchor_id`） | 重新激活该地址（带锚点） |
| `anchor_mismatch` | 声明与登记值不一致，或漏了该组某个地址 | 绑定被改过 → 重新激活 |

实现细节：地址集合来自 `agent_system_addresses`（所以"没登记锚点"能被发现，而不是只看 `address_anchor`）；`addresses` 为空 ⇒ 该组没有地址、不可能有邮件 ⇒ 放行。载荷不变：`group_pending_batches(&records)` 的入参只有 DB 记录，`enforce` 的返回值只有 `Ok(())` 或 403 ⇒ **形状与不带锚点时逐字节一致**（回答质检"是否同一形状"：是）。

测试：`anchor_enforcement_tests`（5 项：无声明 / 未登记 / 不匹配 / 漏地址 / 全proven 放行）+ 既有 `anchor_header_tests`(3) + `storage::anchor_tests`(3)。`cargo test --lib` **438 passed**，fmt/clippy clean。

### SDK 侧同轮补齐
- `StreamRoundResult` 的 auth 分支带 `detail`（网关对 401/403 不写日志，错误码只能在客户端日志里看见）；`gatewayErrorCode()` 只保留 `[A-Za-z0-9_.-]` 的码，不带正文/地址/hash。
- dsh 的 `authFailureLine` 按三种码给不同提示（"没带声明" ≠ "没登记锚点" ≠ "锚点不符"）。
- Python `start_system_pull(members=None)` 自己解析本组绑定（与 tssdk `startSystemPull` 同形），避免调用方漏传导致 `anchor_required`。

### 语义变化（质检复跑须知）
1. 删掉 `address_anchor` 行的 A/B 探针，现在得到的是 **`anchor_unregistered`**（而不是"恢复投递"）——这仍然证明"锚点行是变量"，但方向变了：现在两边都拒绝，区别在 reason。
2. 任何**激活时没带锚点**的绑定，其所在组整体拉不到信（不是只影响自己）。L2 四个宿主都已带锚点，预期全绿。
3. per-address `/api/v1/admin/pending`（V1 路径）**未**加这条强制；它随后 v1 退场一起消失。dsh 的 `drain` 走这条，已经带头，但网关目前不校验它。

### 性能审查（拉邮件是高频持续行为）

**结论：与改动前的基线同价（一次索引查询），不阻塞 reactor，不持有任何跨查询的锁。** 审查中发现并修掉了自己引入的两处损耗：

| 问题 | 处置 |
|---|---|
| 第一版 `enforce` 打了**两条** SQL（`list_agent_system_addresses` + `list_agent_system_anchors`），把每次拉取的 DB 往返从 1 次变成 2 次 | 合成**一条** `LEFT JOIN`：`list_agent_system_address_anchors()` 一次返回 `(address, Option<anchor>)`，`unregistered`/`mismatch` 在一趟里分类。旧函数改为它的薄过滤，避免两条 SQL 漂移 |
| 分类循环按地址 `to_lowercase()`（每地址 2 次分配），200 地址组实测 0.8ms | 改成一个复用 `String` 缓冲 + `make_ascii_lowercase()`（就地、零新增分配）；解析侧已经 lower，DB 侧写入也已 lower |
| `unwrap_or_default()` 让 DB 出错时**放行**（fail-open，与 v2.5 硬性相悖） | 改成 fail-closed：DB 读不出见证 ⇒ 500，不放行（这条与性能无关，是严格性漏洞，顺手堵了） |

**实测（`cargo test --release`，同一台机器；debug 数字不作数）**

| 场景 | 耗时 | 说明 |
|---|---|---|
| `enforce`，1 地址组（真实规模） | **32.9 µs / 次** | 其中 33.3 µs 就是那条索引查询本身的耗时 ⇒ 解析+分类在噪声内 |
| 同组只跑查询（对照） | 33.3 µs / 次 | 改动前后相等：一次 `agent_system_addresses(agent_system_id)` 索引扫描 + `address_anchor` 主键 JOIN |
| 融合查询 vs 旧 JOIN 查询（200 地址组） | 345 µs vs 346 µs | 融合没有变慢 |
| `parse_anchor_claims`，200 条声明（极端） | 82 µs | 真实场景是 1 个 header 值含 1–3 条声明（客户端 `anchorHeader()` 用逗号拼一个值），约 1–2 µs |

**缓存（owner 2026-11-25 三轮纠正后的最终形态）**

我最初"不缓存"的判断是错的，随后"全量 clear"的形状也是错的，现在按 owner 的三条意见收敛成：

- **结构**：`anchor_cache: Arc<RwLock<HashMap<asid, (seq, Arc<Vec<(address, Option<anchor>)>>)>>>`。命中 = 一次 `RwLock` 读 + `Arc` 克隆，不碰连接池；
- **写侧是 PATCH，不是 drop**：激活/换锚点/解绑只改动**那一个地址的槽位**（`Arc::make_mut`），该组的条目保持温热 ⇒ **激活之后的下一次拉取仍然是命中**；
- **新增地址必须进集合**（这条是纠正 owner 假设的地方）：严格规则要求"每个已绑定地址都有锚点"，所以给一个已存在的组新增第二个地址**确实改变了该组要证明的集合**——如果忽略，网关就会把 `new` 的邮件交给一个还没锚定的地址，正是我们把"未登记放行"删掉要堵的洞。正确做法不是忽略、也不是丢弃整个条目，而是 **patch 进去（值为 None）**，随后 `set_address_anchor` 再 patch 成 hash；
- **解绑**：patch 移除该地址；条目若与 DB 不一致（找不到该地址）则丢弃该条目（一次回源）作为安全阀；
- **退出机制（owner 要求）**：`ANCHOR_CACHE_MAX = 4096`，插入时若超限淘汰**最老的插入**（近似 LRU——热路径不取写锁，所以命中不刷新时间戳）；组的内存量级 ≈ 每地址 150–200 B，3 地址组约 200 B，上限下最坏 < 1 MiB。解绑/注销会 patch 掉地址，条目自然变小；
- **写入频率**：`set_address_anchor` 不比 `bind_agent_system_address` 频繁——两者是激活的相邻两步（`activation.rs:934` bind → `:962` anchor，仅在带 `anchor_id` 时写）；`remove_..._if_unowned` 在吊销路径（`keys.rs:786`）。全部控制面，数据面不写库；三条写路径各多一次**同连接的**主键查询（无额外池往返）。

实测（release）：

| 场景 | 数值 |
|---|---|
| 命中后的拉取 | **1.37 µs / 次** |
| **激活之后立刻拉取**（bind + anchor 刚写完） | **1.56 µs / 次**（若 drop 会变成 ~33 µs 回源） |
| 加缓存前 | 32.9 µs / 次 |
| 冷路径（未命中） | 33 µs / 次（一次索引查询） |
| 200 地址组（极端） | 77.7 µs，瓶颈已是 header 解析 |

正确性回归（6 项）：`cached_witnesses_are_invalidated_by_re_anchoring`、`..._by_binding_and_unbinding`、`invalidation_is_scoped_to_the_affected_agent_system`（`Arc::ptr_eq` 证明无关组不回源）、`activation_patches_a_warm_group_instead_of_dropping_it`（patch 后内容与 DB 一致）、`anchor_cache_is_bounded_and_evicts_the_oldest_entry`、`a_re_anchored_address_is_refused_on_the_next_pull`（缓存命中后改锚点，旧声明必须立刻被拒）。

**校验点分布（重要，别记成"只在建连时"）**：`enforce_anchor_binding` 挂在三个端点——① `GET /agent-system/pending/stream`（**每次建连一次**，含 SSE 轮换/重连）；② `POST /agent-system/pending`（轮询模式下**每一轮一次**）；③ `POST /agent-system/pending/ack`（**每一批 ack 一次**）。连接建立之后、服务端沿已建流**主动推送**邮件的过程**不做任何锚点校验**（`sse_response_scoped` 里没有锚点逻辑）——所以长流维持期是零开销。客户端会在 SSE 与长轮询之间切换（`occupied` / `drop` / `stream stay`），实际频率取决于当前模式；加缓存后这三处都约 1.5 µs。

**并发/阻塞面（关键）**：`Database::call` 是 `tokio::task::spawn_blocking` + r2d2 连接池，SQLite 读在阻塞线程池里跑，**不占 async reactor**；命中缓存时连池子都不碰。`enforce` 在三个端点上都在 `try_acquire(delivery_slot)` **之前**执行，所以不会拉长投递槽的持有时间；冷路径只持有一条连接、一次查询，无跨语句事务、无锁升级（`call` 不是 `call_tx`，不走 `BEGIN IMMEDIATE`）。网络侧没有新增 I/O。

**SDK 侧**：请求头在 client 构造时算一次（`anchorHeader`），**不在每轮拉取里重算**；`resolveAgentSystemMembers` 的目录扫描只在 `startSystemPull` 拉流时发生（arm 时机：进程启动 / binding-created / pause-resume），不在每轮循环里；`streamRound` 仅在 401/403 时多读一次响应体（错误路径，且上限 64 字符的码）。
