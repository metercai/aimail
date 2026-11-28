# 服务端 SSE 下行优化记录（v1.0.19）

- 日期：2026-11-26 · 代码坐标：gateway `v1.0.19 @ a0e720c`（前一版 `v1.0.18 @ e8b6b42`）
- 范围：**gateway 侧**聚合 SSE（AgentSystemId 作用域）的下行链路；不改契约、不改客户端协议
- 触发：容量评估时发现"心跳被当成轮询时钟"、"建连快照无字节上限"、"拉取查询全表扫描"三处规模化问题

---

## 1. 结论摘要

| 指标（4096 条聚合流） | 改造前 | 改造后 |
|---|---|---|
| 入站邮件 → 宿主拿到 | 最坏 **15 s**（心跳周期） | **一次 DB 查询**（入库即唤醒） |
| 空闲 CPU | **12% of 1 core** | **1%** |
| 空闲 DB 查询 | 4096 × 1/15s ≈ **273 q/s** | **0** |
| RSS | 224 MB（51 KB/流） | 224 MB（51 KB/流，未变） |
| 单流最坏峰值（深队列） | `20 封 × 单封 × ~4 份复制`（无上界） | **`connect_snapshot_bytes × ~2 + 51 KB`** |
| 并发流数 | 无上限 | `max_sse_connections`（默认 4096，超限 503） |
| 拉取查询计划 | `SEARCH p USING INDEX idx_pending_system`（同 system_id 下**等于全表扫**） | `SEARCH p USING INDEX idx_pending_email (email=? AND status=?)` |

---

## 2. 改造前的四个问题（含证据）

### 2.1 心跳被当成轮询时钟（延迟 15 s、空转查询）

聚合流的主循环是 `sleep(SSE_HEARTBEAT_SECS=15)` → 查库 → 有则发 `event: mail`、无则发 `: ping`。

根因不是"SSE 不支持推送"，而是**唤醒信号的粒度不对**：`PendingNotifier` 只有两种 key

```rust
enum NotifyKey { System(String), Email { system_id: String, email: String } }
```

而聚合流的作用域是 **AgentSystemId（一组地址）**。它的 `nkey` 取的是**代表地址**的 `Email` key，组内**非代表地址**来邮件时唤醒不到这条流 ⇒ 只能退回轮询。等于让保活的 `: ping` 承担了轮询职责，这不是 SSE 的标准用法。

### 2.2 建连快照没有字节上限

建连时把已在队列的邮件交给客户端（否则要等下一次唤醒），用的是 `LIMIT 20`（`default_limit()`）。
"20" 是**条数**，不看字节；且成帧路径会放大：

```
① 查询结果 Vec<PendingDeliveryRecord>（payload: String）        1×
② group_pending_batches → serde_json::Value（from_str + to_string）  ~2×
③ sse_mail_frame 的 format!("event: mail\ndata: {}\n\n")          1×
```

⇒ 单连接峰值 ≈ `4 × 20 封 payload`。同时**没有并发流上限**，最坏 = `流数 × 上述值`。

### 2.3 拉取查询是事实上的全表扫描

```sql
SELECT ... FROM pending_deliveries p
  JOIN agent_system_addresses a ON a.address = p.email AND a.system_id = p.system_id
 WHERE a.agent_system_id = ?1 AND p.status = 'pending' ORDER BY p.id LIMIT 20
```
`EXPLAIN QUERY PLAN` 改造前：

```
SEARCH a USING INDEX idx_agent_system_addresses_owner (agent_system_id=?)
SEARCH p USING INDEX idx_pending_system (system_id=?)          ← 同一部署全表同 system_id
USE TEMP B-TREE FOR ORDER BY
```

10 240 行、195 MB payload 的 backlog 下，**每条流每一拍**都要把这 200 MB 读一遍再排序。实测 200 条流就能把 RSS 顶到「1 MB/流」，且与快照大小无关。

### 2.4 空闲时也持续查询

上一项与 15 s 心跳叠加：4096 条流 × 1/15 s ≈ **273 q/s**，即使没有任何邮件。

---

## 3. 改动清单

