# DSH AIMail 验收测试说明（QA 版）

本目录是跨仓验收夹具：**base 网关 + advanced 网关 + tssdk dsh 插件**。

> 场景口径（1–6）见 **[SCENARIOS.md](SCENARIOS.md)**；场景 1 与 6 的前置/地址格式不同，测试时不要混用。
不依赖 DSH Web UI；用 fake ctx 驱动 tssdk 的真实适配层，用 Mailpit 模拟收件人/SMTP 出口。

## 1. 目录与前置

```text
chains/
  aimail/                  ← 本仓（tssdk、pysdk、CLI、tests）
  aimail-gateway/          ← base（已 clone，与 advanced 平行）
  aimail-advanced/         ← advanced（私有）
```

前置：

- Linux（推荐；advanced 的 `machine_code` 测试依赖 `/etc/machine-id`）
- Docker 24+、Compose 2.17+（`additional_contexts` 用于 advanced 构建）
- Node 20+、pnpm 9+（tssdk 构建/测试）
- Rust 1.98.1（`run-gateway-tests.sh` 原生跑网关测试；Docker 构建不要求宿主 Rust）
- 网络：首次构建需要拉取 crates/npm 包；之后可离线

## 2. 启动测试栈

```bash
cd tests/dsh-acceptance
docker compose up -d --build
docker compose ps
```

端口：

| 服务 | 地址 | 用途 |
|---|---|---|
| base HTTP | http://127.0.0.1:18080 | 申领页 `/apply`、API、tssdk 激活 |
| base SMTP | 127.0.0.1:12525 | 入站测试信 |
| advanced HTTP | http://127.0.0.1:18081 | 共享域 B2 |
| advanced SMTP | 127.0.0.1:12526 | 入站测试信 |
| Mailpit UI/API | http://127.0.0.1:8025 | 模拟收件箱、验证码/激活码 |
| Mailpit SMTP | 127.0.0.1:1025 | 两个网关的 outbound relay |

两个网关都配置 `relay.smtp_server = smtp://mailpit:1025`；验证码/激活码走**真实 outbound 队列**，Mailpit 是模拟收件人。base 的 `/apply` 页面可人工操作，也可用脚本。

## 3. 自动化脚本

所有脚本在 `tests/dsh-acceptance/scripts/`，默认从 `tests/dsh-acceptance` 运行。

### 3.1 base 用户闭环

```bash
./scripts/run-base-loop.sh
```

覆盖：

1. 启动后等 `/health`；打开 `/apply` 可用。
2. 从 `tools` 容器读取 bootstrap admin key / system id。
3. 用 admin API 创建 `test.local` 与 `second.test` 两个独享域（验证 base 单系统多 domain）。
4. `claim-base.sh`：邮箱验证 → Mailpit 取验证码 → `apply-system` → 系统激活码。
5. `dsh-harness system`：系统对接，校验 `aimail_gateway.json`、`domain_keys`、默认工作员绑定默认工作区。
6. `claim-base.sh` + `dsh-harness address`：`alice@test.local` 地址码绑定。
7. 第二个 domain：`bob@second.test` 地址码绑定（验证 per-domain key 选择）。
8. 外部 SMTP `sender@external.test → alice@test.local`，`dsh-harness hub` 必须收到 followup（SSE → workspace session）。

产物：`.data/system.json`、`address.json`、`address2.json`、`hub.json`。

### 3.2 advanced B2 闭环

```bash
./scripts/run-advanced-b2.sh
```

覆盖：

1. 等 advanced `/health`（启动时自动建 `shared_addr-public` 系统）。
2. `claim-advanced.sh`：邮箱验证 → Mailpit 取验证码 → `POST /api/v1/apply-address`（`shared_domain=shared.test`）→ Mailpit 取 `addr-...`。
3. `dsh-harness address`：无 system、共享域 B2 绑定。
4. 申请人（默认 manager，自动白名单）发 SMTP 到 `alice@shared.test`；hub 必须收到 followup。

