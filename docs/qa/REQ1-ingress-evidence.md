# REQ-1（ING-1）证据链：入站→relay 产品路径不存在；症状由夹具端口 off-by-one 解释

- 日期：2026-11-23
- 范围：`aimail-gateway`（main @ `0c46b38`，修复后；pinned release v1.0.11）+ `tests/host-acceptance/v2` 夹具
- 性质：**只暴露证据链，不改夹具、不改判据**——夹具修订权在验证方；本文每项结论附 file:line 与可复现命令

## 结论

1. **网关入站路径不经过出站 relay**：静态路径 + pinned v1.0.11 活体复现双重坐实（§1、§2）。
   原报现象（入站原信出现在 relay spool、`emails=0`、`pending=0`、250）**全部可由夹具把探针发到 sink 口解释**（§3）。
2. **复现过程中发现第二个真实产品 bug**：入站台账行在 pull 入队成功后被立即删除（`emails=0`），与判据"入站后 `emails` 出现 1 行"矛盾。
   已修复：`aimail-gateway` main @ `0c46b38`（§4）。**注意：pinned release v1.0.11 不含该修复，S3 复测前需发布新网关版本或更新 pin（§5）。**

---

## §1 静态证据：inbound 恒走 webhook/pull，无 relay 可达路径

| 锚点 | 行为 |
|---|---|
| `backoff.rs:27-29` `detect_delivery_type` | `direction=="inbound"` 恒返回 `"webhook"`（inbound 永不进 smtp 分支） |
| `flows.rs:263-268` | `"webhook"` ⇒ `deliver_webhook`；`_` 兜底分支（`deliver_smtp`/relay 出站）仅 outbound 可达 |
| `webhook.rs:219-334` | pull 域 ⇒ `insert_pending_delivery` + mark endpoint success，全程无 relay 调用 |
| `deliver.rs` | `deliver_smtp`（relay 出站）唯一调用方 = `flows.rs` 非 webhook 分支 |
| `factory.rs:113` `create_inbound` | 恒写 `direction="inbound"` |
| advanced 仓 `src/advanced/smtp/handler.rs:481→560` | `data_end` 汇入基座 `process_data_end_post`，同一 core，无独立入站→relay 路径 |

## §2 活体复现（pinned v1.0.11 二进制，gw.sh 同款端口布局，独立 pid-file/端口，不碰在飞矩阵）

全流程 apply → bind（activate-address，创建 `domain_addr_meta`）→ wl → **探针发网关入站口 12526**（`$smtp+1`，非 sink 口）：

```text
gateway.log:
  Email received and stored via SMTP   sender=ext-sender@example.com
  Immediate forward  delivery_type="webhook"          ← 走 webhook/pull 分支
  Inserted pending delivery (pull mode)  domain=req1@test.local
DB:   pending_deliveries=1
spool: 入站原信不在 relay spool（spool 0 字节；出站验证信走 relay→sink 250 正常）
```

⇒ 信**发到正确的入站口**时：入库 + 入 pull 队列，relay 全程未参与。"入站→relay"产品路径不成立。

## §3 夹具 off-by-one：症状真因（file:line 已读回验证）

端口基准（`lib/gw.sh:54`：`smtp="${3:-12525}"`）：

| 口 | 值 | 定义处 |
|---|---|---|
| sink（relay 对端，spool 落这里） | 12525 | `gw.sh:83` sink 起在 `$smtp` |
| 网关入站 `[smtp] bind` | 12526（`$smtp+1`） | `gw.sh:63` |
| `[relay] smtp_server` | 12525（`$smtp`） | `gw.sh:70` |
| `export GW_SMTP` | **12525（= sink 口）** | `gw.sh:96` |

注入侧不一致：

| 文件 | 容器侧（正确） | 宿主侧注入（实际执行） |
|---|---|---|
| `hosts/dsh.sh` | `:18` `GWSMTP=$((GW_SMTP + 1))`=12526 传入容器 | `:89` `python3 - "$addr" "$token" "$GW_SMTP"` → `smtplib.SMTP("127.0.0.1", 12525)` |
| `hosts/openclaw.sh` | `:19` 同上 | `:103` 同上，裸 `$GW_SMTP`=12525 |

**机制**：宿主侧注入探针连的是 **12525（sink 口）而非 12526（入站口）**——信根本没进网关，由 sink 直接接收并 spool 原信，返回 250。
⇒ 原报四项症状逐一对上：spool 有原信 ✓、`emails=0` ✓（网关从未收到）、`pending=0` ✓、250 ✓（来自 sink 的 queued）。
与"网关把入站转发出站"是两种机制，前者已由 §1/§2 排除。

## §4 复现中发现的第二个真实产品 bug（已修复 @ `0c46b38`）

**现象**：信发到正确入站口后，pull 入队成功，但 `emails` 台账行随即被删（`emails=0`、`pending=1`）。
**根因**：`flows.rs finalize_after_delivery` 原逻辑——relay 已配置（或 webhook）⇒ `complete()` + `cleanup_completed_email()`；
pull 入队即 mark endpoint success ⇒ all endpoints 完成 ⇒ 立即 finalize。pull"入队成功"≠"已投递"（宿主可能未在线/未拉/未 ack），台账行却已删。
**修法**：finalize 增加判定——inbound + webhook + 该 mail 仍有 pending 行（按 payload 顶层 `mail_id` 匹配）⇒ `mark_delivered`
（行保留 `status='delivered'`），由既有 expired-delivered 批处理（`delivery_window_secs`，默认 7200s）清理；
ack 只删 pending 行、不动台账行。其余路径（webhook push / outbound direct / outbound relay）行为不变。
**验证**：单测 4 新增（pull 保留 / push 照删 / outbound-direct 保留 / outbound-relay 照删）+ 全量 lib 415/415 + clippy --all-targets 干净；
活体 e2e（本地构建二进制）：

```text
STEP1: 入站后 emails=1 pending=1 status=delivered     （修复前 emails=0）
pull: 200 ids:[1] / ack: 200 acked:1
STEP2: ack 后 pending=0，emails 行仍在（status=delivered，retention 接管）
STEP3: spool 无入站原信
```

**发布状态**：修复在 `aimail-gateway` main（`0c46b38`，已 push）。**pinned release v1.0.11 不含修复**——
L2/L3 从 `pins.env` pin 的是已发布二进制，直接复测 S3 仍会 `emails=0`（这次是真·判据可达性问题已修，但二进制未更新）。

## §5 复测前置（供验证方排期，开发侧已就绪）

1. 夹具端口修订（§3 两处：`dsh.sh:89`、`openclaw.sh:103`，宿主侧注入改用 `$((GW_SMTP + 1))`，与容器侧一致）——修订权在验证方。
2. 网关新发版（tag `v*` 触发 release.yml）或更新 `pins.env` pin 至含 `0c46b38` 的版本。
3. 按原判据复测 S3：入站后 `emails`≥1 且 `pending`≥1 → 宿主取走 → `pending=0`、台账行保留至 retention → spool 无入站原信。
   （判据中"行消失"现对应 pending 行；台账行由 retention 清理，与 NDR 窗口同一机制。）

**复现脚本**：`/tmp/req1x.sh`（pinned v1.0.11 决定性复现，§2 数据源）、`/tmp/req1b-verify.sh`（修复后 e2e 全判据，§4 数据源）。
