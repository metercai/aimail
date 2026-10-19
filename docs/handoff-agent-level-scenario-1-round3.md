# 交付交接（第 3 轮）：场景 1 —— D5/D6 清关，请复验

> 出具方：实现方（开发）。接收方：验证方（最终审验与发版）。
> 上游交接：`docs/handoff-agent-level-scenario-1-round2.md`（验证方，HOLD + D5/D6）。
> 判据归属不变：门禁判据/用例/断言/夹具在 `aimail-advanced/tests/SDK/agent-level/`，
> 由验证方所有；本文只做缺陷映射与自验原始输出，不改判据。

## 0. 一页结论

**本轮只做 D5 + D6，不动产品行为。**

- **D5（Blocker）已修**：`cli/src/cmd/agent.rs:733` `pkg_present` 的三层嵌套收成单条件 +
  `Option::filter`；`cargo clippy --all-targets -- -D warnings` rc=0（上一轮 rc=101）。
- **D6 已清**：advanced clippy 4 项（`doc_lazy_continuation` ×3、`type_complexity` ×1）归零；
  gateway/advanced rustfmt 漂移全部归零（含验证方汇总未列出的 gateway `webhook.rs` 3 处）。
  advanced clippy rc=0、三仓 `cargo fmt --all --check` rc=0。
- **回归**：CLI 24 套件/229 passed、gateway `--lib` 290、advanced `--bin` 94，全绿；
  SDK L0 PASS（pytest 277）；A1 `gate.sh` all **PASS 29/13/14/14**，连跑两次 **case 级完全一致**，
  提交后再跑一次仍 PASS。
- **需你裁决一项**：`cargo fmt --all --check` 会把 path 依赖 `vendor/mailin` 一并纳入
  （252cab0 起就不符合，加 `[workspace] exclude` 实测无效）。本批把它一并格式化，
  才能让**你声明的那条命令** rc=0；若你坚持 vendored 不重排，则判据要退成 `cargo fmt --check`
  （包范围）。二者只能选一，我没有改判据，见 §4。
- 版本号沿用你的 bump（CLI 0.1.56 / advanced 1.0.2 / gateway 1.0.4）；
  **未 push、未打 tag、未 dispatch、未发布。**

## 1. 被测物（新 SHA）

| 仓 | 上一轮 | 本轮 HEAD | 提交内容 |
|---|---|---|---|
| `aimail` | e56b9d1 | **21c0288** | `cli/src/cmd/agent.rs`（D5）+ `docs/handoff-agent-level-scenario-1.md`（你 §8 的三处修正） |
| `aimail-advanced` | d581d53 | **06cb540** | `board_quota.rs` + `storage.rs` 两处 lint，`address.rs`/`storage.rs`/`strategy.rs` 的 rustfmt |
| `aimail-gateway` | fe2f088 | **24d220f** | `send.rs`/`webhook.rs` 的 rustfmt + `vendor/mailin/src/parser.rs` 一处（见 §4） |

> 本文档自身是 `21c0288` 之后的 docs 提交（当前 HEAD）。引用被测代码请用上表的 SHA 或
> `git rev-parse HEAD~1`，不要拿 HEAD 当代码 SHA —— 上一轮就是在这里错位成 `b377f99`。

二进制（源码重建，freshness 检查通过）：

```
aimail           0.1.56 (dev)   md5 15c248cb
aimail-advanced  1.0.2          md5 c5075b2b
aimail-gateway   1.0.4          md5 b591d7ec
find ~/aimail-advanced/src ~/aimail-gateway/src -name '*.rs' -newer target/debug/aimail-advanced
  → 空（0 个源码新于二进制）
```

工作树：三仓 `git status --short` 均为空。**未 push**（等你复验 + owner 发版点头，见 §7）。

## 2. D5 / D6 逐条映射

### 2.1 D5（Blocker）：`collapsible_if` @ `cli/src/cmd/agent.rs:734`

修法（无 `#[allow]`，无行为变化）：

```rust
if pkg == "aimailsdk" && !python.is_empty() {
    if let Some(v) = sh(&format!(
        "{} -c \"import importlib.metadata as m;print(m.version('{}'))\" 2>/dev/null",
        shell_escape(python),
        pkg
    ))
    .filter(|v| !v.is_empty())
    {
        return format!("import ok, version {v}");
    }
}
```

语义等价：`aimailsdk` + 有解释器 + 探针输出非空 ⇒ 返回版本；否则继续走目录 / `npm root -g` 探针，
空串仍视为未命中（原来靠 `if !v.is_empty()`，现在靠 `Option::filter`）。

### 2.2 D6：advanced clippy 4 项

| 位置 | 判据 | 修法 |
|---|---|---|
| `board_quota.rs:36-38`（3 项） | `doc_lazy_continuation` | 在 bullet 列表与新段落之间补空行 `///`（`Counting globally …` 是新段落，不是列表续行） |
| `storage.rs:125`（1 项） | `type_complexity` | 新增 `type BoardCountCache = Arc<RwLock<Option<(Instant, Option<String>, usize)>>>;`，与既有 `BlacklistCache` 同款；字段内联 `// (scanned_at, scope, count)` 注释移进别名文档 |

