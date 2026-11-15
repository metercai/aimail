# 给质检的 Prompt（可直接转发）

你是独立质检。目标：从 GitHub 拉取指定 commit，按 `tests/dsh-acceptance/README.md` 对 AIMail 的 DSH 接入做端到端验收；**只测试、不改代码**。任何 FAIL 如实记录，不要为了变绿跳过用例。

## 0. 仓库与提交（必须逐仓核对）

```bash
git clone https://github.com/metercai/aimail.git
git clone https://github.com/metercai/aimail-gateway.git
git clone https://github.com/metercai/aimail-advanced.git   # 私有仓，需授权
```

三个仓放在同一父目录（sibling）：

```text
<work>/
  aimail/                 # HEAD = 交接消息里给出的 aimail commit
  aimail-gateway/         # HEAD 应为 b4d2955
  aimail-advanced/        # HEAD 应为 1f48d62
```

逐仓执行：

```bash
git rev-parse --short HEAD
git log -1 --oneline
git status --short     # 必须干净（不能带着测试期改动）
```

如果 commit 不存在或 HEAD 不符，**停止并报告**，不要自行切到别的 commit。

## 1. 环境

- Linux（必须；advanced 的 machine-code 用例依赖 `/etc/machine-id`）
- Docker 24+、Docker Compose 2.17+（advanced 构建需要 `additional_contexts`）
- Node 20+、pnpm 9+（tssdk 构建/测试）
- Rust 1.98.1（仅 `run-gateway-tests.sh` 需要；Docker 构建不需要宿主 Rust）
- 网络：首次构建拉 crates/npm；之后可离线

## 2. 启动测试栈

```bash
cd aimail/tests/dsh-acceptance
docker compose up -d --build
docker compose ps
```

端口：base `18080/12525`，advanced `18081/12526`，Mailpit `8025`（UI/API）。
如果构建失败，保留 `docker compose logs --no-color` 与构建输出。

## 3. 必跑脚本（按顺序）

```bash
./scripts/run-base-loop.sh          # base 场景 3/6 + 多 domain + SSE
./scripts/run-advanced-b2.sh        # advanced 场景 1（共享域地址级）
./scripts/run-base-independent.sh   # 场景 5 独立网关（/apply 系统码）+ 场景 6 无码
./scripts/run-base-lifecycle.sh     # pause/resume/no-unbind
./scripts/run-base-rotation.sh      # 同地址新码 -> kind=rotation
./scripts/run-tssdk-tests.sh        # dsh/openclaw/pi + 契约
./scripts/run-gateway-tests.sh      # base/advanced cargo 测试
```

每个脚本退出码必须为 0；脚本输出的 `.data/*.json` 是证据，保留。
特别注意：**独立网关场景 5 使用 `/apply` 申领的系统激活码，不是 admin-key/system-key；agent 对话里不应出现任何 key。**

## 4. 人工 webapp 流程

打开 `http://127.0.0.1:18080/apply`：

1. 邮箱验证（验证码在 Mailpit `http://127.0.0.1:8025` 或页面模拟收件箱）。
2. 申领系统码 `sys-...` → 交给 `aimail_system`。
3. 申领地址码 `addr-...` → 交给 `aimail_address`。
4. 确认 `aimail_gateway.json` 0600、无 `webhook_host/bridge_key`；`agentmail.json` 0600；无 9099 监听。 <!-- contract-allowed: design/acceptance doc must quote the contract surface verbatim -->

场景 1/6 的区别、CLI 2/3/4 的覆盖边界见：
- `tests/dsh-acceptance/SCENARIOS.md`
- `tests/dsh-acceptance/CLI-COVERAGE.md`

## 5. 通过标准

按 `tests/dsh-acceptance/README.md` 的验收矩阵逐项勾选；至少：

- base：系统申领/对接、默认工作员默认工作区、地址码绑定、多 domain、SSE 收信、pause/resume、no unbind、rotation。
- advanced：场景 1 共享域地址申领、B2 绑定、申请人发信收信。
- 独立网关：`/apply` 系统码 + `code+domain` 对接；场景 6 无码地址。
- tssdk：dsh/openclaw/pi 测试全绿；3 个契约 checker 通过。
- 网关：base 395+ passed；advanced Linux 全绿（若缺 machine-id，记录环境失败，不要造假）。

## 6. 报告格式

```text
执行人/日期：
commit: aimail=... gateway=... advanced=...
OS/Docker/Compose/Node/Rust：
结果：B1..B10 / A1..A5 / T1 / C1 每项 PASS/FAIL
失败项：命令、退出码、关键日志、Mailpit 邮件、截图/JSON 证据
未执行项与原因：
结论：可验收 / 不可验收
```

不要修改三个仓的代码；不要提交测试期改动；不要打 tag。
测试结束执行：

```bash
docker compose down -v
rm -rf .data
```
