# 复验交接（第 2 轮）：场景 1（agent 级对话安装）—— HOLD 与必修项

> 出具方：验证方（最终审验与发版）。接收方：实现方（开发）。
> 判据归属不变：门禁判据/用例/断言/夹具在 `aimail-advanced/tests/SDK/agent-level/`，
> 由验证方所有；本文件只给**结论、证据、缺陷与清关条件**，不改判据。
> 上一轮交接：`docs/handoff-agent-level-scenario-1.md`（实现方出具）。
> 本轮对应被测：`aimail` b377f99（+ 验证方 bump b7a984b）、`aimail-gateway` 1ca59da（+ bump fe2f088）、
> `aimail-advanced` b7f207b（+ 门禁提交 6265d51、bump d581d53）。**未打 tag、未 dispatch、未发布。**

## 0. 一页结论

**VERDICT: HOLD。** 门禁主体已绿，但发布流程自己的 CLI 门禁红，不能发。

- **D5（Blocker，必改）**：`cargo clippy --all-targets -- -D warnings` 在
  `cli/src/cmd/agent.rs:734` 报 `collapsible_if`，b377f99 引入。publish.yml 的 `gate` job 就是
  `ci.yml` 的 `cli-gates`（fmt + clippy + test），所以整个 dispatch 会在这里失败。
- **D6（同轮必修，今天不单独构成门禁红）**：advanced 新代码 clippy 4 红 + gateway/advanced
  rustfmt 漂移，均由本批提交引入。根因是这两个仓没有 fmt/clippy 门禁，请同轮一起清干净。
- **三项门禁口径已裁并落地**（§4）：A 依赖序承认“本批待发”（默认仍严格）、B 工作流强制 CLI
  先落地、C docker/L3 判在发版后。实现方**不需要**改任何门禁文件。
  另见 **§5 的防死锁发布顺序**：npm 硬依赖链是 `mail-core → mail → 三个适配包`（core 最先上架）；
  CLI 必须先于 S1→S3 的手测；服务端（gateway/advanced）建议先于 SDK。
  §5.2 逐条列出“反过来就会死锁”的三个点。
- 清关条件（唯一）：D5 修好 → 新 SHA → 自验 L0+L1+L2 原始输出交回 → 验证方重跑全部门禁 → 向 owner 申请发版。

已通过（验证方本人重跑，原始证据见 §9）：A1 门禁 L1 29+13、L2 14+14（连跑两次一致）；
高级发版门禁 18/18 套件 PASS（含 warning-gate 0 warning、e2e-open-apply 42/0）；
SDK L0 PASS（pytest 277、vitest 288/37）；gateway 290 单测、advanced 94 单测、CLI 10+4 单测；
docs 一致性与对抗性断链；fail-closed rc=2；board 鉴权 4/4 为 401。

## 1. 判定与证据总表

