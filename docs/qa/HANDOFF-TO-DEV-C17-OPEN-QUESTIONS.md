# 交接给开发：C17 三问（书面回答后我再复验）

- 日期：2026-11-25
- 发起方：verifier
- 前置：`REPLY-TO-QA-C8-P0-1-P1-1.md` 已核对。**C8（openclaw）、P1-1、pi 的 C8 分配半条/C17 三项已放行**。
  本文件只针对 C17 未闭环的部分，共三问。

---

## 0. 先说清我手上有什么（免得重复举证）

| 证据 | 位置 |
|---|---|
| 产者单测 2 项通过 | `tssdk/packages/agent-aimail/test/dsh/inbound-session-gone.test.ts` |
| 产者实现 | `src/dsh/inbound.ts:197` `updateAgentConfig(..., { session_id: sessionId })`；`agents.get(boundId)` 缺失 ⇒ `agents.resume` 失败 ⇒ 新会话 + `session_gone_redirected` |
| 真实流程实测（v6） | 见下"复现命令"与实测输出 |
| hermes / deerflow 实测 | `acked=true` + 会话零写入 ⇒ 邮件被 ack 后消失 |

### 复现命令（我用的就是它）
```bash
cd /home/ubuntu/aimail && set -a && . /home/ubuntu/.hermes/profiles/verifier/.env && set +a
export LLM_API_KEY="$XIAOMI_API_KEY"
GW_HTTP_PORT=18448 GW_SMTP_PORT=12948 \
  bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s6 s7"
```

### v6 实测输出（原文）
```
PASS  S7 session-gone — reply was acked (not left pending)
PASS  S7 session-gone — delivery used a new session (fced0c9a-3641-434e-96ae-bd543ee42b28
                       -> de8c58a9-b575-49e5-bff8-1698aa1e6525)
RED   S7 session-gone — downgrade reason '' does not name session-gone
PASS  S7 session-gone — the thread root survived the re-attach
VERDICT: FAIL (RED=1 GAP=0)

布局探针：
  aimail_home=/root/.aimail exists=True
  systems/system-9f3a -> ['board', 'l2dsh_test.local']
  glob hits=1 pattern=/root/.aimail/systems/*/*/agentmail.json  <!-- contract-allowed: QA 文档引用 binding 文件名以定位证据 -->
  point_at_missing_session: rewrote 0 config(s) -> s7-nonexistent-1795589186
```
**我的读法**：配置文件找到了，但里面**没有可改写的 `session_id`**，于是 `boundId` 为空、走 E3 新建会话；
`session-gone` 分支（依赖 `resume` 失败）从未进入。单测证明的是"若 resume 失败会怎样"。

---

## 1. 三问

### Q1 `session_id` 为什么没有落盘到 `agentmail.json`？  <!-- contract-allowed: QA 文档引用 binding 文件名以定位证据 -->
请回答：
- 写入发生在哪条路径、什么时机（只在 followup 成功后？只在特定 host？）；
- 是否会被其他写操作覆盖（`saveAgentConfig` / binding 同步 / 启动期重建）；
- 实测文件里该键**不存在还是为 null**（我在 v6 加了探针，下一跑会打印键名，但你可以直接给出结论与行号）。

判据：`updateAgentConfig({session_id})` 之后，`AIMAIL_HOME/systems/<sid>/<addr>/agentmail.json` 里 `session_id` 非空，  <!-- contract-allowed: QA 文档引用 binding 文件名以定位证据 -->
且下一次同 thread 投递时 `boundId` 能取到它。用一行命令可证：
```bash
python3 -c "import json,glob;print([ (f, json.load(open(f)).get('session_id')) for f in glob.glob('/root/.aimail/systems/*/*/agentmail.json')])"  <!-- contract-allowed: QA 文档引用 binding 文件名以定位证据 -->
```

### Q2 如果 dsh 不打算用 `cfg.session_id`，产者挂在哪条实际生效的路径上？
事实：同 thread 复用**现在走的是 thread/meta 路径**（s6 能过就是证据），而 `session-gone` 挂在 `resume` 上。
请二选一并给出落点（文件:行号）：
- (a) 让 `cfg.session_id` 真正写入并用于 resume；
- (b) 把产者改挂 thread/meta：thread 指向的 session 在宿主存储中不可用（目录/注册表不一致）⇒ 新建会话 + 记 `session-gone`。
判据：同 thread 回信时，若目标会话不可用，宿主侧能观测到**新会话**且 `agentmail.log` 出现 `session-gone`。

### Q3 `unconfirmed` 宿主上，能否在"未确认落地"时不 ack？
hermes / deerflow / openclaw 目前是 `unconfirmed`（不 N/A、受判）。这两家实测出现过 `acked=true` + **零写入**。
请回答：
- 协议侧是否允许"不 ack 让它 pending 重投"（§9.5 要求 Target 成功才 ack；Refusal ⇒ 不 ack）；
- 若允许，SDK 在什么条件下能判定"没落地"（写回 §9.4 的 thread 指针算不算可用的确认点）；
- 若不允许，约束具体在哪一条（重投窗口/去重/幂等键），以便 owner 决定是否接受风险。
判据：`unconfirmed` 宿主上模拟"配置指向不可用会话"，邮件**停在 pending**（可见、可重投），而不是被 ack 后消失。

---

## 2. 回答格式（我按这个复验，缺一项就打回）

每条请给：
1. **文件:行号**（改在哪）+ **一句话为什么改这里会生效**；
2. **新增/修改的测试**（路径 + 我可直接跑的命令）；
3. **一句可复现的验证命令**（或告诉我用上面那条 `run-matrix.sh` 即可）；
4. 若某条**不做**，给出理由与替代证据（不要用"已登记"代替）。

---

## 3. 三件不要再做的事（本轮已各踩过一次）

1. **改在未接线的模块**：`runtime/routing.ts` 的 `sessionExists`/`gone` 至今无宿主调用（`grep decideInboundDelivery` 只有 runtime 内部）；
2. **用单测代替端到端**：产者单测绿，但真实流程 `resume` 从不执行 —— 结论只能是"逻辑已证、路径不可达"；
3. **把 N/A 当判过**：`admission-only` 只是证据形态标注；`unconfirmed` 宿主只要实测 ack-零落地就判红。

---

## 4. 影响面（为什么这事挡发版）

`aimail@0.5.3` 会把同一套 SDK 推给所有 npm 宿主。C17 未闭环意味着：**审批类回信在会话不可用时可能被 ack 后消失且不再重投**。
在这一条修好或 owner 书面接受风险之前，不发 npm；`.piplug` 与 gateway 不受此条阻塞（各自判据独立）。
