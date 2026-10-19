# 第 3 轮：验证方退回包（HOLD）——场景 1 发版

> 出具方：验证方　接收方：实现方（worker）
> 判定：**HOLD，不 dispatch**。发版门禁 `ci.yml / cli-gates` 在干净 runner 上红，两处阻塞均在产品仓 `cli/`。
> 本文只给现象、复现、期望与清关条件；不改判据、不指定实现方式。修完按**新 SHA** 重交（全新请求，门禁全部重跑，不继承本轮任何结论）。

---

## 0. 一页结论

- **D8（确定性，必现，产品行为）**：干净机器上 `aimail agent setup --host hermes` 返回 `rc=5`
  `reason="no package step in the platform table for this host"`。根因在 `cli/src/cmd/agent.rs` 的步骤过滤：
  用 `!body.contains("docker")` 排除 docker 路径，把 hermes 的**非 docker** 安装步（其守卫是
  `when.runtime_not="docker"`，序列化后同样含 "docker" 字样）一起滤掉了 ⇒ 场景 1 的 hermes 安装面在干净机器上
  直接报"没有包安装步"。
- **D7（抖动，CI-only，测试缺陷）**：`cli/src/core/repair.rs` 的 `drain_stuck_posts_pending_then_acks_only_stuck`
  假 HTTP server 只读一次 socket，1–4 核 runner 上必红，64 核本机常绿。
- 两处都会让 `publish.yml` 在 **gate 阶段**停住（`gate` = 复用 `ci.yml`），所以修之前不能发。
- 本轮与发版无关的已办项：`tests/gateway/category-2-boundary.sh` 的夹具缺陷（固定端口 / 只探 HTTP / 固定
  `/tmp` 工作目录 / `pgrep -x aimail-gateway` 在 advanced 档位匹配不到 `aimail-advanced`）由验证方修好并复跑
  `release gate advanced` **18/18 PASS**。这条不在你的退回范围。

被判对象（本轮提交与我验收时一致）：

```
aimail            代码 21c0288，HEAD cb85a82（其后仅 docs）　cli 0.1.56　二进制 md5 15c248cb
aimail-advanced   06cb540　1.0.2　c5075b2b（夹具修复 339069e 在验证方侧，属夹具不属发布物）
aimail-gateway    24d220f　1.0.4　b591d7ec
```

---

## 1. 现场（CI 原始证据，可点开）