### 3.3 tssdk / 兼容 / 契约

```bash
./scripts/run-tssdk-tests.sh
```

- `pnpm install --frozen-lockfile && pnpm build`
- `vitest run test/dsh test/openclaw test/pi`
- `check-file-ownership.py`、`check-zero-bridge.py`、`check-contract-single-source.py`

### 3.4 场景 5 独立网关 + 场景 6 无码地址

```bash
./scripts/run-base-independent.sh
```

- 从 base 数据卷读取 `<sid>.system.key` 与 `system.id`；
- `aimail_system` 用 **system_key + system_id + domain**（不消耗产品码）；
- 再用 `aimail_address` 无码绑定 `agent@test.local`（场景 6，domain key 注册）。

### 3.5 base 生命周期/轮换

```bash
./scripts/run-base-lifecycle.sh   # pause -> resume -> unbind rejected
./scripts/run-base-rotation.sh    # same address + new code -> kind=rotation
```

`run-base-lifecycle.sh` 依赖 `run-base-loop.sh` 已创建 alice 绑定。

### 3.6 原生网关单测

```bash
./scripts/run-gateway-tests.sh
```

- base：`cargo test --lib`（期望 395+ passed）
- advanced：`cargo test address`（期望 13/13）；`cargo test`（Linux 期望全绿；macOS 会因 `/etc/machine-id` 失败 9 个 machine_code 用例）

## 4. 人工 webapp 流程

打开 `http://127.0.0.1:18080/apply`：

1. 输入邮箱（如 `user@test.local`），点“发送验证码”。
2. 点“查看模拟收件箱”，从 Mailpit/页面读取 6 位验证码，填入并“验证”。
3. 申领系统码：姓名 + 选择 `test.local` → 页面显示 `sys-...`。
4. 申领地址码：姓名 + agentname（如 `alice`）+ domain → 页面显示 `addr-...`。
5. 把码交给 agent：
   - 系统码 → `aimail_system bind{code,gateway_url,domain}`
   - 地址码 → `aimail_address bind{email,code,gateway_url}`

Mailpit UI `http://127.0.0.1:8025` 可看原始邮件（验证码/激活码），用于核对真实发信。

## 5. 验收矩阵

| ID | 场景 | 入口 | 期望 | 证据 |
|---|---|---|---|---|
| B1 | base 系统申领 | `/apply` + `apply-system` | 返回 `sys-...`；Mailpit 有系统码邮件 | `claim-base.sh` |
| B2 | base 系统对接 | `dsh-harness system` | `success=true`；`domain_keys`；默认工作员默认工作区 | `.data/system.json` |
| B3 | base 本域绑定 | `claim-base` + `dsh-harness address` | `success=true`，`mode=code` | `.data/address.json` |
| B4 | base 多 domain | `second.test` 绑定 | `domain_keys` 选对；绑定成功 | `.data/address2.json` |
| B5 | base 收信 | SMTP + `dsh-harness hub` | `followups>=1`，session cwd=workspace | `.data/hub.json` |
| B6 | 场景 6 无码绑定 | `alice@test.local` 已 system 对接 | `mode=register`；domain key 注册 | `run-base-independent`/手工 |
| B7 | 场景 5 独立网关 | `system_key+system_id` | `kind=key`；默认工作员 | `run-base-independent.sh` |
| B8 | 重复绑定/轮换 | 对同一地址再 claim 一次 | 返回 `kind=rotation`；旧 key 失效 | `run-base-rotation.sh` |
| B9 | pause/resume | `aimail_address pause/resume` | paused 不 ack；resume 补收 | `run-base-lifecycle.sh` |
| B10 | no unbind | `action=unbind` | `bad_args` | `run-base-lifecycle.sh` |
| A1 | 场景 1 共享地址 | `aimail_address` code+gateway | 无 system；B2 成功 | `run-advanced-b2.sh` |
| A2 | advanced 地址申领 | `claim-advanced` | Mailpit 有 `addr-...` | 脚本 |
| A3 | advanced B2 绑定 | `dsh-harness address` | `success=true`，无 system | `.data/adv-address.json` |
| A4 | advanced 收信 | SMTP + hub | `followups>=1` | `.data/adv-hub.json` |
| A5 | active 续期 | 再次申请同地址 | `kind=renewal`；expiry 延长 | advanced 单测 `renewal_of_active_shared_address_rotates_the_key` |
| T1 | tssdk dsh/openclaw/pi | `run-tssdk-tests.sh` | 全绿 | vitest |
| C1 | 契约 | `run-tssdk-tests.sh` | 3 个 checker PASS | stdout |

