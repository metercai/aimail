# DSH SSE 试点 · 质检/发版交接（agent-aimail 0.4.0，预发布）

- 交接对象：QA / 发布
- 目标版本：`agent-aimail@0.4.0`（npm）、CLI `0.1.64`
- 关联网关：base **`1.0.8`**（SMTP relay 修复）、advanced `1.0.11`
- 规范：[`docs/aimail-host-sse-contract.md`](../../docs/aimail-host-sse-contract.md)
- 两套 L2：[`L2-PROFILES.md`](L2-PROFILES.md)

## 1. 本轮交付

| 项 | 内容 |
|---|---|
| 系统对接 | `aimail_system` 合并进 DSH tool entry，工具列表可见 |
| 工具分组 | `MAIL_TOOLS`(7) / `MAIL_BOARD`(7) / `MAIL_ADMIN`(1 spec-backed；names 含 `aimail_system`) |
| 地址工作区 | 地址激活的工作区不再被启动 ensure 覆盖；SSE 按 workspace 解析 |
| 共享 SSE | 同一 `gateway_url` + credential scope 复用一条 SSE，多地址按 recipient 路由到 workspace/session |
| 无 CLI | DSH 启动绑定禁止 `ensureSystem` 反向调用 CLI |
| L2 | 新增无 CLI 的 DSH SSE L2；旧 CLI/push L2 保留 |
| 版本 | `agent-aimail@0.4.0` |

提交：

```text
852a051 fix(smtp): accept smtp:// relay URLs and reject unknown schemes at startup
7f96931 fix(dsh): disable the CLI fallback on the no-CLI SSE path
41c2fbb fix(dsh): share one SSE stream per credential scope and document it
3028b69 test(l2): add DSH SSE L2 profile without CLI steps
f6c630f docs: add host-neutral AIMail SSE activation contract
c65ed44 refactor(tools): split MAIL_TOOLS, MAIL_BOARD and MAIL_ADMIN groups
```

## 2. 无 CLI 审计

DSH 的 Cordis bundle 只挂载：

```text
mail-service   → ctx.mail / workspace binding ensure
tool-mail      → 15 spec-backed tools + aimail_system
mail-hub       → SSE pending stream
```

不存在：

- `mail-inbound` / 本地 listener
- `register-cli` / `aimail install` 运行时调用
- `webhook_url` / HMAC 入站依赖

启动时的默认绑定调用显式带：

```ts
allowCli: false
```

因此没有 owned system 时，它只会返回 “CLI disabled” 的错误并跳过默认绑定，
不会 spawn `aimail`。`ensureSystem` 仍保留在通用 core（其他 host 可继续用），
但 DSH 路径不会进入。

注意：`register-cli`/`inbound` 的源文件仍可能随包存在（兼容旧 host/历史导出），
但不在 DSH bundle 中挂载，不属于 DSH 运行路径。

## 3. 本地检测结果

### L0

```bash
bash tests/sdk/l0-gate-tests.sh
```

结果：

```text
L0 PASS (rc=0)
pytest 435 passed
tssdk vitest: Linux 481 passed / 0 skipped；macOS 480 passed / 1 skipped
```

跳过项：macOS Node 的“端口全部耗尽”监听用例；SSE 不需要 listener。
Node 24 / pytest 已持久安装；Mac Node 23 的 SQLite 无 FTS5，不能作为 L0 依据。

### L1

```bash
cargo test --manifest-path cli/Cargo.toml
cargo fmt --manifest-path cli/Cargo.toml --check
cargo clippy --manifest-path cli/Cargo.toml --all-targets --no-deps -- -D warnings
```

结果：全绿（0 failed / fmt / clippy PASS）。

### Gateway（SMTP relay fix）

```bash
cd aimail-gateway
cargo test --lib core::smtp::transport
cargo fmt --check
cargo clippy --all-targets --no-deps -- -D warnings
```

结果：5 transport tests PASS；fmt/clippy PASS。`smtp://` 现在按 plain
SMTP 解析；未知 `://` scheme 在启动时返回配置错误，不再变成运行期 DNS 噪音。

### npm dry run

```bash
DRY_RUN=1 bash scripts/publish-npm.sh packages/agent-aimail
```

结果：

```text
+ agent-aimail@0.4.0

`npm pack --dry-run` also PASS (292 files, 294.9 kB).
```

