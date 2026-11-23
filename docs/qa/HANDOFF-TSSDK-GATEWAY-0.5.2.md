# 交接（最终收口）：tssdk 0.5.2 + gateway v1.0.14 / advanced v1.0.13

日期：2026-11-23  
交接方：开发（PI-Desktop）  
接收方：质检 / verifier  
范围：`/home/ubuntu/aimail`（tssdk）、`/home/ubuntu/aimail-gateway`（base）、`/home/ubuntu/aimail-advanced`（advanced）

---

## 1. 最终结论

```text
VERDICT: PASS
gateway  base 1.0.14 @ df52649 · tag v1.0.14 · pins.env = v1.0.14
tssdk    agent-aimail 0.5.2 @ be2c89e · npm latest = 0.5.2
advanced 1.0.13 @ 66a4c2d · 已部署（备份 aimail-advanced.bak-20261123）
L3       4/4 dsh cells PASS（v1.0.13 批次；v1.0.14 仅邮件文案+版本号，待 QA 复跑）
```

- L2 产品 RED = 0。
- R-1（openclaw s4 第二次激活）已撤回：隔离单跑 `max=2` + `from 2 binding(s)`，是 F5 flake。
- dsh s5 首次 `paused=False` 已归因为模型未调工具（F1 夹具/LLM），复跑 PASS。
- pi s3 的旧“已知 GAP”已关闭：`7fbcee8` 增加长驻 `pi --mode rpc` 会话；开发侧在 gateway v1.0.13 + SDK 0.5.2 下复跑为 `PASS=3 RED=0 GAP=0`。
- base v1.0.14 与 advanced v1.0.13 已完成邮件包名修正并部署；v1.0.14 仅改邮件正文/版本号。

---

## 2. 本轮改动

### 2.1 tssdk

| 主题 | 改动 |
|---|---|
| B8 重新激活 | 带 `code` 时优先激活；归档 restore 只在无 code 时生效；unbind 后新 code 可重新激活/迁移 |
| B9 DSH code-only | `dsh/address.ts` 删除无 code + domain/admin key 注册路径；无 code 且无 binding ⇒ `code_required`；激活不再做本地 system/domain/scope 校验；旧 system 仅提供 gateway URL；跨 GatewaySystemId 成功后归档旧 binding |
| B9 工具面清理 | 删除 `dsh/system.ts`、`runtime/activation/system-tool.ts`、`runtime/activation/system.ts`、`system-core.provisionSystem` 与旧 `self-provision.test.ts`；`system-core` 只保留 `findExistingSystem` 只读查询 |
| C1 入站类型化 | `core/types.ts` 新增 `InboundResult`/`isAckedInboundResult`；DSH `deliver/deliverOne` typed；只 ack `accepted/intercepted` |
| C3 aggregate stats | `startPolling` 接受外部 stats；`startSystemPull` 返回 live `stats()`；Pi/OpenClaw aggregate 不再硬编码 0 |
| C4 路由边界 | Pi/OpenClaw 删除 pointer fallback：未命中 recipient 直接 no-target，不 ack、不静默投递 |
| C5 CLI 入口清理 | 删除 3 个 `register-cli.ts`、`core/cli-op-entry.ts`、`core/cli-arg.ts`；移除 `registerCli` registry 字段/导出 |
| C6 测试稳定化 | `listen.test.ts` 用连续空闲端口块，去掉 `p+1/p+2` 假设 |
| REQ-5 | 绑定成功结果不含 `raw_key`/`api_key`，密钥只落 0600 binding |
| 依赖 | 运行期 `dependencies` 为空；`typebox` 仅 devDependency；`pnpm-lock.yaml` 已同步 |

### 2.2 gateway（base）

- `src/core/api/activation.rs`
  - legacy `/api/v1/activate-address` 响应补 `system_id`
  - 激活邮件补 Appendix A 的 host-plugin 安装提示
  - `bind_agent_system_address` 失败不再静默
- `src/core/api/http.rs`
  - aggregate SSE + aggregate long-poll 共用按 ASID 的 synthetic slot
  - `list_agent_system_pending` 也持有 slot
- `src/core/storage.rs`
  - `remove_agent_system_address_if_unowned()`：仅当没有其他 active agent key 时删除归属行
- `src/core/api/keys.rs`
  - `DELETE /api/v1/api-keys/:id` 成功后清理无主 ASID 归属行

### 2.3 gateway（advanced）

- `src/advanced/api/email_notify.rs`：激活邮件补同一句 host-plugin 安装提示。
- advanced **v1.0.13 已 bump、构建并部署**；base v1.0.14 也使用同一句文案。部署记录见 §6。

---

## 3. 最终验证

### 3.1 静态/单测

```text
cd tssdk
pnpm install --frozen-lockfile                 PASS
pnpm build                                     PASS
pnpm test                                      59 files / 507 tests PASS
bash tests/sdk/l0-gate-tests.sh                PASS

npm pack:
  version                                      0.5.2
  files                                        269
  dependencies                                 {}
  register-cli / cli-op-entry / dsh/system.js  0 / 0 / 0

cd aimail-gateway
cargo test --lib                               416 passed
cargo fmt --all --check                        PASS
cargo clippy --all-targets -- -D warnings      PASS

cd aimail-advanced
cargo test email_notify                        3 passed
rustfmt --check src/advanced/api/email_notify.rs  PASS
```

### 3.2 L2（gateway v1.0.13 / v1.0.14 + SDK 0.5.2，真实 Xiaomi LLM）