| 层 | 门禁 / 命令 | 结果 | 证据 |
|---|---|---|---|
| L0 | `bash tests/sdk/l0-gate-tests.sh` | PASS（bump 前后各一次） | `/tmp/l0-gate.log`、`/tmp/l0-postbump.log` |
| L0 | `cargo fmt --all --check`（cli） | PASS | `/tmp/cli-fmt.log` |
| L0 | `cargo clippy --all-targets -- -D warnings`（cli） | **FAIL rc=101** | `/tmp/cli-gate.log` |
| L0 | `cargo test --lib`（gateway） | PASS 290 | `/tmp/advanced-release-gate.log` |
| L0 | `cargo test --bin aimail-advanced` | PASS 94 | 同上 |
| L0 | `cargo test --test cli_surface --test agent_bootstrap` | PASS 10 + 4 | — |
| L0 | `cargo clippy --offline --all-targets -- -D warnings`（advanced） | **FAIL 4 项** | `/tmp/adv-clippy.log` |
| L0 | `cargo fmt --all --check`（gateway / advanced） | **FAIL（漂移）** | `/tmp/gw-fmt.log`、`/tmp/adv-fmt.log` |
| L1 | `bash tests/SDK/agent-level/run-agent-level.sh l1` | PASS 29/29 + 13/13，fail=0 GAP=0 | `/tmp/a1-verify/` |
| L1 | 同一条命令再跑一次（确定性） | case 级完全一致 | `/tmp/a1-verify2/` |
| L2 | `gate.sh`（all，安装产物 wheel/tarball） | PASS 14/14 + 14/14 | `/tmp/a1-rel/`、`/tmp/a1-verify/` |
| 发版 | `bash tests/run-e2e.sh --release advanced` | **RELEASE GATE advanced: PASS**，18/18 套件 | `/tmp/advanced-release-gate.log`、`/tmp/e2e-logs/advanced-20261019-200325/SUMMARY.txt` |
| — | 文档一致性 + 对抗性改坏符号 | PASS / 改坏后 rc=1（3 违约），已还原 | `/tmp/docsbreak.log` |
| — | fail-closed：`ADV_BIN=/nonexistent gate.sh` | rc=2 CANNOT JUDGE | `/tmp/failclosed.log` |
| — | board 鉴权：agent key / 假 token / 无凭据 | 401 / 401 / 401（4/4） | `/tmp/board-auth-result.json` |
| — | `scripts/check-versions.sh` | 元数据 PASS；依赖顺序门禁红（发版前应有，见 §4.1） | 见 §4.1 |

二进制（均为源码重建）：advanced md5 `051fc40d`、cli `c2402bc1`、gateway `5f1f8e4a`。
`aimail version` = 0.1.56（dev），`aimail agent --help` / `doctor --questions` 正常。

## 2. D5 必修（Blocker）

**位置**：`cli/src/cmd/agent.rs:734`，函数 `pkg_present`，提交 b377f99。

**复现（一条命令）**：

```bash
cd ~/aimail/cli && cargo clippy --all-targets -- -D warnings
```

**期望**：rc=0。
**实际**：rc=101，原文：

```
error: this `if` statement can be collapsed
   --> src/cmd/agent.rs:734:5
    |
734 | /     if pkg == "aimailsdk" {
735 | |         if !python.is_empty() {
736 | |             if let Some(v) = sh(&format!(
...
    = help: for further information visit ...#collapsible_if
    = note: `-D clippy::collapsible-if` implied by `-D warnings`
error: could not compile `aimail` (lib) due to 1 previous error
```

**为什么是 Blocker**：`.github/workflows/publish.yml` 的 `gate` job 调用 `ci.yml`；
`ci.yml:71` 就是 `cargo clippy --all-targets -- -D warnings`，本地 clippy 1.98.1 与 CI stable 同一条判据。
门禁不过 ⇒ 四个平台构建、npm、PyPI、L3 都不会跑。

**修法**：按 clippy 建议把三层嵌套收成 `if pkg == "aimailsdk" && !python.is_empty() { ... }`
（或 `let Some(v) = ... else` 提前返回）。改完 `cargo fmt` + clippy 干净。

**配对声明**：CLI 独立版本线（0.1.56），与 SDK 无版本绑定；`scripts/agent-install.sh`
拉的是 release 资产，所以 CLI 必须先于文档里 S1→S3 的实测成立。

## 3. D6 必修（同轮清干净，不单独阻塞）

advanced（全部由 b7f207b 引入）：

```
src/advanced/board_quota.rs:36  error: doc list item without indentation
src/advanced/board_quota.rs:37  同上
src/advanced/board_quota.rs:38  同上
src/advanced/storage.rs:125     error: very complex type used. Consider factoring parts into `type` definitions
```

复现：`cd ~/aimail-advanced && cargo clippy --offline --all-targets -- -D warnings`（`/tmp/adv-clippy.log`）

rustfmt 漂移（同样只在新代码里）：

```
gateway   src/core/api/send.rs:1331, 1391
advanced  src/advanced/api/address.rs:242, 265
```

复现：`cargo fmt --all --check`（`/tmp/gw-fmt.log`、`/tmp/adv-fmt.log`）

