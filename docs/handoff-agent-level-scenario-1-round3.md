# 发版提交（第 3 轮）：场景 1 —— 缺陷已内部清关，待发版验收

> 出具方：实现方。接收方：验证方（发版验收）。
> 上一轮：`docs/handoff-agent-level-scenario-1-round2.md`（HOLD，D5/D6）。
> **本次提交不是"请复验缺陷"，而是"请验收发版"**：D5/D6 已在开发侧内部闭环，
> 判据/用例/夹具仍归你所有，本文不改任何判据。

## 0. 一页结论

- **D5 / D6 已在开发侧清干净**：CLI `clippy -D warnings` rc=101 → rc=0；advanced clippy 4 红 → 0；
  两仓 rustfmt 漂移 → 0。做法是改代码，不是加 `#[allow]`。
- **发版前判据全绿且可复现**：三仓 fmt/clippy/test、SDK L0、A1 `all` 29/13/14/14（连跑两次 case 级一致）、
  `check-versions.sh` 元数据 PASS + 批次内依赖序 PASS(+7 pending)、
  以及**发版门禁全档本地预跑**：`tests/run-e2e.sh --release advanced` → `RELEASE GATE advanced: PASS`，
  18/18 套件（含档内再跑一次 A1，fail 全 0；e2e-open-apply 91 PASS/0 FAIL；warning-gate 0 warning）。
- **唯一口径争议已内部裁定**：`cargo fmt --all --check` 会把 path 依赖 `vendor/mailin` 拉进判据
  （252cab0 起就不符合，`[workspace] exclude` 实测无效）。我按"判据只增不减"保留你的 `--all` 命令，
  因此把 vendor 那一行按 rustfmt 展开（whitespace only）。理由与备选见 §2.3，不需要你再裁决。
- **版本/顺序/回滚已就位**（§4/§5）：CLI 0.1.56 独立线先落地；npm `core→mail→三适配`；
  pysdk 0.1.56 与 npm 并行；gateway 1.0.4 + advanced 1.0.2 同批 tag，部署另行授权。
- **等你的是两件事**：①发版验收（跑你自己那套门禁/矩阵，判"可否发"）；②owner 授权 push + dispatch。
  我没有 push、没有打 tag、没有 dispatch。

## 1. 被测物

| 仓 | 上一轮 | 本轮 HEAD（代码） | 提交内容 |
|---|---|---|---|
| `aimail` | e56b9d1 | **21c0288** | `cli/src/cmd/agent.rs`（D5）+ 上一轮交接的三处文档修正 |
| `aimail-advanced` | d581d53 | **06cb540** | `board_quota.rs`/`storage.rs` 两处 lint + `address.rs`/`storage.rs`/`strategy.rs` rustfmt |
| `aimail-gateway` | fe2f088 | **24d220f** | `send.rs`/`webhook.rs` rustfmt + `vendor/mailin/src/parser.rs` 一行（§2.3） |

> 本文档自身是 `21c0288` 之后、`b517a18` 之后的 docs 提交（当前 HEAD）。
> 引用被测代码请用上表 SHA 或 `git rev-parse`，不要把 HEAD 当代码 SHA——上一轮就是在这里错位成 `b377f99`。

二进制（源码重建，A1 的 freshness 检查 = 0 个源码新于二进制）：

```
aimail           0.1.56 (dev)   md5 15c248cb
aimail-advanced  1.0.2          md5 c5075b2b
aimail-gateway   1.0.4          md5 b591d7ec
```

三仓 `git status --short` 为空。**未 push**（ahead origin：aimail 7 / advanced 4 / gateway 2）。

## 2. 内部清关的缺陷

### 2.1 D5（Blocker）：`collapsible_if` @ `cli/src/cmd/agent.rs:734`

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

语义等价：`aimailsdk` + 有解释器 + 探针输出非空 ⇒ 返回版本；否则落到目录 / `npm root -g` 探针，
空串仍视为未命中（原靠内层 `if !v.is_empty()`，现靠 `Option::filter`）。无 `#[allow]`。

### 2.2 D6：advanced clippy 4 项

| 位置 | 判据 | 修法 |
|---|---|---|
| `board_quota.rs:36-38`（3 项） | `doc_lazy_continuation` | bullet 列表与新段落之间补空 `///` 行（`Counting globally …` 是新段落，不是列表续行） |
| `storage.rs:125`（1 项） | `type_complexity` | 抽 `type BoardCountCache = Arc<RwLock<Option<(Instant, Option<String>, usize)>>>`，与既有 `BlacklistCache` 同款；内联元组注释移进别名文档 |