## 6. 夹具与数据

- 网关配置：`config/base.toml`、`config/advanced.toml`
- 测试域：`test.local`、`second.test`（base）；`shared.test`（advanced）
- 测试账号：`admin@test.local`、`user@test.local`、`alice@test.local`、`bob@second.test`、`applicant@example.com`、`alice@shared.test`
- 外部发件人：`sender@external.test`（base 脚本会加白名单；advanced 用申请人 manager 自动白名单）
- 所有验证码/激活码从 Mailpit 读取，不写死
- `harness/dsh-harness.mjs` 的 fake ctx 输出 followup，用于断言“SSE → workspace session”

## 7. 已知限制

- **advanced `machine_code`**：Linux 需要可读 `/etc/machine-id`；容器/精简系统缺失时 9 个用例失败，属环境问题。
- **Docker 构建**：advanced 需要 `additional_contexts.gateway`；Compose 版本 <2.17 不支持，请升级。
- **Mailpit**：仅测试用；生产把 `relay.smtp_server` 指向真实 SMTP 即可，base 的 `/apply` API 不变。
- **模拟收件箱**：base 的 `/api/v1/public/mock-inbox` 读取真实 outbound 邮件（不是假表）；仅试点，生产不应暴露。
- **真实 DSH agent**：本夹具用 fake ctx 验证适配层；真实 DSH 插件加载/L3 仍需单独跑（见 `docs/agent-self-setup.md`）。
- **advanced B2**：需 advanced 仓构建成功；若私有仓无权限，先在有权限的机器构建镜像。

## 8. 真实 DSH agent（可选 L3）

自动化夹具用 fake ctx 验证适配层；真实 DSH 需要桌面/CLI：

1. 构建 tssdk：`cd tssdk && pnpm install && pnpm build`。
2. 在测试 profile 安装插件（二选一）：
   - 插件入口：`dsh plugin --profile qa add <aimail/tssdk/packages/agent-aimail>`；
   - 或桌面/CLI 的插件安装入口。
3. 用 `fixtures/prompt-system.txt` 走系统对接；默认工作员应绑到 **DSH 默认工作区**。
4. 用 `fixtures/prompt-address.txt` 绑定地址；从 Mailpit 取地址码。
5. 用 base SMTP（12525）发外部信到绑定地址；真实 agent 应在对应 workspace/session 收到并回复。
6. 断言：`~/.dsh/profiles/<profile>/aimail/status.json` 有 `mode=sse`、streams；无 9099 监听；`lsof -iTCP:9099 -sTCP:LISTEN` 为空。

## 9. 清理

```bash
docker compose down -v
rm -rf .data
```

## 10. 记录模板

```text
日期/执行人：
commit: aimail=4527668 base=28bdc7b advanced=c650ab1
Docker/OS/Rust/Node:
B1..B5: PASS/FAIL + 证据文件
A1..A3: PASS/FAIL
T1/C1: PASS/FAIL
异常：
```

> 验收结论以脚本退出码 + `.data/*.json` 证据为准；任何 FAIL 请附 Mailpit 邮件原文与网关日志（`docker compose logs base advanced`）。