## 4. QA 需要执行的两种 L2

### 4.1 旧 L2（保留，CLI/push）

```bash
cd aimail-advanced/tests/SDK/docker-regression/hosts
bash run-host-regression.sh dsh <sid> <manager> <domain>
```

仍然是旧规范：CLI install/register、push/webhook/本地 inbound。不要用它判断
SSE 试点。

### 4.2 新 L2（SSE，无 CLI）

前置：

- live base/advanced gateway
- 两个一次性激活码（`/apply` 申领）：
  - `SYSTEM_CODE`（`sys-...`）
  - `ADDRESS_CODE`（`addr-...`，对应 `EMAIL`）
- 一个目标 `WORKSPACE`
- 已构建的 `tssdk/packages/agent-aimail/dist`（`pnpm build`）

执行：

```bash
cd aimail/tests/dsh-acceptance
GATEWAY_URL=http://127.0.0.1:18080 \
SYSTEM_CODE=sys-... \
ADDRESS_CODE=addr-... \
EMAIL=alice@test.local \
DOMAIN=test.local \
WORKSPACE=/tmp/dsh-l2-workspace \
./scripts/run-dsh-l2-sse.sh
```

共享域改用 `SYSTEM_NAME` 替代 `DOMAIN`。

判定：`RESULT: L2-SSE PASS`，且：

- 系统/地址激活均不调用 `aimail` CLI；
- binding 的 `workspace_path` == `WORKSPACE`；
- binding 无 `webhook_url` / `session_id`；
- welcome 经 SSE 进入 Agent session，`cwd` == `WORKSPACE`。

说明：新 L2 用 fake Cordis Agent session，不跑真实 LLM；真实 welcome 回复/manager
批复仍由 L3 真 LLM 负责。

## 5. 契约验收矩阵（C1–C11）

| ID | 本轮证据 | 状态 |
|---|---|---|
| C1 系统码共享域 | 新 L2 + 单测（self-provision） | 代码通过；live 待 QA |
| C2 系统码独享域/独立网关 | 新 L2 可传 `DOMAIN`/`SYSTEM_NAME` | 代码通过；live 待 QA |
| C3 地址 B2 | 新 L2 + `run-advanced-b2` | 代码通过；live 待 QA |
| C4 地址 B1 | 新 L2 + 单测 | 代码通过；live 待 QA |
| C5 激活 workspace 保持 | 新增单测 + 新 L2 断言 | PASS（单测） |
| C6 welcome SSE | 新 L2；gateway welcome | live 待 QA |
| C7 manager 回复同 workspace | L3 / thread meta 单测 | live 待 L3 |
| C8 pause/resume | `dsh-harness lifecycle` + 单测 | 代码通过 |
| C9 ack/重连幂等 | mail-hub 单测 + L3 | 代码通过 |
| C10 密钥/文件边界 | L0 contract/file-ownership | PASS |
| C11 共享 SSE | 同一 gateway/scope 复用一条流；多地址按 recipient 路由 | 代码/契约已列；live 待 QA |

## 6. 发版顺序

1. 发布 `agent-aimail@0.4.0`（npm）。
2. 发布/更新 base gateway `1.0.8`（smtp:// relay 修复）。
3. 确认 CLI `0.1.64` 已发布（pull-mode check）。
4. 跑新 L2（4.2）。
5. 跑 L3 `dsh-aimail` / `dsh-plugin`（真 LLM）。
6. 通过后再做试点签字；旧 L2 结果不计入 SSE 试点。

发布后核对：

```bash
npm view agent-aimail version   # 0.3.2
```

## 7. 已知限制 / 回滚

- 0.2.19 之前的旧 binding 若被启动 ensure 覆盖过 workspace，需要重新执行一次
  地址 link/bind 更新；0.3.2 不会自动改回历史值。
- macOS 默认 Node 23 缺 FTS5；L0 必须用 Node 24。
- 旧 L2 仍使用 CLI/push，仅用于旧 host 回归，不代表 SSE 试点。
- 回滚：npm 回退上一稳定 `agent-aimail` 版本，并恢复旧 L2；网关数据不迁移。
- advanced `1.0.11` 仍内嵌 gateway `1.0.7`；若 advanced 的 relay 路径也需要
  `smtp://` 修复，需基于 gateway `1.0.8` 重建并发布 advanced。