| 旅程 | 结果 | 归属 |
|---|---|---|
| DSH `s1 s2`（gateway **v1.0.14**） | **PASS=10 RED=0 GAP=0** | 第二版发布后的 smoke；证据 `dsh-s1s2-gateway-v1.0.14.log` |
| `--host all --stages "s1 s2 s3"`（v1.0.13） | PASS=34 **RED=0** GAP=1（旧） | dsh/openclaw s1/s2/s3 全绿；旧 GAP = pi s3 |
| pi `s3`（`7fbcee8` 后复跑） | **PASS=3 RED=0 GAP=0** | 长驻 rpc 夹具已关闭 GAP |
| `--host all --stages "s4 neg"` | 首次 RED=1/GAP=1 → 隔离复跑 PASS | openclaw s4 = F5 flake；无产品 RED |
| `--host all --stages "s5"` | 首次 RED=2 → 复跑 PASS | dsh s5 = F1（模型未调工具）；无产品 RED |

- OpenClaw 首次 s1/s2 的 gateway connection failed 也是同一类夹具 flake；重跑 9/9 PASS。
- 最终判定：**L2 无产品 RED**。

开发侧 pi s3 证据：

```text
evidence/l2/round2-20261123/pi-s3-final-gateway-v1.0.13.log
  PASS S3 sender whitelisted
  PASS S3 inbound stored (emails=1)
  PASS S3 queued then acked
  PASS=3 RED=0 GAP=0
```

### 3.3 L3（published artifacts）

| cell | run | 结论 |
|---|---|---|
| linux-amd64 / dsh | 45147504126 | PASS |
| linux-arm64 / dsh | 45147504126 | PASS |
| macos-arm64 / dsh | 45147504126 | PASS |
| windows-amd64 / dsh | 45167142107 | PASS |

- 前 3 格取自 publish run（`be2c89e`）；windows 格取自重跑（`65c6c5b`，差异只在门禁脚本，被测产物同为 npm 0.5.2 + gateway pin v1.0.13）。
- hermes / deerflow 只打印 `SKIP_LEGACY`，不计绿、不执行。
- windows 四轮修复均为验证方夹具问题（路径 glob、NTFS 模式位、300s 预算、cp1252 输出）；产品证据每轮一致。

---

## 4. 发布坐标

| 组件 | 坐标 |
|---|---|
| gateway base | `df52649` · tag `v1.0.14` |
| gateway 资产 | linux-amd64 / linux-arm64 / macos-arm64 / windows-amd64.exe；sha256 见 `REVIEW-REPLY-0.5.2.md` §2 |
| pins | `tests/agent-env/pins.env` = v1.0.14（本轮更新） |
| tssdk | `be2c89e` · npm `agent-aimail@0.5.2` |
| tssdk 收口 | `8ef6fd0`（0.5.2 批次）、`2965598`（发布坐标）、`22b1fbf`（版本断言/门禁输出） |
| advanced | `66a4c2d`（分支 `release/1.0.13`，已部署） |

---

## 5. 复验命令（只读核对）

```bash
cd /home/ubuntu/aimail
set -a; . ~/.hermes/profiles/verifier/.env; set +a
export LLM_API_KEY="$XIAOMI_API_KEY"

cd tssdk && pnpm install --frozen-lockfile && pnpm test
cd /home/ubuntu/aimail && bash tests/sdk/l0-gate-tests.sh

cd /home/ubuntu/aimail-gateway && cargo test --lib && cargo clippy --all-targets -- -D warnings
cd /home/ubuntu/aimail-advanced && cargo test email_notify

# L2（已发布 pin）
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2 s3"
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s4 neg"
bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s5"

# L3（verifier-only，published artifacts）
bash tests/l3/run-v2.sh
```

判定：

- `RED>0` ⇒ 产品缺陷。
- `GAP>0` ⇒ 夹具/环境；必须写明归因，不能当绿。
- 期望：L2 三宿主 s1/s2/s3、s4/s5/neg 全 PASS；L3 4/4 dsh PASS。

---

## 6. advanced 发布记录

- 版本：**1.0.13**；提交 `66a4c2d`，分支 `release/1.0.13`。
- 已构建 release 二进制并部署到生产；部署前备份 `/usr/local/bin/aimail-advanced.bak-20261123`。
- 远端 `aimail-advanced --version` = **1.0.13**，systemd `active`。
- 邮件正文包名已改为 `agent-aimail`；base 与 advanced 使用同一句提示。

---

## 7. 已知残留（不影响 0.5.2 / v1.0.14 放行）

| 项 | 状态 |
|---|---|
| CR-11 外部预写 binding | 需要 gateway 归属校验端点；本轮未做 |
| CR-13 address ledger / legacy `session_id` | 无 ledger 文件格式定义；legacy `dsh/inbound.ts` 未挂载但未删 |
| 旧 `aimail_system` acceptance 文档 | 代码入口已删，历史文档仍描述旧场景，需归档 |
| advanced 格式漂移 | 已随 v1.0.13 提交 `cargo fmt`（address.rs / open_apply.rs），不再阻塞 advanced 发布 |
| advanced 邮件文案 | 已随 v1.0.13 部署；见 §6 |

---

## 8. 回滚

- gateway：`pins.env` 可回 v1.0.13（上一稳定版）；v1.0.14 tag 保留，不删除。
- tssdk：npm dist-tag 回退到上一稳定版；不删除已发布版本。
- advanced：生产备份 `/usr/local/bin/aimail-advanced.bak-20261123`（v1.0.12）；需要时 stop → 覆盖 → start。
- 任何 key 只从 `~/.hermes/profiles/verifier/.env` 运行时读取，不进入仓库、日志、证据文件。
