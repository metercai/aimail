# 交接：tssdk 0.5.2 + gateway v1.0.13 复验 / 发版 / L3

日期：2026-11-23  
交接方：开发（PI-Desktop）  
接收方：质检 / verifier  
范围：`/home/ubuntu/aimail`（tssdk）、`/home/ubuntu/aimail-gateway`（base）、`/home/ubuntu/aimail-advanced`（advanced）

---

## 1. 一句话结论

计划中的代码修复项已完成；L2 三宿主功能矩阵已用真实 Xiaomi LLM 跑通（产品 RED=0，pi s3 是已登记 fixture 限制）。请按本文顺序**先复验 → gateway 发版 → tssdk 发版 → L3 回归**。当前 L2 使用的是 pinned gateway **v1.0.12**；D1/D2 尚未发版，最终 L2 必须在 gateway 新版本发布并更新 `pins.env` 后重跑。

---

## 2. 本轮改动

代码基线（未提交工作树）：

- aimail `d67feb4`（main，本轮 48+ 文件改动，见 §3）
- aimail-gateway `166b6bf`（main，4 个文件）
- aimail-advanced `07f588c`（plan/activation-code-unification，1 个文件）

> 请 QA 先按仓库流程提交/评审，再触发发版；下面所有验证均基于该工作树。


### 2.1 tssdk（`/home/ubuntu/aimail`）

| 主题 | 改动 |
|---|---|
| B8 重新激活 | 带 `code` 时优先激活；归档 restore 只在无 code 时生效；unbind 后新 code 可重新激活/迁移 |
| B9 DSH code-only | `dsh/address.ts` 删除无 code + domain/admin key 注册路径；无 code 且无 binding ⇒ `code_required`；激活不再做本地 system/domain/scope 校验；旧 system 仅提供 gateway URL；跨 GatewaySystemId 成功后归档旧 binding |
| B9 工具面清理 | 删除 `dsh/system.ts`、`runtime/activation/system-tool.ts`、`runtime/activation/system.ts`、`system-core.provisionSystem` 与旧 `self-provision.test.ts`；`system-core` 只保留 `findExistingSystem` 只读查询；移除 `./system` export 与 runtime system-tool 导出 |
| C1 入站类型化 | `core/types.ts` 新增 `InboundResult`/`isAckedInboundResult`；DSH `deliver/deliverOne` typed；只 ack `accepted/intercepted`；`deliverBatches` 同样只 ack 可 ack 结果 |
| C3 aggregate stats | `startPolling` 接受外部 stats；`startSystemPull` 返回 live `stats()`；Pi/OpenClaw aggregate 不再硬编码 0 |
| C4 路由边界 | Pi/OpenClaw 删除 pointer fallback：未命中 recipient 直接 no-target，不 ack、不静默投递 |
| C5 CLI 入口清理 | 删除 3 个 `register-cli.ts`、`core/cli-op-entry.ts`、`core/cli-arg.ts`；移除 `registerCli` registry 字段/导出；`sdk-ops` op* 保留为宿主命令/测试内部 API |
| C6 测试稳定化 | `listen.test.ts` 用连续空闲端口块，去掉 `p+1/p+2` 假设 |
| REQ-5 | 增加断言：绑定成功结果不含 `raw_key`/`api_key`，密钥只落 0600 binding |
| 依赖 | `package.json` 运行期 `dependencies` 为空；`typebox` 仅 devDependency；`pnpm-lock.yaml` 已同步（frozen install 可过） |

### 2.2 gateway（base `aimail-gateway`）

文件：

- `src/core/api/activation.rs`
  - legacy `/api/v1/activate-address` 响应补 `system_id`（GatewaySystemId）
  - 激活邮件补 Appendix A 的 host-plugin 安装提示
  - `bind_agent_system_address` 失败不再静默（`tracing::warn!`）
- `src/core/api/http.rs`
  - `agent_system_slot_id()`：aggregate SSE + aggregate long-poll 共用按 ASID 的 synthetic slot，不再按代表地址 key 抢占
  - `list_agent_system_pending` 也持有 slot
- `src/core/storage.rs`
  - 新增 `remove_agent_system_address_if_unowned(system_id,address)`：仅当没有其他 active agent key 时删除归属行
  - 新增单元测试 `agent_system_ownership_is_removed_only_after_last_agent_key`
- `src/core/api/keys.rs`
  - `DELETE /api/v1/api-keys/:id` 成功后清理无主 ASID 归属行

### 2.3 gateway（advanced `aimail-advanced`）

- `src/advanced/api/email_notify.rs`
  - 激活邮件补同一句 host-plugin 安装提示。

> 注意：advanced 仓的 `cargo fmt --check` 在 `address.rs` / `open_apply.rs` 有**既存格式漂移**，与本轮无关；本轮只改 `email_notify.rs`，该文件单独 `rustfmt --check` 通过。发版前请决定是否单独格式化这两个文件，不要把它混进本轮功能 diff。