### 2.3 rustfmt + vendor 口径（内部裁定，备查）

实测 hunk：`gateway send.rs` 1331/1391、`gateway webhook.rs` 776/994/1002（后三处你上一轮 §3 汇总未列，
`/tmp/gw-fmt.log` 里有）、`advanced address.rs` 242/265、`advanced storage.rs` 1458、`advanced strategy.rs` 1892。
只做换行/缩进。

**vendor 口径**：`cargo fmt --all --check` 会跟进 `mailin = { path = "vendor/mailin" }`；
在 252cab0 的干净 worktree 里就报 `vendor/mailin/src/parser.rs:113`，加
`[workspace] members=["."] exclude=["vendor/mailin"]` 实测无效（`cargo metadata` 的
`workspace_members` 只有根包）。

裁定：**保留 `--all` 判据，格式化 vendor 那一行**（该 hunk 7 增 1 删，纯 whitespace）。
原因是判据只增不减、且发版门禁用的就是这条命令；备选（把判据退成 `cargo fmt --check`）会让覆盖面变小，
不作为本轮选择。若你验收时认为 vendored 一行也不能动，请按"判据修订"提，我回退该 hunk 并同步改判据——
但那是下一轮的事，不阻塞本次发版验收。

## 3. 发版前判据（原始输出，本机 2026-10-19）

```
$ cd ~/aimail/cli && cargo fmt --all --check                        rc=0
$ cd ~/aimail/cli && cargo clippy --all-targets -- -D warnings      rc=0   （上一轮 rc=101）
$ cd ~/aimail/cli && cargo test --all-targets                       rc=0   24 套件 / 229 passed

$ cd ~/aimail-advanced && cargo fmt --all --check                   rc=0
$ cd ~/aimail-advanced && cargo clippy --offline --all-targets -- -D warnings   rc=0  （上一轮 4 红）
$ cd ~/aimail-advanced && cargo test --bin aimail-advanced          rc=0   94 passed

$ cd ~/aimail-gateway && cargo fmt --all --check                    rc=0   （上一轮 6 hunk 漂移）
$ cd ~/aimail-gateway && cargo clippy --offline --all-targets -- -D warnings    rc=0
$ cd ~/aimail-gateway && cargo test --lib                           rc=0   290 passed

$ cd ~/aimail && bash tests/sdk/l0-gate-tests.sh
    [L0] PASS — all gates green   （pytest 277；vitest 288/37；docs 契约值 == 单一真源）

$ cd ~/aimail-advanced && A1_EVIDENCE=/tmp/r2-evidence/a1-postcommit bash tests/SDK/agent-level/gate.sh
    ADV_BIN: target/debug/aimail-advanced (md5=c5075b2b, fresh)
    RESULT: 29 cases · 29 pass · 0 fail    l1-py
    RESULT: 13 cases · 13 pass · 0 fail    l1-ts
    RESULT: 14 cases · 14 pass · 0 fail    l2-py
    RESULT: 14 cases · 14 pass · 0 fail    l2-ts
    GATE a1 all: PASS   (rc=0)

确定性：两次完整 `gate.sh` 的 4 份 JSON 逐 case 的 id/status 序列 identical
（不是只比 29/13/14/14 的总数）。

$ cd ~/aimail && bash scripts/check-versions.sh                     rc=1（不带批次：依赖未上架，发版前应如此）
$ RELEASE_BATCH=mail-core,mail,dsh-aimail,openclaw-aimail,pi-aimail bash scripts/check-versions.sh
    [L1] PASS (versions aligned, release type consistent)
    pending(batch[…]) ×7 → [L1] PASS                                rc=0

$ cd ~/aimail-advanced && bash tests/run-e2e.sh --release advanced
    RELEASE GATE advanced: PASS            rc=0，18/18 套件 PASS
    SUMMARY: /tmp/e2e-logs/advanced-20261019-221354/SUMMARY.txt
    含 SDK/agent-level/gate.sh（该档内复跑 A1：l1-py 29 / l1-ts 13 / l2-py 14 / l2-ts 14，fail 全 0，
      证据 tests/SDK/agent-level/reports/20261019-224334/）
    含 advanced/warning-gate.sh PASS（0 warning）、e2e-open-apply 91 PASS / 0 FAIL
```

## 4. 发版顺序（与你的依赖图一致，不改判据）