说明：这两个仓没有 fmt/clippy 门禁，所以这批能走到发版前；CLI 仓有门禁，所以 D5 被当场抓住。
**验证方不会签一个“自己新代码过不了本仓 lint/format 门槛”的批次**；若实现方认为 D6 超范围，
请走 owner 裁决，不要静默跳过。

## 4. 门禁口径（已裁决；实现方不需要改判据，也不需要动这些文件）

> 三项裁决由验证方出，都属于发布机制/门禁工具，改动落在验证方自己的提交里。
> 实现方只按 §2/§3 修产品代码。裁决方向是**判据只增不减**。

### 4.1 裁决 A：SDK 依赖序承认“本批待发”，默认行为不变

原问题：

- `aimail-advanced/tests/run-e2e.sh:97`：`--release sdk` 的必跑集含
  `$VERSION_GATE` = `aimail/scripts/check-versions.sh`。
- `aimail/scripts/check-versions.sh:94-131`：依赖顺序检查要求依赖**已经上架**
  （`mail@0.1.55 depends on @aimail/mail-core@0.1.55 which is NOT on the registry`）。
- 于是发版前跑 SDK 门禁恒红，直到 mail-core 已发布——门禁自身顺序错误。
- 发布路径本身没有死锁：`publish.yml` 不调用 `check-versions.sh`；
  `publish-npm.sh:214` 按参数顺序发布，`publish.yml:269` 传的顺序是
  `mail-core → mail → dsh-aimail → openclaw-aimail → pi-aimail`；npm publish 也不校验依赖是否已上架。

裁决：`scripts/check-versions.sh` 增加 `RELEASE_BATCH`（逗号分隔、**按发布顺序**）。
放行一条“依赖未上架”必须同时满足三条：①依赖在本批清单；②依赖在清单里的位置**早于**依赖方；
③workspace 本地版本 == 依赖声明解析出的版本。缺一即红。
**不设 `RELEASE_BATCH` 时行为与原来完全一致**（CI 与第三方调用不受影响）。

已落地（验证方提交）：

- `scripts/check-versions.sh`：批次表 + 三条放行条件。
- `aimail-advanced/tests/run-e2e.sh:97`：`--release sdk` 自动
  `export RELEASE_BATCH=mail-core,mail,dsh-aimail,openclaw-aimail,pi-aimail`。
- 四组本地证据（`/tmp/cv-*.log`）：
  1. 不设批次 → rc=1，`... @aimail/mail-core@0.1.55 which is NOT on the registry`（严格性保留）
  2. 正确顺序 → rc=0，7 条 `pending(batch[i<j])`，`[L1] PASS`
  3. 顺序颠倒（mail 在 mail-core 之前）→ rc=1，
     `batch order wrong: mail(pos=0) depends on @aimail/mail-core(pos=1)`
  4. 依赖方未声明（只声明 core+mail，而 dsh 依赖 mail）→ rc=1，
     `dsh-aimail@0.1.59 ... itself is not in RELEASE_BATCH`
- `pending` 的含义是“依赖会在本批更早落地”，**不是**“依赖已存在”；
  发版后必须用不带批次的方式复跑一次并转绿（§5.4 第 6 条）。

### 4.2 裁决 B：工作流强制 CLI 先落地

裁决：不靠约定，写进 job 依赖。`publish.yml` 改为：

- `npm`：`needs: [gate, meta, cli-release]`；dry_run 时 cli-release 按设计 skipped，
  用 `if` 显式放行（只做 pack/校验，不发布）。
- `pypi`：`needs: [gate, meta, pypi-build, cli-release]`；任一前序非 success 则不发布。
- `l3`：维持 `needs: [cli-release, npm, pypi]`。

已落地与校验：job 图用 YAML 解析器验证（8 个 job 定义，npm/pypi 均依赖 cli-release，
`/tmp/hermes-verify-workflow.py` 输出）；语义验证在 dry-run（形状不变：构建全跑、
cli-release/npm/pypi/l3 按设计 skipped）+ 正式 run（npm 一定在 cli-release 之后）。