---

## 3. 已执行验证（开发侧）

```text
cd tssdk
pnpm install --frozen-lockfile                 PASS
pnpm build                                     PASS
pnpm test                                      59 files / 507 tests PASS
bash tests/sdk/l0-gate-tests.sh                PASS

npm pack:
  version                                      0.5.1
  dependencies                                 {}
  register-cli files                           0
  cli-op-entry files                           0
  dist/dsh/system.js                           0

cd aimail-gateway
cargo test --lib                               416 passed
cargo fmt --all --check                        PASS

cd aimail-advanced
cargo test email_notify                        3 passed
rustfmt --check src/advanced/api/email_notify.rs  PASS
```

gateway L2 用 pinned 二进制 v1.0.12（不是本轮源码构建）；因此上面的 416 是源码单测，不能替代发版后的 L2/L3。

---

## 4. L2 实机结果（已执行）

环境：

- 本机 linux-amd64
- SDK：dev build **0.5.1**（`pnpm build` + `npm pack`）
- Gateway：published **v1.0.12**（`tests/agent-env/pins.env`）
- LLM：真实 Xiaomi key（从 `~/.hermes/profiles/verifier/.env` 的 `XIAOMI_API_KEY` 注入，未写入仓库）
- 三宿主 Docker 镜像：`aimail-host-dsh:latest`、`aimail-host-pi:latest`、`aimail-host-openclaw:latest`

| run | 结果 |
|---|---|
| DSH `s1 s2` | **PASS=10 RED=0 GAP=0** |
| Pi `s1 s2` | **PASS=9 RED=0 GAP=0** |
| OpenClaw `s1 s2`（重跑） | **PASS=9 RED=0 GAP=0** |
| all `s4 neg` | **PASS=9 RED=0 GAP=0** |
| all `s5` | **PASS=6 RED=0 GAP=0** |
| all `s3` | **PASS=6 RED=0 GAP=1**；GAP = pi s3 已知 fixture（无 live delivery） |

补充：

- DSH s3 实际 PASS：`emails=1`、queued→acked；OpenClaw s3 也 PASS（`emails=2`）。
- DSH s1/s2 在最后的 `system-core` 清理后重跑 **10/10 PASS**，证据 `dsh-s1s2-final.log`；这是 final artifact 的 L2 基线。
- OpenClaw 首次 s1/s2（06:39Z）有 3 RED：容器 turn 报 gateway connection failed，gateway 日志显示脚本退出后 22s 正常 shutdown；无代码变更，06:42Z 重跑 9/9 PASS。首次失败日志保留用于归因，**归类为 fixture 瞬时问题，不是产品 RED**。
- pi 的 s3 是 `tests/host-acceptance/v2/GATE.md` 已登记的 “pi live delivery not wired”；不要把该 GAP 当成 tssdk 产品缺陷。若要求 s3 全绿，需要先补 pi 长驻进程 fixture，或把 s3 范围限定为 dsh/openclaw。

证据：

```text
evidence/l2/round2-20261123/
  dsh-s1s2.log
  dsh-s1s2-final.log
  pi-s1s2.log
  openclaw-s1s2-rerun.log
  openclaw-s1s2-first-attempt.log
  all-s3.log
  all-s4-neg.log
  all-s5.log
```

---

## 5. 复验步骤（QA 先做）

### 5.1 环境

```bash
cd /home/ubuntu/aimail
set -a
. ~/.hermes/profiles/verifier/.env
set +a
export LLM_API_KEY="$XIAOMI_API_KEY"
```

不要 `echo` key；不要提交 `.env`。

### 5.2 静态/单测

```bash
cd /home/ubuntu/aimail/tssdk
pnpm install --frozen-lockfile
pnpm build
pnpm test
cd /home/ubuntu/aimail
bash tests/sdk/l0-gate-tests.sh

cd /home/ubuntu/aimail-gateway
cargo test --lib
cargo fmt --all --check

cd /home/ubuntu/aimail-advanced
cargo test email_notify
rustfmt --check --edition 2021 src/advanced/api/email_notify.rs
```

### 5.3 L2（当前 pinned gateway 先复验，发版后再跑一次）

```bash
cd /home/ubuntu/aimail
rm -f /tmp/aimail-gateway.pid   # 本地重复跑时清 stale PID；并确认 18080/12525/12526 无残留

bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2"
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s3"
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s4 neg"
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s5"
```

判定：

- `RED>0` ⇒ 产品缺陷，停止发版。
- `GAP>0` ⇒ fixture/环境问题；pi s3 是已知例外。
- 期望：s1/s2/s4/s5/neg 三宿主全 PASS；s3 dsh/openclaw PASS，pi 为 known GAP。

---

## 6. 发版顺序（必须 gateway 先）

### 6.1 Gateway v1.0.13（base + advanced）