```
CLI 0.1.56 ─────────────────────────────┐  agent-install.sh 拉的是 release 资产 ⇒ 必须先落地
                                        ├→ L3 12 格（needs: cli-release + npm + pypi）
pysdk 0.1.56 (PyPI) ────────────────────┤  独立线，可与 npm 并行
mail-core 0.1.55 → mail 0.1.55 → dsh 0.1.59 / openclaw 0.1.58 / pi 0.1.57   （npm 硬依赖链）
gateway 1.0.4 ──path──→ advanced 1.0.2   同批 tag；advanced 部署另行授权
```

不可逆点只有一处：`publish.yml` 的 `dry_run=false`。dry_run=true 只验形状（4 平台构建、npm pack、PyPI build）。

## 5. 回滚（逐项可执行）

- CLI：重新 dispatch `cli-v0.1.55`；`agent-install.sh --version cli-v0.1.55` 可钉住。
- npm：逐包回退；适配包必须与 `mail-core` 同批回退（bundle 内 SKILL.md 与核心包语义要一致）。
- PyPI：回退 `aimailsdk==0.1.55`（旧版仍在）。
- gateway/advanced：tag 保留、Release 可下架；本批无 DB schema 变更，回退后旧 SDK 仍可用
  （只是没有 manager/快照等新行为）。部署回滚：
  `mv -f /usr/local/bin/aimail-advanced.prev-<oldver> /usr/local/bin/aimail-advanced && systemctl restart aimail-advanced`
  （部署前必须确认备份文件存在）。

## 6. 发版后硬证据（发版验收的口径，不是"job 绿"）

1. `gh release view cli-v0.1.56 --json assets`：4 个平台资产，逐个 sha256 + 运行版本；
2. `curl -s https://pypi.org/pypi/aimailsdk/0.1.56/json`：版本 + provenance（workflow=publish.yml, environment=pypi-release）；
3. `npm view @aimail/mail-core@0.1.55 version` + `npm pack --dry-run` 里 `resources/skills/SKILL.md` 在位；
4. 干净环境：`pip install aimailsdk==0.1.56` → `agent_toolset()` 15 个；`npm i @aimail/mail-core@0.1.55` → `agentTools()` 15 个；
5. 安装器：`agent-install.sh` → `aimail version` = 0.1.56 → `aimail agent doctor --questions` 正常；
6. 依赖序：此时**不带** `RELEASE_BATCH` 跑 `check-versions.sh` 必须转绿（证明 §2 的 pending 已落地）；
7. L3 12 格（workflow 内）+ docker 五宿主（镜像重建后）必须有结论；任一红 ⇒ 本批只能前滚补丁版本。

## 7. 不在本批 / 未验证（不冒充）

- L3 12 格、docker 五宿主、pi/dsh/openclaw/deerflow 真机冒烟：发版后判据，本机此刻不具备条件。
  docker 侧你已修掉 `agent-paths.sh` 漏拷贝的根因（hermes 镜像 PASS），其余四平台待重建。
- `SDK/sdk-e2e`（需 `RELAY_BIN`/`BRIDGE_BIN`/`TEST_MANAGER`）：环境不完整，未计入本轮。
- category-21 J.5 仍是条件性 WARN（`no code found in delivered email`），非本批引入，本批不处理。
- 本轮**零产品行为变更**（lint/format + 一处等价收窄 + 文档），因此不需要重新论证 SDK/服务端兼容性。

## 8. 需要 owner 的两项授权

1. push 三仓 main（aimail / aimail-advanced / aimail-gateway）；
2. dispatch `publish.yml`（先 dry_run=true，再 dry_run=false）。
gateway/advanced 的**部署**是第三项、单独授权，不在本次请求内。

## 9. 证据索引（本机 /tmp/r2-evidence/）

```
cli-fmt.log cli-clippy.log cli-test-full.log         CLI 三件
adv-fmt.log adv-clippy.log adv-test-full.log         advanced 三件
gw-fmt.log  gw-clippy.log  gw-test.log               gateway 三件
l0-final.log l0-docsv2.log                            SDK L0（文档改动后再跑一次）
a1-all/ a1-all.log                                   首次 A1 all
a1-l1-run2/ a1-l1-run2.log                           第二次 A1 all（case 级一致）
a1-postcommit/ a1-postcommit.log                     提交后 A1 all（二进制 c5075b2b）
release-gate-advanced.log                            tests/run-e2e.sh --release advanced
```

## 10. 本次提交要你产出的结论

**一句话**：这批能不能发。

具体三项：①发版验收判绿（你自己的门禁/矩阵，不必逐条回我 D5/D6）；②发版后硬证据（§6）由谁在什么时候跑；
③是否有阻塞发版的遗留（有就指名，没有就一句"可发"）。红的话我按归因（现象/复现/期望 vs 实际）修，
不重开范围、不动判据。