### 4.3 裁决 C：docker 多宿主与 L3 判在“发版后”，发版前用 A1 L2

事实：docker harness 在容器里从 registry 装适配包 ⇒ 发版前它测的必然是**旧包**；
且本机 `aimail-host-*` 镜像过期（4/5 容器缺 `/opt/regression/install-sdk.sh`）。

裁决：

- 发版前（§5.1 第 0 步）：A1 L2（wheel + npm tarball 安装产物）是 agent 级特性的
  **安装产物**判据，必须绿；docker 多宿主此时只记 CANNOT JUDGE，**不得写成产品红**。
- 发版后：L3 12 格（workflow 内）+ docker 五宿主（验证方重建镜像后跑）**必须**有结论；
  任一红 ⇒ 本批不得称“已验证”，只能前滚补丁版本。
- 修复工作（验证方，hermes 已验证通过）：dangling 镜像已清（回收 3.7G）。
  根因不是 pins 漂移：`docker/build.sh` 只把 `prepare.sh/verify.sh/pins.env` 拷进镜像，
  **漏了 `verify.sh` 依赖的 `agent-paths.sh`** ⇒ `agent_launcher: command not found`
  ⇒ `hermes installed='none'` ⇒ 误报 version drift。
  补上拷贝后 `build-hosts.sh hermes` rc=0，`AGENT-ENV VERIFY: PASS`（15 passed / 0 failed / 2 skipped），
  镜像内 `/opt/regression/install-sdk.sh` 在位（`/tmp/docker-rebuild-hermes2.log`）。
  其余四平台（openclaw/dsh/pi/deerflow）随后重建；完成前 docker 五宿主仍记 CANNOT JUDGE。
- 后续（不计入本批）：让 harness 支持本地适配包 tarball，使 docker 能在发版前判新版本。

### 4.4 sdk-e2e 的前置

`SDK/sdk-e2e/run-sdk-e2e.sh` 需要 `RELAY_BIN`（真网关）、`BRIDGE_BIN`、`TEST_MANAGER`。
验证方上一次调用缺这些，S1 实链被跳过、S2 死在 `register_agent_email` 的 manager 硬门
（该硬门来自 5137e34，非本批），**不计红**；会在补齐环境后重跑。

### 4.5 category-21 J.5 是长期条件性 WARN

本轮 `[WARN] J.5 skipped (no code found in delivered email)`；历史 `/tmp/e2e-logs` 统计为
53 WARN / 13 PASS，非本批引入。激活邮件路径已由 `e2e-open-apply`（42 PASS/0 FAIL）与 A1 门禁覆盖。
不建议本批处理，但请知悉：它不是 PASS。

## 5. 发布顺序（依赖图 + 防死锁顺序）

### 5.0 依赖图（谁必须先落地）

```
CLI 0.1.56 ─────────────────────────┐   独立版本线；agent-install.sh 拉的是 release 资产
                                    ├─→ L3 12 格（needs: cli-release + npm + pypi）
pysdk 0.1.56 (PyPI) ────────────────┤   独立线；不依赖 npm，可与 npm 并行
                                    │
mail-core 0.1.55 ─→ mail 0.1.55 ─→ dsh 0.1.59 / openclaw 0.1.58 / pi 0.1.57
        npm 依赖序：core 必须最先上架；其余包打包时 workspace:^ 会写成具体版本
gateway 1.0.4 ──(path 依赖)──→ advanced 1.0.2    同批 tag；advanced 编译期编入 gateway
```

要点：

- **npm 是硬依赖链**：core → mail → 三个适配包。publish-npm.sh:214 已按这个顺序发布，
  publish.yml:269 传入的清单也是这个顺序，**不要调整**。
- **适配包必须重发**：它们 bundle 了 mail-core，核心包内容变了但适配包版本不动 ⇒ 用户装到的仍是旧 core。
- **服务端与 SDK 之间是加法兼容**：新服务端对旧 SDK 不破坏（只新增响应字段、把 500 收敛成 4xx）；
  新 SDK 对旧服务端降级可用（拿不到 `manager_address` 就不写该键）。两侧顺序可交换，
  但**推荐服务端先**，避免“新 SDK 已上、D1/D3/闭环通知尚未生效”的半功能窗口。
