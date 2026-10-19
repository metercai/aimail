# 第 4 轮：场景 1 发版重交（D7 / D8 已修，本地自验全绿）

> 出具方：实现方　接收方：验证方
> 回应对象：`handoff-agent-level-scenario-1-round3-verifier-hold.md`（HOLD，不 dispatch）
> 判定口径未改：不改判据、不指定实现、不扩范围。本轮只做 D7 / D8 两条阻塞的修复与自验。
> **CI 已绿**：[run 39292197770](https://github.com/metercai/aimail/actions/runs/39292197770)（`a6ccf82`，5/5 job success）。

---

## 0. 一页结论

| # | 缺陷 | 修法（一句话） | 你的清关条件 | 本轮自验 |
|---|---|---|---|---|
| D8 | `agent setup --host hermes` 在干净机器 rc=5 | 选步判据从**序列化文本**换成**结构**：`kind=="spawn"` ∧ `argv` 真含适配包 ∧ `when.runtime != "docker"`；dry-run 展开占位符；`--apply` 不再假报 `applied:true` | 干净机器 rc=0 且输出含 pip/uv 安装命令；CI `cli-gates` 绿 | `rc=0` 且打印真实命令（§2.3 原始输出）；CI `cli-gates` **success**（run 39292197770） |
| D7 | `drain_stuck` mock server 只读一次 socket，1–4 核必红 | mock 读到 `Content-Length` 声明的 body 齐了再回包、回包带 `Connection: close`、以客户端结束停 accept（删掉 `served < 4`） | `taskset -c 0` 连跑 10 次全过；CI 绿 | **10/10 PASS，每次 0.00s**（§3.3 原始输出）；CI `cli-gates` **success**（run 39292197770） |

产品代码改动面：`cli/src/cmd/agent.rs`（D8 面 + 同函数的重启提示口径）、`cli/src/core/repair.rs`（**仅测试**）、
`cli/tests/agent_bootstrap.rs`（**仅测试**）。SDK / 桥 / 网关 / advanced 零改动。逐文件见 §6。

---

## 1. 被判对象（新 SHA，工作树全净）

```
aimail            86837dd　（71488fd D8 → fc960c9 D7 → 86837dd 提示口径）　cli 0.1.56
                  二进制 target/debug/aimail md5 c6df4b89　cargo build 判定"无需重编"
aimail-advanced   339069e　1.0.2　c5075b2b　（本轮未动）
aimail-gateway    24d220f　1.0.4　b591d7ec　（本轮未动）
aimail-bridge     2dd0b88　（本轮未动）
```

本轮文档提交紧随 `86837dd` 之后（**其后仅 docs**：`git diff 86837dd..HEAD --stat` 只列本文件），
所以"代码 SHA = `86837dd`、HEAD = 该 docs 提交"两句话同时成立。

三仓 `git status --porcelain` 均为空。CLI 版本号**未动**（0.1.56 尚未发版，HOLD 轮无 tag）；
二进制 md5 由 `15c248cb` 变为 `c6df4b89` —— 只因本轮改了源码，属预期。

---

## 2. D8：`agent setup --host hermes` 干净机器 rc=5

### 2.1 根因（与你的定位一致）

`cli/src/cmd/agent.rs::package_setup` 的选步判据是**对整条步骤的序列化文本**做排除：

```rust
kind == "spawn" && body.contains(pkg) && !body.contains("docker")   // 旧
```

hermes 的首个包步是**本机** uv/pip 路径，其守卫写的是 `when.runtime_not = "docker"`。序列化后
`"runtime_not":"docker"` 同样命中 `contains("docker")` ⇒ 唯一的本机包步被滤掉 ⇒ `steps.is_empty()`
⇒ `reason="no package step in the platform table for this host"`、`rc=5`。

源码对照（`aimail platform spec --json` 的 hermes `install_steps`）：

```
step 0: kind=spawn  argv 含 aimailsdk  when={"path_exists":"{home}/hermes-agent","runtime_not":"docker"}
step 1: kind=spawn  argv 以 docker exec 开头  when={"runtime":"docker"}
```

### 2.2 修法

1. 选步判据改为结构判定 `is_local_package_step`：`kind=="spawn"` ∧ `argv`（逐元素）含适配包 ∧
   `when.runtime != "docker"`。**不再出现任何"按文本包含 docker"的排除**。
2. dry-run 打印的 argv 按同一条 `ctx` 展开模板 —— 旧输出把 `{python}` 原样当命令回给用户，
   现在给的是 `P='python3'` 这种可执行形态（`--host openclaw|pi|dsh` 无占位符，行为不变）。
3. `--apply` 的出口见 §2.4。

### 2.3 清关命令原始输出（命令 + 输出，未转述）

```txt
$ python3 -m venv /tmp/bare2 && rm -rf /tmp/h && mkdir -p /tmp/h
$ HOME=/tmp/h PATH=/tmp/bare2/bin:/usr/local/bin:/usr/bin:/bin ./cli/target/debug/aimail agent setup --host hermes; echo rc=$?
{
  "applied": false,
  "dry_run": true,
  "host": "hermes",
  "mechanism": "package",
  "package": "aimailsdk",
  "installed": null,
  "commands": [
    "sh -c P='python3'; if command -v uv >/dev/null 2>&1; then echo 'pkgmgr=uv'; uv pip install --python \"$P\" --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; else echo 'pkgmgr=pip'; \"$P\" -m pip --version >/dev/null 2>&1 || \"$P\" -m ensurepip --default-pip >/dev/null 2>&1 || \"$P\" -m ensurepip >/dev/null 2>&1 || true; \"$P\" -m pip install -q --upgrade --index-url \"${AIMAIL_PIP_INDEX:-https://pypi.org/simple/}\" aimailsdk; fi"
  ],
  "restart": []
}
rc=0
```

五个宿主同一条命令的 dry-run 也都 `rc=0` 且各给出自己的 native 命令（openclaw / pi / dsh / hermes / deerflow）。

### 2.4 `--apply` 在"未检测到宿主"时的裁定（你 §2 点名要我给的出口）

- 我**保留**宿主的 `when` 守卫：`path_exists {home}/hermes-agent` 是 hermes 自己的数据（`hosts/hermes.rs:29`），
  不是本函数的判断。宿主不在 ⇒ 没有可装的目标 ⇒ 什么都不做，是对的。
- 但**不许假报成功**。旧代码在此情形打 `applied:true`（并可能 `rc=5`），口径自相矛盾。现在的出口：

```txt
$ HOME=/tmp/h PATH=/tmp/bare2/bin:/usr/local/bin:/usr/bin:/bin ./cli/target/debug/aimail agent setup --host hermes --apply; echo rc=$?
{
  "applied": false,
  "host": "hermes",
  "mechanism": "package",
  "package": "aimailsdk",
  "installed": null,
  "reason": "no install step applies on this machine (step guards unmet: {\"path_exists\":\"/tmp/h/.hermes/hermes-agent\",\"runtime_not\":\"docker\"})",
  "restart": []
}
rc=5
```

  规则：`applied` 现在只在**至少一条步的守卫在本机成立**时才为 `true`；`rc=5` 的语义是
  "期望状态（包在位）未达成"，包已在位时该分支 `rc=0`（与 `steps.is_empty()` 分支既有约定一致）。
- dry-run **不看**守卫（它是"这个平台的包动作是什么"的预览，不是"现在会做什么"），所以干净机器上
  照样打印真实命令并 `rc=0` —— 这正是你 §2 的清关条件。
- 因此"`mechanism:"package"` 与 `reason:"no package step"` 同时出现"的自相矛盾输出已不存在：
  该 reason 现在只在平台表**真的没有本机包步**时才可能出现。

### 2.5 为什么旧断言被本机掩盖、现在为什么不会再被掩盖

- 旧测试只断言 `out.contains("aimailsdk")`；失败输出里 `"package": "aimailsdk"` 同样满足它 ⇒
  在本机（系统 python 装了 `aimailsdk`，走 `already` 非空 ⇒ `rc=0`）这条断言形同虚设。这是**测试缺陷**，
  你的 §8.2 说对了。
- 现在的断言**按分支判、与机器上装没装 SDK 无关**：
  `rc==0` ∧ `"dry_run": true` ∧ 含 `pip install` ∧ **不含** `docker exec`（容器步不得漏进本机模式）；
  另加一条 `--apply` 未检测到宿主 ⇒ `applied:false` ∧ 含 `no install step applies`。
- 变异验证（守卫真能红）：把选步判据临时改回文本判定 `body.contains(pkg) && !body.contains("docker")`
  ⇒ `package_hosts_show_their_native_command_without_touching_the_machine` **FAILED**；再 `git checkout` 还原。

### 2.6 同一函数的一处口径修正（同一 HOLD 面，明确报备）

`all_restart_hints` 原样回放平台表里所有 `print` 步，不看 `when` ⇒ 本机 dry-run 末尾会出现
`docker: 重启宿主 = docker restart {container}`（该步 `when.runtime="docker"`，且占位符没展开）。
现在提示行按**同一套守卫**过滤并展开模板，故本机模式 `restart: []`；容器用户仍能从
`aimail install --container` 拿到这条提示（那条路径以 `runtime=docker` 跑整张表）。
openclaw / pi 的提示（`when` 为空）行为不变。

---

## 3. D7：`drain_stuck` 假 HTTP server 只读一次 socket

### 3.1 机制（与你的判定一致）

一次 `read()` 取请求 ⇒ body 落在第二个 TCP 段时只拿到头（`:2146` 空 body）；每个 accept 只服务一个请求
且不再读该连接 ⇒ 客户端复用连接时 `Connection reset by peer`（`ack old failed`，`:2150` 那条断言随之红）；
另有 `served < 4` 硬上限。64 核本机常绿、1–4 核必红。

### 3.2 修法（只在测试内）

- 读到 `Content-Length` 声明的 body 齐了再回包（头未齐则继续读；`set_read_timeout(5s)` 兜住半开连接）。
- 回包带 `Connection: close`，处理完即关 ⇒ 客户端不再复用半读连接。
- accept 循环以**客户端结束**（`AtomicBool`）或 15s 兜底退出，删掉 `served < 4`。
- 未动产品代码（`drain_stuck` 本体不变）。

### 3.3 清关命令原始输出

```txt
$ taskset -c 0 cargo test --lib core::repair::tests::drain_stuck_posts_pending_then_acks_only_stuck   # ×10
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 175 filtered out; finished in 0.00s
```

每次 `0.00s`（旧写法在单核上吃满 15s 超时后判红）。修复前的同一条命令在本机 pin 单核为
`FAILED`（`:2150`，`Connection reset by peer`），与你 run 1 的现象一致。

---

## 4. 本地自证全量（最终 SHA `86837dd`，干净 PATH 口径）

| 门 | 命令 | 结果 |
|---|---|---|
| cli fmt | `cargo fmt --all --check` | rc=0 |
| cli clippy | `cargo clippy --all-targets -- -D warnings` | rc=0 |
| cli 测试（**干净 PATH**） | `PATH=/tmp/bare2/bin:/home/ubuntu/.cargo/bin:/usr/local/bin:/usr/bin:/bin cargo test --all-targets` | **24 套件 / 229 passed / 0 failed** |
| 单核定向（D7） | `taskset -c 0 cargo test --lib core::repair::tests::drain_stuck_…` ×10 | 10/10 PASS |
| 4 核 pin 全量 lib | `taskset -c 0-3 cargo test --lib` | **176 passed / 0 failed**（5.47s） |
| 注册表形状（CI 同款） | `python3 -m pytest tests/cli/ -q` | 12 passed |
| cli L2 配对 | `cargo test --features sdk-parity --test repair_backfill_parity` | 2 passed |
| A1（场景 1，L1+L2） | `A1_EVIDENCE=/tmp/a1-round4b bash tests/SDK/agent-level/gate.sh`（advanced） | l1-py 29 / l1-ts 13 / l2-py 14 / l2-ts 14 = **70 · 0 fail** ⇒ `GATE a1 all: PASS` |
| 产物一致性 | `cargo build`（离线） | 判定无需重编，md5 `c6df4b89` |
| **CI（4 核 runner）** | [run 39292197770](https://github.com/metercai/aimail/actions/runs/39292197770) @ `a6ccf82` | **5/5 job success**：`cli-gates`(fmt+clippy+test+pytest+sdk-parity) / `sdk-gates` / `build linux-amd64` / `build linux-arm64` / `build macos-arm64` |

A1 原始输出（节选，行号即 `gate.sh` 日志）：

```txt
── L1-PY: SDK↔网关契约(工作树 pysdk, 无宿主) ──
  RESULT: 29 cases · 29 pass · 0 fail
  evidence: /tmp/a1-round4b/l1-py.json
── L1-TS: 通用 Node 宿主 + 工作树 @aimail/mail-core ──
  RESULT: 13 cases · 13 pass · 0 fail
  evidence: /tmp/a1-round4b/l1-ts.json
── L2/py: 安装产物 + 通用语言宿主(无 CLI/bridge/平台适配层) ──
  RESULT: 14 cases · 14 pass · 0 fail
  evidence: /tmp/a1-round4b/l2-py.json
── L2/ts: 安装产物 + 通用语言宿主(无 CLI/bridge/平台适配层) ──
  RESULT: 14 cases · 14 pass · 0 fail
  evidence: /tmp/a1-round4b/l2-ts.json
  RESULT: PASS
GATE a1 all: PASS
```

**干净 PATH 口径说明（针对你的 §8.2）**：`/tmp/bare2` 是**新建空 venv 且未装 aimailsdk**，并放在 PATH 首位
⇒ `python3` 解析到该 venv ⇒ "已装 SDK ⇒ 走 `already` 非空分支" 的掩盖路径在本轮自证里不存在。

---

## 5. 未做 / 待你授权（区分"未做（范围成本）"与"不可为（约束）"）

| 项 | 状态 | 说明 |
|---|---|---|
| 新 SHA 上的 `ci.yml` run 链接 | **已产出（绿）** | [run 39292197770](https://github.com/metercai/aimail/actions/runs/39292197770) @ `a6ccf82`，5/5 job success（`cli-gates` 首绿：此前 run 39280603075 / 39286096020 / 39287716482 三跑全红）。 |
| `publish.yml` dry-run 形状 | 未做（按你 §7 由你做） | gate 绿后才有形状可核。 |
| L3 12 格、docker 五宿主冒烟、`SDK/sdk-e2e` | 未做（同上） | 需正式 dispatch 后的已发布产物 / 完整 RELAY_BIN 环境。 |
| 版本号 | 未动 | 0.1.56 未发版（HOLD 轮无 tag），重交不需要再 bump。 |

---

## 6. 改动面清单（逐文件，供你按"产品变更重新判定配对"）

| 文件 | 性质 | 内容 |
|---|---|---|
| `cli/src/cmd/agent.rs` | 产品 | ① `is_local_package_step`：结构判定选步（替掉文本 `contains("docker")`）② dry-run 展开模板 ③ `--apply` 守卫不成立时的出口（`applied:false` + 不成立守卫 + rc）④ `all_restart_hints` 按守卫过滤并展开 |
| `cli/src/core/repair.rs` | **仅测试** | `drain_stuck_posts_pending_then_acks_only_stuck` 的 mock server（§3.2）；`drain_stuck` 本体未动 |
| `cli/tests/agent_bootstrap.rs` | **仅测试** | hermes 断言按分支判（`dry_run` / `pip install` / 无 `docker exec`）+ 新增 `--apply` 守卫用例 |

未动：`pysdk/`、`tssdk/`、`cli/src/hosts/*`（含 `hermes.rs` 的步骤表）、`aimail-gateway/`、`aimail-advanced/`、`aimail-bridge/`。
`platforms.json` / 宿主表 = 零改动（守卫与命令原样保留，只改 CLI 的读法）。
