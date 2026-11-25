# C16 处置决定：不签 N/A，按"已修 + 可判"收口

日期：2026-11-25 ｜ 状态：**已决定并已实现**（L2 证据待质检用 `s8` 采集）
相关：契约 v2.3 §9.2 I2 / §9.3 / §16 C16；`tests/host-acceptance/v2/GATE.md`

## 1. 决定

| 选项 | 采不采 | 理由 |
|---|---|---|
| 签 `N/A`（"跨锚点不可构造"） | ❌ | 核查证明**可以构造**，而且原来的实现真的会跨锚点投递 |
| 只写"待定/夹具能力不足" | ❌ | owner 口径：已知问题 + 已知修法就必须做 |
| **修 + 给出可判口径 + 一个宿主的真实证据（dsh `s8`）** | ✅ | 缺陷是产品侧的，修完就能判；不能判的宿主按能力位记 `N/A` |

## 2. 依据：E1 原来对锚点完全无感（真实缺陷）

`runtime/routing.ts` 原有三条边里，只有 E2 校验空间：

```ts
// 旧代码（E2）
if (thread && (!thread.spacePath || thread.spacePath === input.workspace.path)) { … }
// 旧代码（E1）——没有任何空间比较
const sessionId = await outboundSessionOf(input.email, parent)
if (sessionId) { if (await alive(input, sessionId)) return { kind: 'direct', sessionId, … } }
```

后果：地址曾在锚点 A 用过、会话在 A；之后地址被 move 到锚点 B。此时一封"回复我们当初在 A 发出的
那封信"的来信，父信 meta 里记着 A 的 `workspace_path` —— E1 命中，于是**信被投进 A 的会话**，
即 I2（禁止跨锚点）被违反。契约 §9.2 写的"跨锚点命中在构造上不存在"只对 E2 的 thread 键成立，
对 E1 的 `outbound[message_id]` 并不成立：那条键里**不含锚点**。

## 3. 修法（已落地）

- `src/runtime/routing.ts`
  - `outboundSessionOf()` 现在连 `workspace_path` 一起返回；
  - 新增 `foreign()` 判定：记录的 space 非空且 ≠ 当前锚点 ⇒ 该边作废；
  - E1、E2 都过这道判定；作废后继续按 E2/E3 在**当前**锚点重排；
  - 作废结果通过 `anchorMoved: { sessionId, space, edge: 'direct' | 'thread' }` 带出（不只是内部值）。
- `src/dsh/inbound.ts`：dispatch 行新增 `anchor: "anchor-moved"`、`anchorMovedEdge`、`anchorMovedFrom`，
  与既有 `reason` 正交（`reason` 仍说最终落点，`anchor` 说为什么换了锚点）。

单测：
- `test/core/runtime-routing.test.ts` 13 项：外来锚点的 E1 被弃（`edge: direct`）、E2 被弃（`edge: thread`）、
  同锚点仍走 E1（不是一刀切拒绝）。
- `test/dsh/inbound-session-gone.test.ts` 10 项：外来锚点的会话即使**活着**也不用（否则只有锚点检查能拦住），
  日志里能看到 `anchor-moved`；同锚点仍正常投递。

## 4. 判定口径（写进契约 §16 C16）

- **受判**：宿主能分配会话，并能表达"绑定在 A、该 thread 的会话曾在 B" ⇒ 跑 `s8`；
- **`N/A`**：`HOST_CAN_ALLOCATE_SESSION=0`（会话身份由 CLI 决定，没有第二个锚点可跨）——目前 hermes、pi；
- **`GAP`**：没有钩子、或 `rewritten=0`（场景没搭起来）。**`rewritten=0` 不得记绿**，否则"没搭"会伪装成"没违规"。

## 5. 夹具 `s8`（dsh 已实现，其余宿主 GAP 跟踪）

- `tests/host-acceptance/v2/lib/s8_drive.py`：发信 A → 记录会话 → **把 thread 指针的 `workspace_path`
  改成外来锚点但保留 `session_id`**（会话仍存活，所以只有锚点检查能拦住）→ 回信 →
  采集 `acked / moved / edge / moved_from / rewritten / used / leaks`。