| 文件 | 改动 |
|---|---|
| `src/core/pending_notifier.rs` | 新增 `NotifyKey::AgentSystem(asid)` 与 `notify_agent_system(asid)`；key 仍然是"一个 key 一个 gen 计数器 + notify"，合并天然（一次广播只醒一次） |
| `src/core/api/webhook.rs` | 入库（pending 行 commit 之后）在原有 `notify_system`/`notify_email` 旁增加 `notify_agent_system`；asid 由 `agent_system_id_for_address(addr)` 取得（主键查询，未绑定则跳过） |
| `src/core/storage.rs` | 新增 `agent_system_id_for_address`（主键查询）、`list_pending_for_agent_system_budgeted`（按字节停行）、`idx_pending_email(email,status,id)` 建表批次 |
| `src/core/api/http.rs` | 聚合流：`sleep` → `wait_since(AgentSystem(asid), since, heartbeat)`；心跳只做保活与丢信号兜底（**超时不再查库**）；新增 `drain_frame_batch(queue, budget)` 逐帧按字节取；`try_take_sse_slot` 准入；`SseStreamState` 增加 `queue` / `snapshot_bytes` / `_permit` |
| `src/core/api/types.rs` | `HttpState` 增加 `sse_slots: Arc<Semaphore>` |
| `src/core/config.rs` | `[http] max_sse_connections`（默认 4096，0=不限）、`[http] connect_snapshot_bytes`（默认 32768，0=只按条数）；env `AIMAILGW_MAX_SSE_CONNECTIONS` / `AIMAILGW_CONNECT_SNAPSHOT_BYTES` |
| `src/server.rs` | 生产构造点按配置建信号量 |

### 语义要点（不要记错）

- **不是"心跳推送"**：SSE 连接是单向的，客户端发完请求不再发包；`: ping` 一直是服务端写的。改造后它的职责只剩保活/兜底。
- **预算不是丢信**：超出预算的行**留在库里仍是 `pending`**，由后续帧或下一拍取走；已取未发的行留在该连接的 `queue` 里。永远至少取 1 条（超大单封也能动）。
- **预算同时约束"取数"和"成帧"**：`list_pending_for_agent_system_budgeted` 与 `drain_frame_batch` 用同一个值。
- **准入在分配之前**：超限 503 `sse_capacity` 发生在取 slot / 建快照 / 建状态之前；permit 存在流状态里，断连即释放。
- **notify 一定在 commit 之后**：沿用原有顺序，避免"通知到了但行还查不到"。

---

## 4. 实测数据与测量方法

机器：本机（同一台，release 构建，`cargo build --release --offline`）。测量手段：`/proc/<pid>/status` 的 `VmRSS`/`Threads`、`/proc/<pid>/stat` 的 utime+stime、`/proc/net/sockstat`、`EXPLAIN QUERY PLAN`。

### 4.1 并发流数与内存（稳态，空闲、无积压）

| 并发 SSE | RSS | 增量 | 每流 |
|---|---|---|---|
| 0 | 18.6 MB | — | — |
| 1000 | 79.1 MB | 60.9 MB | 61 KB |
| 2000 | 126.9 MB | 108.5 MB | 54 KB |
| 4000 | 220.8 MB | 201.9 MB | 50 KB |
| **4096** | **224–227 MB** | ~205 MB | **~51 KB** |

稳态不增长（20 s / 60 s / 120 s 采样：195.4 / 195.9 / 195.6 MB，该组数据用 `MALLOC_ARENA_MAX=2`）。

### 4.2 分配器对比（4096 条流）

| 配置 | RSS | 每流 |
|---|---|---|
| glibc 默认 | 224.3 MB | 51.4 KB |
| **glibc `MALLOC_ARENA_MAX=2`** | **192.6–195.6 MB** | **43.2 KB** |
| jemalloc（默认） | 258.5 MB | 59.1 KB |
| jemalloc（`narenas:2,dirty_decay_ms:1000,background_thread`） | 259.1 MB | 59.2 KB |

**结论：不使用 jemalloc**（本路径是活对象而非碎片，jemalloc 的元数据与脏页保留更贵）。部署建议 `MALLOC_ARENA_MAX=2`。

### 4.3 事件驱动（v1.0.19）

| 指标 | 数值 |
|---|---|
| 4096 条流空闲 CPU | **1% of 1 core**（改造前 12%） |
| 空闲 DB 查询 | **0**（改造前 ~273 q/s） |
| 唤醒延迟 | 由单测 `aggregate_stream_is_woken_by_ingest_not_by_the_clock` 固定：心跳 30 s、断言 **2 s 内**出帧 |
| RSS | 224 MB（与改造前相同，事件驱动不改变稳态内存） |