无 `#[allow]`、无阈值放宽、无 allow-list。

### 2.3 D6：rustfmt 漂移

实测 hunk（`cargo fmt --all --check` 原文，非汇总）：

| 文件 | hunk | 归因 |
|---|---|---|
| `gateway src/core/api/send.rs` | 1331、1391 | 本轮 1ca59da |
| `gateway src/core/api/webhook.rs` | 776、994、1002 | 本轮 1ca59da（**你 §3 汇总未列**，`/tmp/gw-fmt.log` 里有） |
| `advanced src/advanced/api/address.rs` | 242、265 | 本轮 b7f207b |
| `advanced src/advanced/storage.rs` | 1458 | 本轮 b7f207b（`scan_active_boards` 签名） |
| `advanced src/advanced/strategy.rs` | 1892 | 本轮 b7f207b（测试夹具 `make_board`） |
| `gateway vendor/mailin/src/parser.rs` | 113 | **非本批**，252cab0 起就存在，见 §4 |

只做换行/缩进/尾随分隔，无语义改动（`git show --stat`：gateway 31 增 13 删，advanced 57 增 30 删）。

## 3. 原始输出（你 §6.2 的命令，逐条）

```
$ cd ~/aimail/cli && cargo fmt --all --check
RESULT cli fmt: rc=0
$ cd ~/aimail/cli && cargo clippy --all-targets -- -D warnings
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.26s
RESULT cli clippy: rc=0
$ cd ~/aimail/cli && cargo test --all-targets
    24 × "test result: ok"；229 passed / 0 failed
RESULT cli test: rc=0

$ cd ~/aimail-advanced && cargo fmt --all --check
RESULT advanced fmt: rc=0
$ cd ~/aimail-advanced && cargo clippy --offline --all-targets -- -D warnings
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.41s
RESULT advanced clippy: rc=0
$ cd ~/aimail-advanced && cargo test --bin aimail-advanced
    test result: ok. 94 passed; 0 failed; 0 ignored
RESULT advanced test: rc=0

$ cd ~/aimail-gateway && cargo fmt --all --check
RESULT gateway fmt: rc=0
$ cd ~/aimail-gateway && cargo clippy --offline --all-targets -- -D warnings
    Finished `dev` profile [unoptimized + debuginfo] target(s) in 0.35s
RESULT gateway clippy: rc=0
$ cd ~/aimail-gateway && cargo test --lib
    test result: ok. 290 passed; 0 failed; 0 ignored

$ cd ~/aimail && bash tests/sdk/l0-gate-tests.sh
    [L0] PASS — all gates green（pytest 277 passed；vitest 288/37；docs 契约值 == 单一真源）
L0_POSTCOMMIT_RC=0

$ cd ~/aimail-advanced && A1_EVIDENCE=/tmp/r2-evidence/a1-postcommit bash tests/SDK/agent-level/gate.sh
    ADV_BIN: target/debug/aimail-advanced (md5=c5075b2b, fresh)
    RESULT: 29 cases · 29 pass · 0 fail        l1-py
    RESULT: 13 cases · 13 pass · 0 fail        l1-ts
    RESULT: 14 cases · 14 pass · 0 fail        l2-py
    RESULT: 14 cases · 14 pass · 0 fail        l2-ts
    GATE a1 all: PASS   (rc=0)
```

确定性（case 级，不是只比总数；两次都是完整 `gate.sh`，`gate.sh` 读 `A1_LEVEL`，默认 all）：

```
 python3 比对 a1-all/*.json vs a1-l1-run2/*.json
 l1-py cases=29 identical · l1-ts cases=13 identical · l2-py cases=14 identical · l2-ts cases=14 identical
```

## 4. 需你裁决：`cargo fmt --all --check` 的 vendor 口径（本批唯一越界项）

事实（可复现，三步自证）：

1. `aimail-gateway` 没有 `[workspace]`，`workspace_members` 只有根包
   （`cargo metadata` 输出 `path+file:///home/ubuntu/aimail-gateway#1.0.4`）；
2. 但 `mailin = { path = "vendor/mailin" }` 是 path 依赖，`cargo fmt --all` 会跟着它进 vendor：
   在 **252cab0**（上一轮“清干净”的格式化提交）的 worktree 里跑 `cargo fmt --all --check`
   就报 `vendor/mailin/src/parser.rs:113`，rc=1；
3. 加 `[workspace] members=["."] exclude=["vendor/mailin"]` 后仍是同一条报错（已实测）。

所以“你声明的那条命令”只有两种落点：

- **A（本批采用）**：把 vendor 那一行按 rustfmt 展开（`Ok((buf, Cmd::Mail {..}))` 拆 8 行，
  whitespace only）。代价：vendored 代码出现 1 处格式 diff，与 252cab0 的“vendored 不重排”口径冲突。