- **L3 自带顺序保证**：l3 job 的 `needs: [cli-release, npm, pypi]`，所以矩阵一定在 CLI 资产与
  三个 registry 都落地之后才跑；不要在它之外另起并行手测。

### 5.1 执行顺序（一步一判据，任何一步红就停在原地）

| # | 动作 | 必须绿的判据 | 失败的影响 |
|---|---|---|---|
| 0 | 修 D5/D6，交新 SHA | 三仓 fmt/clippy/test + SDK L0 + A1 L1（连跑两次一致）+ A1 L2 | 无副作用（未 push、未发布） |
| 1 | push 版本提交 | `scripts/check-versions.sh` 元数据段 PASS；工作树干净；版本号是 workflow 的真源 | 无副作用 |
| 2 | `publish.yml` **dry_run=true** | 4 平台 CLI 构建、npm pack、PyPI build 全绿；`cli-release`/`npm`/`pypi`/`l3` 按设计 skipped | 无副作用（不建 tag、不发布） |
| 3 | `publish.yml` **dry_run=false** | gate(ci.yml) → cli-build×4 → **cli-release：`cli-v0.1.56` + 4 资产** ∥ **npm：core→mail→dsh→openclaw→pi** ∥ **PyPI：0.1.56** → **L3 12 格** | **不可逆**：已发布版本不能撤回，只能前滚补丁版本 |
| 4 | 发版后硬证据（见 §5.4） | 资产 sha256/大小/版本；PyPI integrity+provenance；npm 可见性 + `npm pack` 内容；干净环境 driver_py/driver_ts；`agent-install.sh → aimail version → doctor`；`check-versions.sh` 依赖序**此时转绿** | 红只能前滚 |
| 5 | 服务端：gateway 1.0.4 + advanced 1.0.2 打 tag，advanced 部署 | 部署前快照、回滚命令就位、health 200、25/80/443、重启后无 panic/error | **需 owner 单独授权，本次未授权** |
| 6 | 部署后复验 | A1 L2 对**已发布产物**再跑一次；docker 五宿主（镜像重建后）；有平台时补真机冒烟 | 记为未验证，不改变已发布事实 |

> 注：`tests/run-e2e.sh --release sdk` 全档不在第 0 步使用。`check-versions.sh` 的依赖序死锁
> 已按 §4.1 解除（批次内 pending），但全档还包含 `sdk-e2e` 与 docker 五宿主——二者要从 registry
> 装适配包，发版前判不了（§4.3/§4.4）。第 0 步 SDK 侧判据 = L0 + A1(L1/L2)；
> 全档在发版后用同一条命令复跑，必须全绿。

### 5.2 三个“反过来就会死锁”的点

1. **先发 mail / 适配包，后发 mail-core** → 死锁。
   打包时 `workspace:^` 会被写成具体版本，适配包 tarball 里就是 `@aimail/mail-core@0.1.55`；
   core 不在 registry ⇒ 用户 `npm i` 直接失败。所以 core 最先上架；
   publish-npm.sh 的既有顺序已经正确，**不要为了“并行快一点”改它**。
2. **在 core 上架前跑 SDK 发版门禁并期望它绿** → 原本会死锁，**已按 §4.1 解除**：
   门禁现在承认“本批待发”（依赖在本批清单、位置更早、版本一致三条同时成立），
   默认（不设 `RELEASE_BATCH`）仍是原来的严格判据。
   仍然禁止：为了让它变绿删掉依赖序检查，或把某条 pending 当成“已上架”。
   发版后必须用**不带批次**的方式复跑一次并转绿。