### 4.4 字节预算与查询计划（改造中间态测得，用于说明量级）

200 条流、每 ASID 20 封 × 20 KB（195 MB backlog）：

| 配置 | RSS 增量 | 每流 |
|---|---|---|
| 空队列基线 | 4 MB | 21 KB |
| 满队列 + 预算 32 KB | 140 MB | 700 KB |
| 满队列 + 只按条数（旧） | 203 MB | 1.0 MB |

（当时尚未加 `idx_pending_email`，绝对值被全表扫描放大；加索引后计划变为 `SEARCH p USING INDEX idx_pending_email`，这部分放大消失。预算本身节省的约 315–370 KB/流与理论值 `400 KB → 32 KB` 吻合。）

---

## 5. 内存模型与规格建议

**下行载荷不含附件**：SSE 下发的是 JSON（正文 + headers + 附件索引），附件已拆成文件索引、走独立下载通道。因此单封 payload 是**几 KB 量级**，`smtp.max_message_size`（10 MB）**不适用**于 SSE 载荷。

```
RSS ≈ 19 MB + min(N, cap) × 51 KB              ← 常态（4096 → 224 MB，实测）
     + min(N, cap) × (budget × ~2)             ← 深队列时的额外上界
```

| 场景 | 4096 条流 |
|---|---|
| 常态（无/浅积压） | **~224 MB** |
| 深积压（每流多封待投） | **~470 MB**（32 KB 预算） |
| 深积压 + 预算 16 KB | ~350 MB |

⇒ **2 vCPU / 2 GB** 可覆盖 4096 条流的最坏情况；`LimitNOFILE ≥ 8192`（4096 条流约 4200 个 fd）；内核 socket 缓冲额外 25–35 MB（不在 RSS 里，容器按 cgroup `memory.current` 算）。

---

## 6. 回归与复验

新增/相关的单测（`cargo test --lib`，共 449 项）：

| 测试 | 守住什么 |
|---|---|
| `pending_notifier::agent_system_key_wakes_only_its_own_waiters` | ASID key 只唤醒本实例，不串台 |
| `api::http::aggregate_stream_is_woken_by_ingest_not_by_the_clock` | 心跳 30 s 下，入库 notify 后 2 s 内出帧（**事件驱动生效**） |
| `api::http::frame_batch_is_bounded_by_bytes_and_takes_at_least_one` | 成帧按字节截断、至少 1 条、余量留队列 |
| `api::http::a_stream_is_refused_with_503_once_the_cap_is_reached` | 超过 `max_sse_connections` 返回 503 `sse_capacity`；permit 释放即恢复 |
| `storage::budgeted_pending_query_stops_at_the_byte_budget` | 取数按字节停行，且 0 = 只按条数（旧行为） |

复验步骤（任选）：

```bash
# 计划必须走新索引
python3 - <<'PY'
import sqlite3; db=sqlite3.connect('<data>/aimail.db')
q="""SELECT p.id FROM pending_deliveries p JOIN agent_system_addresses a
       ON a.address=p.email AND a.system_id=p.system_id
     WHERE a.agent_system_id=? AND p.status='pending' ORDER BY p.id LIMIT 20"""
[print(r) for r in db.execute('EXPLAIN QUERY PLAN '+q, ('as0',))]
PY

# 参数生效（容器/进程环境）
AIMAILGW_MAX_SSE_CONNECTIONS=1024 AIMAILGW_CONNECT_SNAPSHOT_BYTES=16384 <gateway> --db ... 
```

---

## 7. 已知边界（未做，别当成已完成）

1. **`sse_heartbeat_secs` / `sse_max_life_secs` 仍是常量**（15 s / 300 s），未做成配置。做配置会同时影响延迟语义与保活成本，需要单独拍。
2. **notify 是进程内的**：多进程/多实例共享一个库时，另一进程的入库不会唤醒本进程的流——那种部署下必须保留心跳兜底（当前默认即是）。
3. **per-address 旧流**（非 ASID，`NotifyKey::Email`）仍走原路径，行为未变。
4. **`/api/v1/admin/pending`（per-address 长轮询）不受 `max_sse_connections` 约束**，它的并发由 `delivery_slot` 的单消费者语义限制。
5. **附件下载通道**的带宽/内存未在本记录范围内；本记录只覆盖 SSE JSON 下行。