- **B（请你改判据）**：判据写成 `cargo fmt --check`（包范围），vendor 保持原样。
  代价：判据从“all”退到“包”，覆盖面变小。

我按 A 做了，因为 §6.2 给的是 `--all`、且“判据只增不减”；但这条属于**口径变更**，
我把它显式摆出来而不是静默过去。你要 B，我下轮把 vendor 那行退回去（`git revert` 单 hunk 即可）。

## 5. 未改门禁文件（你 §6.5）

三个提交的文件清单（`git show --stat`）：

```
aimail 21c0288        cli/src/cmd/agent.rs, docs/handoff-agent-level-scenario-1.md
aimail-advanced 06cb540  src/advanced/{api/address.rs,board_quota.rs,storage.rs,strategy.rs}
aimail-gateway 24d220f   src/core/api/{send.rs,webhook.rs}, vendor/mailin/src/parser.rs
```

`aimail-advanced/tests/SDK/agent-level/`、`aimail/scripts/check-versions.sh`、
`aimail/.github/workflows/publish.yml`：本轮 0 改动（不在任何提交的文件清单里）。

## 6. 上一轮文档偏差（你 §8 要求的三处，已改在 `docs/handoff-agent-level-scenario-1.md`）

| 位置 | 原文 | 改为 |
|---|---|---|
| §0 | `aimail` main `b377f99` | `e56b9d1`（= `b377f99` 代码提交 + `c1943bd`/`e56b9d1` 两个文档提交），并写清构成 |
| §2.2 / §3 | pytest 275、vitest 287/35 | pytest **277**、vitest **288/37**（两处都改，避免只改一处又漂） |
| §2.5 第 2 条 | 改坏 `agent-self-setup.md` 的契约值应红 | 改为：自举文档按设计带 **0 项**契约值，契约值对抗在 `agent-integration.md`；对自举文档只能做**符号**对抗（改坏被引用符号/命令名） |

第 5 条（`board_status` 用 agent key 必须 401）未改：本轮没动鉴权代码，也没复测这一条
（你的 4/4 401 结论我不冒充为自己的证据）；如要我再跑一遍，说一声。

## 7. 未做 / 不可为（不冒充通过）

- **未 push**：三仓 HEAD 只在本地。按流程 push 属于发版序列（你 §5.1 第 1 步），等你复验 +
  owner 点头后我一条命令推。若你复验需要远端 SHA，说一声我先推。
- **未跑 `tests/run-e2e.sh --release advanced`**：它是你的工装档位，且其中 `sdk-e2e`/docker 五宿主
  在发版前从 registry 装旧包，按 §4.3/§4.4 此刻判不了；第 0 步的 SDK 侧判据只要求 L0 + A1(L1/L2)，
  三项都给了原始输出。
- **docker 五宿主 / L3 12 格 / 真机冒烟**：未做，按 §4.3 属发版后判据。
- **product 行为零变更**：本轮只动 lint/format + CLI 一处等价收窄 + 文档；无新功能、无接口变化，
  因此**不需要**重新论证 SDK/服务端兼容性。

## 8. 证据索引（本机，/tmp/r2-evidence/）

```
cli-fmt.log cli-clippy.log cli-test-full.log        CLI 三件（§6.2 第 1 条）
adv-fmt.log adv-clippy.log adv-test-full.log        advanced 三件（第 2 条）
gw-fmt.log  gw-clippy.log  gw-test.log              gateway 三件（第 3 条）
l0-gate.log l0-postcommit.log                       SDK L0（第 4 条，提交前后各一次）
a1-all/ a1-all.log                                  首次 A1 all（29/13/14/14, PASS）
a1-l1-run2/ a1-l1-run2.log                          第二次 A1 all（case 级一致）
a1-postcommit/ a1-postcommit.log                    提交后 A1 all（PASS，二进制 c5075b2b）
```

## 9. 复验建议顺序

```bash
# 1) 三仓 lint/format 复跑（应全 rc=0）
cd ~/aimail/cli && cargo fmt --all --check && cargo clippy --all-targets -- -D warnings && cargo test --all-targets
cd ~/aimail-advanced && cargo clippy --offline --all-targets -- -D warnings && cargo fmt --all --check
cd ~/aimail-gateway && cargo fmt --all --check && cargo clippy --offline --all-targets -- -D warnings
# 2) SDK L0
cd ~/aimail && bash tests/sdk/l0-gate-tests.sh
# 3) A1（先确认 ADV_BIN 新鲜：cargo build --bin aimail-advanced）
cd ~/aimail-advanced && A1_EVIDENCE=/tmp/<your-dir> bash tests/SDK/agent-level/gate.sh
# 4) 想验确定性就再跑一次第 3 步，比 case 级而不是只比总数
```

反馈路径：你把 D5/D6 判绿 + 对 §4 的 A/B 口径给一句裁决 → owner 发版授权 → 我 push 并按
§5.1 第 2、3 步 dispatch（dry_run 先验）。
