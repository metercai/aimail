# 交接：激活统一改造发布（gateway v1.0.16 / advanced v1.0.15）

范围：`/home/ubuntu/aimail`（QA/文档/pins）、`/home/ubuntu/aimail-gateway`（base）、`/home/ubuntu/aimail-advanced`（advanced）
方案：`docs/qa/ACTIVATION-UNIFIED-REFORM-PLAN.md`（第 12 节为实施状态）
本版本**不做历史数据迁移**。

---

## 1. 发布坐标

| 组件 | 坐标 |
|---|---|
| gateway base | `9062f43` · tag `v1.0.16`（CI gate + release build 全绿） |
| gateway 资产 | linux-amd64 `813c43d8…b792` · linux-arm64 `6443b0c3…4ecc` · macos-arm64 `2cab2631…0c9e` · windows-amd64.exe `c2ddb2d4…7e9a`（完整值见 `tests/agent-env/pins.env`） |
| pins | `tests/agent-env/pins.env` → `GATEWAY_VERSION=v1.0.16` + 4 个 sha256（`516f8a1`） |
| advanced | `0242b23` · tag `v1.0.15` · 分支 `release/1.0.13`（沿用既有分支约定） |
| 发布文档 | 本文件；`5c1737d`（方案 + 域名审计入库） |

二进制自报版本已核对：`aimail-gateway-linux-amd64 --version` → `aimail-gateway 1.0.16`。

---

## 2. 变更要点

### base v1.0.16
- `[admin] domains` = **纯域名数组**（如 `["mail.example.com"]`）。启动时只补缺失项（`ON CONFLICT DO NOTHING`），id/webhook/归属改由域名 API 管理；删除配置项不动数据库。**v1.0.15 没有这个键，属纯新增，不影响既有配置。**
- 邮箱验证改为 **name+email+code 三元匹配**：`issue(email,name)` / `verify(email,name,code)`；name 精确比较，不匹配即消耗一次尝试。`send`/`check`、`apply-address`、内嵌申请页全部带上 name。
- 出站 `reply-to` 真正写进 MIME（relay sender + MX deliverer，新增 `parse_mailbox`）；无法解析时丢弃，不影响发信。
- activation-confirm 入站记录保留到激活码失效：`finalize_after_delivery` 只标记 delivered，`expired_delivered` 优先读 `x-aimail-activation-expires-at`。
- 共享激活工具层（prompt / key 素材 / code 生成 / agentname / 裸域规范化 / code 提取）集中在 `core::api::activation`。

### advanced v1.0.15
- 激活邮箱固定 `activate@{smtp.hostname}`；激活邮件带 Reply-To 与 Cc 副本。Cc 副本进 `emails` 作为确认源（`Handled`，不转发/不投递），保留至 code 失效。
- 用户回复按 **发件人 + code hash** 校验后走既有 `activate_system`，用既有 auto-reply 回结果；确认缺失/过期回“已失效，请重新申请”；伪造 confirm 头按回复处理并被拒绝。
- `address-options` 只返回申请人**自有**资源（shared / shared_system / dedicated + 剩余额度）；未知 `system_name` 不会被当成新命名空间。`apply-address` 支持 `system_id`（校验归属），旧 `system_name`/`domain` 仍可用；`address-state` 接受 `system_name`/`system_id`。
- `systems.applicant_email` 为 additive 列，仅新数据写入（历史回填已删除）。
- standalone：`GET /api/v1/public/standalone/address-options`（公开读）+ signed `system_admin` 写接口 `POST /api/v1/standalone/domains`、`POST /api/v1/standalone/apply-address`；新增 `/standalone` 许可证控制台页面（解锁 → 配额/域名 → 加域名 → 发地址码）。

### 契约影响（测试侧已同步）
- `/api/v1/public/email-verify/send` 与 `/check` **必须带 `name`**（缺 name → 400）。
- 已更新夹具：`tests/host-acceptance/v2/lib/gw.py`、`tests/l3/dsh-v2.sh`、`tests/l3/journey-pi.sh`（都用提交时同一个 name）。

---

## 3. 验证证据

| 项 | 结果 |
|---|---|
| base `cargo test` | **427 passed** |
| base `cargo fmt --check` / `clippy --all-targets` | 干净 |
| advanced `cargo test` | **154 passed** |
| advanced `cargo test --features standalone` | **123 passed** |
| advanced fmt / clippy（两种 feature） | 干净 |
| GitHub Actions（tag `v1.0.16`） | gate（fmt·clippy·tests）+ 4 平台 release build 全绿 |
| L2 dsh `s1 s2`（发布 smoke） | **PASS=10 RED=0 GAP=0** |
| L2 dsh `s1 s2 s3`（组合跑） | PASS=13（含 S3 delivery 3 项） |
| L2 pi `s1 s2` / `s3` | PASS=9 / PASS=3，GAP=0 |
| L2 openclaw `s1 s2` / `s3` | PASS=9 / PASS=3，GAP=0 |
| L2 all-hosts `s1`（同网关连跑） | **PASS=22 RED=0 GAP=0** |

新增单测：`parse_mailbox`（Reply-To 解析/拒绝非法值）、`delivered_expired_prefers_activation_expiry_header`（Cc 记录保留到 code 失效）、`name_mismatch_rejects_correct_code`、`resend_rebinds_name`、`activation_mail::{classify, reply_matches_confirmation}`、`standalone::{check_domain_admission, check_address_admission}`。

已记录的一次夹具抖动：`--host all --stages "s1 s2 s3"` 组合跑中 pi/openclaw 在 `/apply` 的 `stage: inbox`（15s 内没在 spool 里抓到 6 位码）GAP，随后 `all s1` 连跑 22/22 PASS、pi/openclaw 单独跑 s1–s3 全 PASS ⇒ 判为 spool 抓取时序抖动，不是产品 RED。

---

## 4. 未完成 / 交接

1. **advanced 生产部署**：已 bump/tag/push，但本轮没有目标主机信息，未构建部署二进制、未重启服务。需要按既有流程 `cargo build --release --features standalone` → 备份 `/usr/local/bin/aimail-advanced` → 覆盖 → 重启，并核对 `--version` = 1.0.15 + 服务 active。
2. **L2 s4 / s5 / neg 未跑**（与上一版一致，仍是未覆盖项）；L3（dsh 4 平台）未跑。
3. 组合跑（三宿主一次跑完 s1→s3）建议由验证方复跑一次，确认夹具抖动不再出现。

## 5. 回滚

- base：`pins.env` 可回 `GATEWAY_VERSION=v1.0.15` + 上一版 sha256；`v1.0.16` tag/资产保留不删除。配置无需回滚（新增键可整段删除）。
- advanced：`v1.0.14` tag 保留；生产回滚用部署前备份覆盖 + 重启。