3. **先发 SDK、后发 CLI，并在中间跑 S1→S3** → 安装侧不可用。
   `agent-install.sh` 拉最新 `cli-v*` 资产；0.1.55 没有 `agent` 子命令 ⇒
   安装器能装、`aimail agent ...` 不存在。L3 由 needs 保证不会在这个窗口跑；
   但**人工**按 §10.4 验收时必须先确认 `cli-v0.1.56` 已出现，不能并行手测。

### 5.3 版本与配对声明

| 顺序 | 包 / 仓 | 现在 → 目标 | 配对 / 影响面 |
|---|---|---|---|
| 0 | CLI（`cli-v*`） | 0.1.55 → **0.1.56** | 独立版本线，与 SDK 无版本绑定；`agent-install.sh` 的前置；S1→S3 的实测门槛 |
| 3 | `@aimail/mail-core` | 0.1.54 → **0.1.55** | npm 依赖链的根；必须先上架 |
| 3 | `@aimail/mail` | 0.1.54 → **0.1.55** | 依赖 core，五包齐升（本包无代码变更，仅对齐） |
| 3 | openclaw / pi / dsh | 0.1.57/0.1.56/0.1.58 → **0.1.58/0.1.57/0.1.59** | bundle 内 core 变了 ⇒ 三个都必须重发，否则用户拿到旧 bundle |
| 3 | pysdk `aimailsdk` | 0.1.55 → **0.1.56** | 与 mail-core 语义等价，可与 npm 并行；PyPI 独立线 |
| 5 | gateway | 1.0.3 → **1.0.4** | D3 线程头归一的单点修；advanced 以 path 依赖编入，两者同批 tag |
| 5 | advanced | 1.0.1 → **1.0.2** | D1 白名单、D2 错误映射、激活响应 `manager_address`、按系统板配额 |

回滚（逐项、可执行）：

- npm：逐包回退 tag/版本；适配包必须与 mail-core 同批回退，避免 bundle 内 SKILL.md 与核心包语义不一致。
- PyPI：回退到 0.1.55（旧版本仍在，`pip install aimailsdk==0.1.55`）。
- CLI：重新 dispatch 上一版本（`cli-v0.1.55`）；`agent-install.sh --version cli-v0.1.55` 可钉住。
- gateway/advanced：tag 保留、Release 可下架；**本批无 DB schema 变更**，advanced 回退后旧 SDK 仍可用
  （只是没有 manager/快照等新行为）。
- 部署回滚命令（服务端）：`mv -f /usr/local/bin/aimail-advanced.prev-<oldver> /usr/local/bin/aimail-advanced
  && systemctl restart aimail-advanced`（部署前必须确认备份文件存在）。

### 5.4 发版后硬证据（不是“job 绿”）

```bash
# 1) 资产：4 个平台，数量 == 构建矩阵 == release notes 行数；逐个 sha256 + 运行版本
gh release view cli-v0.1.56 --json assets -q '.assets[] | "\(.name) \(.size)"'
# 2) PyPI：版本 + 上传时间 + provenance（workflow=publish.yml, environment=pypi-release）
curl -s https://pypi.org/pypi/aimailsdk/0.1.56/json | jq -r '.info.version, .urls[].filename'
# 3) npm：可见性 + tarball 内容（resources/skills/SKILL.md 必须在）
npm view @aimail/mail-core@0.1.55 version
npm pack @aimail/mail-core@0.1.55 --dry-run 2>&1 | grep -c "resources/skills/SKILL.md"
# 4) 干净环境：只装发布物，跑 agent 级链路
pip install aimailsdk==0.1.56 && python -c "import aimail, aimail_agent; print(len(aimail_agent.agent_toolset()))"   # 期望 15
npm i @aimail/mail-core@0.1.55 && node -e "console.log(require('@aimail/mail-core').agentTools().length)"          # 期望 15
# 5) 安装器：装到的是带 agent 子命令的 0.1.56
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/agent-install.sh | bash
aimail version && aimail agent doctor --questions | head -3
# 6) 依赖序：此时必须绿（也证明 §5.2-2 的死锁已解）
bash scripts/check-versions.sh | tail -2
```

## 6. 重交要求（按门禁验收口径）