- `tests/host-acceptance/v2/cases/s8_anchor_isolation.sh`：判 ack、`anchor-moved`、被弃边、**leak**（落进外来
  锚点会话 = 直接红）、`used ≠ foreign`；`rewritten=0` 或缺证据一律 GAP。
- 接线：`run-matrix.sh` 增加 `s8`（opt-in，默认 stages 不变）、`hosts/dsh.sh` 增加 `S8` 钩子。

## 6. 验证（本轮实际跑过）

```
cd tssdk/packages/agent-aimail
npx tsc --noEmit -p tsconfig.json            # clean
npx vitest run                               # 568 passed (64 files)
python3 -m py_compile tests/host-acceptance/v2/lib/s8_drive.py    # ok
bash -n tests/host-acceptance/v2/cases/s8_anchor_isolation.sh     # ok
bash -n tests/host-acceptance/v2/hosts/dsh.sh                     # ok
```

**没跑**：`s8` 的 L2 实跑（需要网关 + dsh 镜像 + LLM key 的完整 journey，约 15–25 分钟）。
所以 `s8` 目前是"**代码就位、未采集证据**"，不是"已验证通过"。

## 7. 剩余

- 质检在 dsh 上跑 `bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s8"`
  并把 `/out/s8_anchor.json` 归档。
- 其余宿主若也要 C16 证据，需各自实现 `host_s8_anchor_isolation` 的驱动（openclaw 的锚点是
  sessionKey 前缀白名单，语义等价但要单独实现；deerflow 的锚点是 thread 表 + 地址 owner）。

## 8. 重要发现（同轮核查，2026-11-25 晚）：dsh 出货路径不是 `dsh/inbound.ts`

核查"回环监听是否还有用"时发现：**dsh 的出货 bundle 根本没挂载 `inbound`**。

- `cordis.patch.yml` 只挂三行：`mail-service`（`ctx.mail`）、`tools`、`mail-hub`（SSE pull hub）。
- `package.json` 虽有 `./inbound` 导出，但没有任何 patch 行引用它 ⇒ `dsh/inbound.ts` 的
  `apply()`/`createServer()` 在打包后的 dsh 上**不会运行**。
- 真正投递的是 `dsh/mail-hub.ts` 的 `deliver()`：它用 `runtime/session-router.ts` 的 `routeThread()`，
  而 `routeThread()` **只有 E2（thread 复用）**，没有 E1（直接回复），也没有 dispatch 日志。

这意味着（必须记录，不能当成已完成）：

1. 本轮 §3 的 C16/E1 修复落在 `routing.ts` + `dsh/inbound.ts`——**单元测试覆盖的那条链**，但 dsh 出货
   路径不经过它，所以 C16 在 dsh 的真实投递上仍未被覆盖；
2. 出货路径缺 E1：一封"回复我们发出的那封信"的信，在 dsh 上不会回到发信会话（只能靠 E2 thread 命中）；
3. `agentmail.log` 的 dispatch 行（`session-gone` / `anchor-moved`）出自 `dsh/inbound.ts`，出货路径
   不写它 ⇒ s7/s8 在打包 dsh 上取不到证据（GAP，而不是绿）。

修法（需 owner/质检确认，属行为变更）：让 `mail-hub.deliver()` 改用 `decideInboundDelivery()`（E1/E2/E3
+ C16 + C17 语义已就位），并用 `logAimailDispatch()` 写 dispatch 行；`routeThread()` 保留给"只看 thread
指针"的兼容调用或直接删除。二者择一都要重跑 s6/s7/s8，因为它们现在测的链路与出货链路不一致。

## 9. 收口（同日，owner 指示"现在就做"）

§8 的两套路由已合并：`mail-hub.deliver()` 改调 `deliverInbound()`（`decideInboundDelivery` + dispatch 行），
`routeThread()` 删除。因此 **s6/s7/s8 测的就是出货链路**，C16 的 `anchor-moved` 证据在打包 dsh 上可以出现。
同时本地监听整体移除（`inbound_path`/`inbound_port` 退出契约）。详见
`docs/qa/UNIFY-DSH-DELIVERY-AND-DROP-ENDPOINTS-2026-11-25.md`。
