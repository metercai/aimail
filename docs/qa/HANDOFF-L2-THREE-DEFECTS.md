# L2 门禁三缺陷交接（v2.4 发版前必须修）

- 日期：2026-11-25 · 提出：verifier
- 坐标：SDK `aaa1a3d` · gateway `v1.0.17 @ ac30461`（已发布，四资产 sha256 已亲核）
- 说明：**gateway 与 advanced 已发版**；本文件只讲挡 SDK 发版的三条 L2 红/异常。

## 复现方式（一条命令，四宿主）
```bash
GW_BIN=/home/ubuntu/aimail-gateway/target/release/aimail-gateway \
bash tests/host-acceptance/v2/run-release-gate.sh --only dsh,deerflow,pi
```

---

## 缺陷 1（P0）：dsh 在锚点绑定下零投递
**现象**：v2.4 激活后（`address_anchor` 有行），邮件到达网关但宿主一封不取。
```
PASS  sender whitelisted before inbound
PASS  inbound stored (emails=2)
RED   observed max=2 final=2        ← pending 一直为 2，从未 ack
```
连带 `s6/s7/s8` 全部 GAP。

**A/B 证据（唯一变量 = 锚点行）**
```
锚点存在      → emails=2, final=2            → RED
DELETE FROM address_anchor → "queued then acked" → PASS
```
**交叉对照（同一网关二进制）**：hermes / deerflow（pysdk）带锚点投递正常；只有 dsh（tssdk）零投递。

**定位线索**
- 网关强制点：`src/core/api/http.rs:3447 parse_anchor_claims()` + `:3491 enforce_anchor_binding`；
  头部 **`X-Aimail-Anchors: <email>;<sha256hex>[,<email2>;<hex2>]`**，缺失/不符 ⇒ **403 `anchor_mismatch`**（403 不写日志，别被"日志里没有 403"误导）。
- SDK 侧：`ANCHOR_HEADER` 仅出现在 `core/poll-entry.ts` / `core/anchor-id.ts`；
  **dsh 的 `dsh/mail-hub.ts`、`dsh/mail-service.ts` 未引用**。

**修法**：dsh 的每个已认证请求（SSE 拉取、ack）都带 `X-Aimail-Anchors`，值为该 agent 名下全部地址的 `email;sha256(workspace_path)`（集合来自绑定的 `members`）；403 必须留一行可见日志。

**验收**：`dsh s1 s2 s3` 全 PASS（不再出现 `final>0`）；`s8` 负例（改 workspace_path）必须 403；`s6 s7` 恢复可判。

---

## 缺陷 2（P1）：deerflow ack 之后没有任何线程接收（回读未生效）
**现象**（`s7`，删掉 thread 行后回信）：
```
PASS  reply was acked (not left pending)
RED   no thread received the reply (used=''); the mail was acked and dropped
RED   downgrade reason '' does not name session-gone
RED   thread root changed on re-attach; E2 lineage lost
```
**为什么不是夹具**：deerflow 的 `thread_id` 由 `(gateway_system_id, address, root)` 推导，
删掉后只要重建就必然是**同一个 id**；而 `used=''` 说明**连重建都没发生**——信被 ack 后没有任何线程收到它。
（我原先按"必须出现新 id"判，那条判据是错的，已改为判血缘+落地；改正后红没有消失。）

**期望**：已声明 `HOST_DELIVERY_CONFIRMATION=probe`（投递后回读 `GET /api/threads/<t>/runs/<r>`）；
那就不该出现"ack 了但无线程"。若本次确实无法确认落地，请改为 `unconfirmed` 并说明，别让声明与行为不一致。

**验收**：`deerflow s7` 出现 `acked + 线程落地 + reason=session-gone`（或按契约 9.3 记对应降级原因）。

---

## 缺陷 3（P1，待确认）：pi 的绑定没有锚点行
**现象**：pi 的 `/apply` 激活后 `address_anchor` 表里**没有** `l2pi@test.local`；
同一轮 hermes / deerflow / dsh 都有。
**影响**：`enforce_anchor_binding` 在"无记录锚点"时直接放行 ⇒ pi 永远不会被锚点强制校验，
`s8` 在 pi 上等于"没验"。L2 显示 pi 全绿，但这不是"支持锚点"，只是"没触发"。

**请回答**：pi 的激活路径为何没写入锚点？是宿主差异（例如 pi 不走 `/apply` 的绑定分支），还是 `/apply` 某分支漏了 `set_address_anchor`？

**验收**：pi 激活后 `address_anchor` 有该地址一行；`s8` 在 pi 上可判（正例 200、负例 403）。

---

## 附：已由 owner 签字、**不再要求修改**的两项
| 项 | 处置 |
|---|---|
| `pi / C8 分配半条`、`pi / C17` | **N/A**（pi 1.5.2 把 `newSession/fork/switchSession` 限定在 `ExtensionCommandContext`；会话身份由 CLI 决定） |
| `hermes / C17` | **RISK-ACCEPTED**（owner 2026-11-25 签字：pysdk 无公开落地 API，接受 ack-未落地风险） |

## 附：我已修好的夹具问题（不要再当产品问题追）
1. **deerflow/hermes** 的 `HOST_SESSION_MODEL=deterministic-thread`：id 由 root 推导，判据改为"血缘 + 落地"；
2. **dsh 锚点 A/B 探针**（`AIMAIL_L2_DROP_ANCHOR=1`，默认关闭）：用于证明/排除锚点强制；
3. s6/s7 的 `CASE` 标签、空值解析（管道分隔）、`sessions_json.py`/`threads_db.py` 等挂载助手。



---

# 复验结果（2026-11-25 夜，坐标 a07a592a + gateway v1.0.17）

三条修复**全部验证通过**：

| # | 项 | 复验证据 |
|---|---|---|
| 1 | dsh 锚点投递 | `s1 s2 s3 s6 s7 s8` → **PASS=31 RED=0 GAP=0**（s3 三封全 ack；s8 锚点隔离含 anchor-moved 记录） |
| 2 | deerflow 落地 | `s1 s2 s3 s7` → **19/0/0**（ack + 着陆 + `session-gone` 齐备） |
| 3 | pi 锚点 | `s1 s2 s3 s7 s8` → 15/0/0，**s8 可判**（`address_anchor` 已写入） |

## 复验期间发现的三处**夹具**缺陷（已修，与产品无关）
1. **幽灵会话**：两个 37–38 小时的遗留容器仍在拉取，各自落信 → s6 读成"新建会话"。run-matrix 现在每次 journey 前按镜像清容器。
2. **s7 的 reason 被写死空值**：驱动解析出 `session-gone` 却把 `""` 写入结果 → 三个宿主都被误判为"无降级记录"。已改为真实值；dsh 现在把插件日志打成证据包。
3. **模型 key 覆盖**：dsh 镜像主模型走 Xiaomi（`LLM_API_KEY`），会话标题走 DeepSeek；适配器把 `DEEPSEEK_API_KEY` 覆盖成主 key → 每轮 401、绑定失败。已分别注入。

## 对上一版本文档的更正
- "锚点强制破坏 dsh 的 C8 会话复用" —— **撤回**，实为遗留容器。
- "C17 的 reason 没有生产者" —— **撤回**，实为驱动丢值。