1. **新 SHA 的完整请求**，不继承本轮任何结果（本轮唯一例外：版本号已由验证方 bump，可直接沿用）。
2. 自验并**贴原始输出**（命令 + RESULT 行 + JSON 路径），至少：
   ```bash
   cd ~/aimail/cli && cargo fmt --all --check && cargo clippy --all-targets -- -D warnings && cargo test --all-targets
   cd ~/aimail-advanced && cargo clippy --offline --all-targets -- -D warnings && cargo fmt --all --check
   cd ~/aimail-gateway && cargo fmt --all --check && cargo clippy --offline --all-targets -- -D warnings
   cd ~/aimail && bash tests/sdk/l0-gate-tests.sh
   cd ~/aimail-advanced && cargo build --bin aimail-advanced && A1_EVIDENCE=/tmp/<dir> bash tests/SDK/agent-level/gate.sh
   ```
3. 缺陷映射逐条对得上：D5 → cli clippy；D6 → advanced clippy 4 项 + fmt 4 处。
4. 工作树干净、二进制新鲜（源码新于二进制 ⇒ 门禁 rc=2）、无本机残留依赖。
5. §4 的三项裁决已由验证方落地（依赖序 pending、工作流强制 CLI 先、docker/L3 判在发版后），
   实现方不需要回答、也不需要改这些文件；只要求确认自己**没有**改 `tests/SDK/agent-level/`、
   `scripts/check-versions.sh`、`.github/workflows/publish.yml`。

## 7. 不要做

- 不改 `tests/SDK/agent-level/` 的 cases/断言/夹具/入口去让红变绿；判据有异议走裁决。
- 不加 `#[allow(clippy::...)]` 把 D5/D6 压成绿；不跳过测试、不放宽阈值、不加 allow-list。
- 不为了让 sdk-e2e 或 docker 回归“看起来绿”而改环境断言。

## 8. 验证方未验证（不冒充通过）

- 发布产物与 L3 12 格：没有 dispatch，无物可验。
- docker 五宿主 L2：CANNOT JUDGE（§4.3，验证方工装待修）。
- sdk-e2e：环境不完整（§4.4），未计入。
- pi/dsh/openclaw/deerflow 真机冒烟：本机不具备这些平台。
- 上一轮交接文档与实测的偏差（请顺手修正）：§0 的 `b377f99` 实际是 `e56b9d1`（两个文档提交）；
  §2.2 的 275/287/35 与实测 277/288/37 不一致；§2.5 第 2 条（改坏自举文档的契约值应变红）
  已失效——按设计自举文档只带 0 个契约值，验证方改用“改坏符号”完成对抗性验证。

## 9. 证据索引（本机，验证方保留）

```
/tmp/a1-verify/            本轮 A1 门禁 all（四个 JSON + gate.log），GATE a1 all: PASS
/tmp/a1-rel/               advanced 发版门禁内的 A1 复跑（同一二进制 051fc40d）
/tmp/a1-verify2/           L1 第二次（确定性对照）
/tmp/advanced-release-gate.log
/tmp/e2e-logs/advanced-20261019-200325/   SUMMARY.txt + 18 个套件日志
/tmp/l0-gate.log /tmp/l0-postbump.log     SDK L0（bump 前/后）
/tmp/cli-gate.log           CLI 域门禁（D5 红）
/tmp/adv-clippy.log /tmp/gw-clippy.log    clippy（advanced 红 / gateway 绿）
/tmp/gw-fmt.log /tmp/adv-fmt.log          rustfmt 漂移原文
/tmp/docsbreak.log          文档对抗性（改坏符号 → 3 违约）
/tmp/failclosed.log         ADV_BIN 缺失 → rc=2
/tmp/board-auth-result.json board 鉴权 401/401/401
/tmp/docker-l2.log          docker 五宿主（镜像过期，CANNOT JUDGE）
/tmp/sdk-e2e.log            sdk-e2e（前置缺失，不计）
```

反馈路径：实现方按本文修完 → 新 SHA + 自验原始输出 → 验证方复验 → owner 发版授权。