1. 评审并提交：
   - base：`activation.rs`、`http.rs`、`storage.rs`、`keys.rs`
   - advanced：`email_notify.rs`
2. 版本：
   - `aimail-gateway` Cargo version → **v1.0.13**
   - `aimail-advanced` Cargo version → **v1.0.13**
3. 构建 4 平台资产：
   - linux-amd64
   - linux-arm64
   - macos-arm64
   - windows-amd64
4. 生成/记录 sha256。
5. GitHub release tag `v1.0.13`，上传 4 个资产。
6. 更新 `tests/agent-env/pins.env`：
   - `GATEWAY_VERSION=v1.0.13`
   - 四个 `GATEWAY_SHA256_*`
7. 用新 pin 重跑 L2（至少 s1/s2/s3；建议 s4/s5/neg 也跑）。

发版验收：

```text
base cargo test --lib            416 passed
advanced email_notify test       3 passed
L2 RED=0（pi s3 GAP 记录在案）
```

### 6.2 tssdk 0.5.2

1. 确认 `tssdk/pnpm-lock.yaml` 已提交（本轮已更新，frozen install 通过）。
2. 版本：`tssdk/packages/agent-aimail/package.json` → **0.5.2**（按发布策略确认）。
3. 发布前：

```bash
cd /home/ubuntu/aimail/tssdk
pnpm install --frozen-lockfile
pnpm test
pnpm build
cd packages/agent-aimail
npm pack --pack-destination /tmp/release
tar -xOf /tmp/release/agent-aimail-0.5.2.tgz package/package.json
```

必须确认：

```text
dependencies = {}
register-cli 文件 = 0
cli-op-entry 文件 = 0
dist/dsh/system.js = 0
```

4. `npm publish`。
5. 打 tag / 记录版本。

---

## 7. L3 回归（verifier-only，发版后）

命令：

```bash
cd /home/ubuntu/aimail
export LLM_API_KEY="$XIAOMI_API_KEY"   # 从 verifier .env 注入

bash tests/l3/run-v2.sh
# 或单 cell
bash tests/l3/run-v2.sh --cell linux-amd64/dsh
```

矩阵（`tests/l3/GATE.md`）：

| platform | agent |
|---|---|
| linux-amd64 | dsh |
| linux-arm64 | dsh |
| macos-arm64 | dsh |
| windows-amd64 | dsh |

要求：

- 4/4 dsh cells PASS。
- hermes / deerflow 只打印 `SKIP_LEGACY`，不计绿，不执行。
- L3 必须使用**已发布**的 tssdk + 新 gateway pin；不能拿工作树构建。
- 验收闭环：取 code → 粘贴 prompt → 插件安装 → 激活 → address-code bind → welcome → identity card + signature。

---

## 8. 质检验收清单

- [ ] `pnpm install --frozen-lockfile` 通过
- [ ] tssdk 507 tests / L0 PASS
- [ ] tarball `dependencies {}`，无 `register-cli` / `cli-op-entry` / `dist/dsh/system.js`
- [ ] gateway base 416 tests、advanced email 3 tests
- [ ] L2 s1/s2/s4/s5/neg 三宿主全绿
- [ ] L2 s3 dsh/openclaw 绿；pi known GAP 有记录
- [ ] gateway v1.0.13 发布，`pins.env` 更新
- [ ] 新 pin 下 L2 重跑 RED=0
- [ ] tssdk 0.5.2 发布
- [ ] L3 4/4 dsh cells PASS，hermes SKIP_LEGACY
- [ ] 发布说明写明：CLI 入口移除、code-only、typed ack、ASID slot/归属清理

---

## 9. 已知残留 / 不在本轮范围

| 项 | 状态 |
|---|---|
| pi s3 live delivery | 已知 fixture 限制，不是产品 RED；要全绿需补 pi 长驻进程 |
| 旧 `aimail_system` acceptance 文档 | 代码入口已删，旧文档仍描述历史场景，需归档 |
| CR-11 外部预写 binding | 需 gateway 归属校验端点；本轮未做 |
| CR-13 address ledger / legacy `session_id` | 无 ledger 文件格式定义；legacy `dsh/inbound.ts` 未挂载但未删 |
| advanced `address.rs` / `open_apply.rs` 格式漂移 | 既存，与本轮功能无关；发版前单独决定 |

---

## 10. 联系点 / 回滚

- 若 L2 出现 RED：先保留 `evidence/l2/<ts>-*` 与 `/tmp/l2-*.log`，不要改断言。
- 若 gateway 新版本导致回归：回滚 `pins.env` 到 v1.0.12，保留新版本 tag 但不推广。
- 若 tssdk 发布后 L3 失败：npm dist-tag 回退到上一稳定版（由发布负责人执行），不要删除已发布版本。
- 任何 key 只从 `~/.hermes/profiles/verifier/.env` 运行时读取，不进入仓库、日志、证据文件。