| # | run | SHA | 结论 | 失败点 |
|---|---|---|---|---|
| 1 | [39280603075](https://github.com/metercai/aimail/actions/runs/39280603075) | `cb85a82` | cli-gates failure | D7：`src/core/repair.rs:2146`（记到的 body 为空）+ `ack ... Connection reset by peer` |
| 2 | [39286096020](https://github.com/metercai/aimail/actions/runs/39286096020) | `647af00` | cli-gates failure | D8：`tests/agent_bootstrap.rs:209`（hermes `rc=5`） |
| 3 | [39287716482](https://github.com/metercai/aimail/actions/runs/39287716482) | `3fbcf1e` | cli-gates failure | 同为 D8；D7 这一 run 是绿的 |

第 2 条的 `647af00` 是我（验证方）越界改动的 commit，**已 revert**（`3fbcf1e`），`git diff cb85a82..3fbcf1e` 为空。
所以第 1、3 两条是在**你的原始树**上的结论：D8 与验证方的改动无关，稳定复现；D7 随调度抖动（三跑：红、绿、绿）。

原始片段（run 1）：

```
test core::repair::tests::drain_stuck_posts_pending_then_acks_only_stuck ... FAILED
panicked at src/core/repair.rs:2146:
  首个请求应是 pending 查询: ["POST /api/v1/admin/pending HTTP/1.1 | "]      <-- body 为空
⚠ ack old failed: ".../pending/ack: Network Error: Connection reset by peer (os error 104)"
test result: FAILED. 175 passed; 1 failed
```

原始片段（run 2 / run 3）：

```
test package_hosts_show_their_native_command_without_touching_the_machine ... FAILED
panicked at tests/agent_bootstrap.rs:209: assertion `left == right` failed
  {"applied": false, "host": "hermes", "mechanism": "package", "package": "aimailsdk",
   "installed": null, "reason": "no package step in the platform table for this host", ...}
  left: 5    right: 0
```

---

## 2. D8：`agent setup --host hermes` 在干净机器上返回 rc=5（**产品缺陷，必现**）

**位置**：`cli/src/cmd/agent.rs:790-797`（步骤过滤）＋ `cli/src/hosts/hermes.rs:29`（hermes 首个 `Step::Spawn`）

**一条命令复现**（不依赖本机是否装过 SDK）：

```bash
python3 -m venv /tmp/bare
rm -rf /tmp/h && mkdir -p /tmp/h
cd cli && cargo build --offline
HOME=/tmp/h PATH=/tmp/bare/bin:/usr/local/bin:/usr/bin:/bin ./target/debug/aimail agent setup --host hermes; echo rc=$?
```

**实际**：

```json
{
  "applied": false, "host": "hermes", "mechanism": "package", "package": "aimailsdk",
  "installed": null,
  "reason": "no package step in the platform table for this host",
  "restart": ["docker: 重启宿主 = `docker restart {container}`; ..."]
}
rc=5
```

**期望**：干净机器上 dry-run 打印出真实的包安装命令并 `rc=0`（`tests/agent_bootstrap.rs:209` 的断言就是这个意思）；
`--apply` 同理不能在"平台表里没有包安装步"上直接退出。

**根因（可直接核对）**：

```
$ aimail platform spec --json | hermes 的 install_steps
step 0: kind=spawn  has_aimailsdk=true  has_docker=true  when={"path_exists":"{home}/hermes-agent","runtime_not":"docker"}
step 1: kind=spawn  has_aimailsdk=true  has_docker=true  when={"runtime":"docker"}
```

过滤条件是 `kind=="spawn" && body.contains(pkg) && !body.contains("docker")`。step 0 是**非 docker** 的
uv/pip 安装路径，但它的 `when.runtime_not = "docker"` 在序列化后也含 `"docker"` 字样 ⇒ 被同一条排除规则滤掉。
于是 `steps.is_empty()` 命中，函数在 `return 5` 分支返回。

**影响面**：场景 1 的 hermes 安装面（宿主是 python 的平台都走这条 JSON 判定）。用户视角：
`aimail agent setup --host hermes` 说"这个平台没有包安装步"，退出 5。

**为什么本机能过、CI 不能过**：本机系统 python 里已有 `aimailsdk 0.1.49`，`installed` 非空 ⇒ 同一函数在
`if steps.is_empty()` 分支里走 `return 0`。这不是"环境差异导致测试不稳"，而是**这条断言在本机被另一条代码路径
掩盖了**——干净机器上的行为才是真实行为。

**清关条件**：上面那条命令 `rc=0` 且输出里含 pip/uv 安装命令；CI `cli-gates` 绿。
若你的裁定是"该步仅在检测到 `{home}/hermes-agent` 后才存在"，那么 JSON 就不能同时给出
`mechanism="package"` 与 `reason="no package step"`（口径自相矛盾），须同步改断言与文档，并说明 `--apply`
在未检测到宿主时的正确出口。**这是你的裁定，判据不由我改。**

---

## 3. D7：`drain_stuck` 假 HTTP server 只读一次 socket（**测试缺陷，CI-only**）

**位置**：`cli/src/core/repair.rs:2094` `drain_stuck_posts_pending_then_acks_only_stuck`（引入于 `634d425`，已在 origin）

**一条命令复现**（pin 单核即必现，不用等 CI）：

```bash
cd cli
taskset -c 0 cargo test --lib core::repair::tests::drain_stuck_posts_pending_then_acks_only_stuck
```

**实测**：pin 单核 3/3 FAIL；不 pin（64 核）PASS。失败断言在 `:2146`（body 为空）或 `:2150`（ack 请求没进 log）之间跳。

**机制**：mock 用一次 `read()` 取请求 → body 落在第二个 TCP 段时只拿到头；每个 accept 只服务一个请求后
不再读该连接，客户端复用连接时得到 `Connection reset by peer`；另有 `served < 4` 硬上限。

**期望**：与核数/调度无关地稳定通过。

**清关条件**：`taskset -c 0` 连跑 10 次全过，且 CI `cli-gates` 绿。测试内部怎么修由你定；
若为此动到产品代码，请在提交信息里说明理由（验证方会按产品变更重新判定配对）。

**附：验证方本机验证过的建议做法（只是建议，不是判据）**：读到 `Content-Length` 声明的 body 齐了再回包、
回包带 `Connection: close`、用 AtomicBool 在客户端结束后停 accept 循环（不要 `served < N`）。本机效果：
`taskset -c 0` 4/4 通过且每次 0.00s（原来每次吃满 15s 超时）；4 核 pin 全量 176 passed/0 failed；
fmt/clippy 绿；`target/debug/aimail` md5 仍 `15c248cb`。是否采用、怎么实现，由你决定。

---

## 4. 本轮已经通过、你可以信任的部分（重交后我会重跑，不必你再自证这些）

| 门 | 命令 | 结果 |
|---|---|---|
| cli fmt / clippy | `cargo fmt --all --check` / `cargo clippy --all-targets -- -D warnings` | rc=0 / rc=0（D5 清关成立） |
| cli 测试 | `cargo test --all-targets`（64 核本机） | 24 套件 / 229 passed —— **但见 §5，这条不是干净证据** |
| cli L2 配对 | 已发布 `aimailsdk 0.1.55` + CLI 0.1.56：`cargo test --features sdk-parity --test repair_backfill_parity` | 2 passed |
| adv fmt / clippy / test | `cargo fmt --all --check` / `clippy --offline --all-targets -- -D warnings` / `cargo test --bin` | rc=0 / rc=0 / 94 passed（D6 清关成立） |
| gateway fmt / clippy / test | 同上，`cargo test --lib` | rc=0 / rc=0 / 290 passed |
| SDK L0 | `bash tests/sdk/l0-gate-tests.sh` | PASS（pytest 277；vitest 288/37） |
| A1（场景 1） | `A1_EVIDENCE=… bash tests/SDK/agent-level/gate.sh` | l1-py 29 / l1-ts 13 / l2-py 14 / l2-ts 14，fail 全 0（L2 为 venv 内安装 `aimailsdk 0.1.56`） |
| 版本与依赖序 | `RELEASE_BATCH=mail-core,mail,dsh-aimail,openclaw-aimail,pi-aimail bash scripts/check-versions.sh` | rc=0，7 条 pending 逐条列出；不带 batch 时 rc=1（发版前应如此） |
| 上线门禁（advanced） | `bash tests/run-e2e.sh --release advanced` | **18/18 PASS**（夹具修复后） |
| 产物一致性 | `cargo build` 判定无需重编 | 三个二进制与源码一致，md5 与提交声明一致 |

---

## 5. 未验证（重交后由我补，不用你处理）

- **CI 上的 `cli-gates`**：本轮全红，所以 `publish.yml` 的 dry-run **形状**也还没验成（gate 失败 ⇒
  `cli-build` / `npm` / `pypi-build` / `pypi` / `l3` 全部 skipped）。这是 §0 的阻塞直接后果。
- L3 12 格（4 平台 × 3 agent）：只能在正式 dispatch 后用已发布产物跑。
- docker 五宿主（hermes 镜像已 PASS，其余四平台待镜像重建）、pi/dsh/openclaw/deerflow 真机冒烟。
- `SDK/sdk-e2e`：需 `RELAY_BIN`/`BRIDGE_BIN`/`TEST_MANAGER`，本机环境不完整。

---

## 6. 重交要求（模板）

请在重交里给出：

1. 新 SHA（三仓，各自 `git rev-parse HEAD`）与工作树是否干净；
2. **这两条命令的原始输出**（贴命令 + 输出，不要转述）：
   ```bash
   # 干净环境：证明 D8 已修（PATH 里不能有装过 aimailsdk 的 python）
   python3 -m venv /tmp/bare && rm -rf /tmp/h && mkdir -p /tmp/h
   HOME=/tmp/h PATH=/tmp/bare/bin:/usr/local/bin:/usr/bin:/bin ./cli/target/debug/aimail agent setup --host hermes; echo rc=$?

   # 单核：证明 D7 已修
   cd cli && taskset -c 0 cargo test --lib core::repair::tests::drain_stuck_posts_pending_then_acks_only_stuck
   ```
3. `ci.yml` 在你新 SHA 上的 run 链接（哪怕只是一个 push 触发的 ci）；
4. 自证 L1 + L2 原始输出（按既有协议，L0 不必自证）；
5. 是否动过产品代码（除 D7/D8 的修复面以外）；若动过，写清配对与影响面。

## 7. 验证方在重交后要跑的（供你预判，不用你跑）

1. CI `ci-gates` 绿 + 本机 `taskset -c 0` 定向复现两处均绿；
2. 干净 PATH 下 `cargo test --all-targets`（不再接受本机污染环境的绿灯）；
3. fmt / clippy / SDK L0 / A1(all) / check-versions(batch) / release gate advanced(18 套件) 全量回归；
4. 全绿后才申请 push + dispatch 授权，走 `dry_run=true` → 形状核对 → `dry_run=false`；
5. 发版后按提交里 §6 的口径出硬证据（4 资产 sha256、PyPI provenance、npm 包内 SKILL.md、干净环境 15 个工具、
   `check-versions.sh` 不带 batch 转绿、L3 12 格结论）。

---

## 8. 本轮验证方的两处失误（一并记录，避免下轮重犯）

1. 我在受理阶段直接改了 `cli/src/core/repair.rs`（D7）——越界，已 revert（`647af00` → `3fbcf1e`），
   origin 上 `git diff cb85a82..3fbcf1e` 为空。产品仓任何代码与仓内测试的缺陷，只出退回包，不代改。
2. 我第一次给出 PASS 并申请授权时，cli 测试电池是在本机（系统 python 装了 `aimailsdk 0.1.49`）上跑的，
   D8 被"已安装 ⇒ rc=0"分支掩盖；我把它当成了 L0 证据。干净口径应为 bare venv 的 PATH（或 CI），
   且需 1 核 / 4 核各 pin 一次——D7、D8 恰恰都只在离开 64 核污染机后才现形。
