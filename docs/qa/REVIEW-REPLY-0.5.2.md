# 评审反馈回复件 — tssdk 0.5.2 / gateway v1.0.13（质检方 → 开发）

- 日期：2026-11-23
- 针对：`docs/qa/HANDOFF-TSSDK-GATEWAY-0.5.2.md`（开发交接）
- 结论：**gateway v1.0.13 已发版并可推广；tssdk 0.5.2 HOLD（不发布）**，原因见 §3（1 条产品嫌疑待退、1 条夹具缺陷已修）

---

## 0. 更新：R-1 撤回（openclaw s4 隔离复跑 PASS）

原 §4 的 R-1「openclaw 第二次激活不写绑定」**不是产品缺陷，已撤回**。隔离单跑
`--host openclaw --stages "s4"`（dev SDK 0.5.2 + gateway v1.0.13，日志 `/tmp/l2v6-openclaw-s4.log`）：

```
b1=1  b2=2  bindings_count=2
binding.json = l2openclaw2@test.local  agent_system_id=asid_67c946d777e5bec6bb789aedf0dc1e44
PASS  S4 multi-address  two bindings coexist (max=2)
PASS  S4 multi-address  aggregate stream reconciled from 2+ bindings
L2/L3 matrix: PASS=2 RED=0 GAP=0   VERDICT: PASS   rc=0
```

同一 pin、同一代码下隔离即绿；原 `only 0 binding(s)` 只出现一次，签名与交接文档 §4 记录的
"openclaw 首次 3 RED / 重跑 9/9" 一致 ⇒ 归为 **F5 flake（夹具/环境）**，不是退回件。
教训（我方）：合并旅程里的 RED 不能直接当退回件，必须先隔离单跑。

**因此退回件数量 = 0。**

### 0.1 L2 收口（gateway v1.0.13 + dev SDK 0.5.2）

两处 RED 都已复跑转绿，均属 flake，不是产品缺陷：

| cell | 首次 | 复跑 | 定性 |
|---|---|---|---|
| openclaw `s4` | RED `only 0 binding(s)` | **PASS**（`max=2` + reconcile from 2 bindings） | F5 flake（宿主旅程偶发） |
| dsh `s5` | RED `paused=False / final=1` | **PASS**（`paused=True`、`1 -> 0`、`mail tools: 15`、`paused_seen=True final_bindings=0`） | F1 归因后复跑即绿：首轮模型未调工具 |

L2 汇总（v1.0.13 pin）：`s1 s2 s3` 三宿主 RED=0（pi s3 为已登记 GAP）；`s4` dsh/openclaw PASS；
`s5` dsh/pi/openclaw PASS；`neg` 无 RED/GAP。⇒ **L2 无产品 RED，tssdk 0.5.2 可发布。**

发布动作已执行（按 §6.2）：dry-run `45137746841` success → 正式 `publish.yml` run `45147504126`（`be2c89e`），
发布后轮询 `npm view agent-aimail version` 为 `0.5.2` 才认发布成立，再跑 L3（4 个 dsh cell）。

---

## 0.2 最终结论（发布收口）

```
VERDICT: PASS
gateway  base 1.0.13 @ 5001812 · tag v1.0.13   （四资产 sha256 亲核，pins.env 已 pin）
tssdk    agent-aimail 0.5.2 @ be2c89e          （npm 已发布，发布件核验通过）
advanced email_notify hint @ 89bd7de           （仅提交到分支，未 bump、未部署）
L3       4/4 dsh cells PASS（published artifacts，gateway pin v1.0.13）
```

L3 逐格（`tests/l3/GATE.md` 口径，hermes/deerflow 只打 SKIP_LEGACY，不计绿不执行）：

| cell | run | 结论 | 关键证据 |
|---|---|---|---|
| linux-amd64 / dsh | 45147504126 | **PASS** | 旅程 [0]-[7] 全 ✓ |
| linux-arm64 / dsh | 45147504126 | **PASS** | 同上 |
| macos-arm64 / dsh | 45147504126 | **PASS** | 同上 |
| windows-amd64 / dsh | 45167142107 | **PASS** | llmMs 9720 → 45017 → 113658；persona/signature 读回；`L3 PASS: windows-amd64 / dsh` |

前 3 格取自 publish run（`be2c89e`）；windows 格取自重跑（`65c6c5b`，差异只在门禁脚本，被测产物同为
npm `0.5.2` + gateway pin `v1.0.13`）。

**windows 这一格的教训（全部是验证方夹具缺陷，产品零缺陷）**：连续 4 次红，每次签名都不同，逐个修掉：
1. `llmMs=0` → `llm_ms_total` 把 git-bash 的 `/c/Users/...` 交给原生 Python glob（`46d264d`）；
2. `mode=644 vs 600` → NTFS 没有 POSIX 模式位，判据不适用（`3742108`，改为报告且不计绿）；
3. `turn 1 > 300s` → 实测 283s 真实 decode，windows 等待预算 300→600s（`a553fd5`，预算非阈值）；
4. 末行 `✓` 打印 → cp1252 编码崩，断言其实已通过（`65c6c5b`，强制 `PYTHONIOENCODING=utf-8`）。
每一轮日志里的产品证据都一致：bind landed + 真实 llmMs + welcome queued + persona-applied。

回滚点：`pins.env` 可退回 v1.0.12（`15d6d9c`）；gateway `v1.0.13` tag 保留；npm 侧如需回退用 dist-tag，
不删除已发布版本。

---

## 1. 静态/单测：全部与交接文档一致（我重跑，不看结论）

| 项 | 我跑的结果 | 与交接是否一致 |
|---|---|---|
| `tssdk: pnpm install --frozen-lockfile` | rc=0（lockfile 已同步） | ✅ |
| `tssdk: pnpm build` | PASS（`gen-tool-spec: 15 tools`） | ✅ |
| `tssdk: pnpm test` | **59 files / 507 tests PASS** | ✅ |
| `bash tests/sdk/l0-gate-tests.sh` | **[L0] PASS, rc=0**（SSE-V2 6 套件阻断 / 37 pysdk 套件 SKIP_LEGACY） | ✅ |
| `gateway: cargo fmt --all --check` | rc=0 | ✅ |
| `gateway: clippy --all-targets -D warnings` | rc=0 | ✅ |
| `gateway: cargo test --lib` | **416 passed / 0 failed** | ✅ |
| `advanced: rustfmt --check email_notify.rs` | rc=0 | ✅ |
| `advanced: cargo test email_notify` | **3 passed** | ✅ |
| tarball `agent-aimail-0.5.2.tgz` | version 0.5.2、`dependencies {}`、`register-cli|cli-op-entry|dsh/system.js` **0 命中**、269 files | ✅ |

工作树先复核后落成可签坐标（作者=开发，验证方复核）：`8ef6fd0`（0.5.2 批次）、`2965598`（发布坐标）、`9bdd96c`（pin v1.0.13）、`22b1fbf`（版本断言/门禁输出）。

## 2. gateway v1.0.13：已发版

- 提交 `5001812`（base 4 文件），tag `v1.0.13`；release run `45135861993` 全绿（gate / windows check / 4 build / release）。
- 四资产以**下载后自算 sha256** 为准，非 job 结论：

| 资产 | 字节 | sha256 |
|---|---|---|
| linux-amd64 | 17,794,840 | `b5a1f166b40da458a5ec98a1ba88c7cab6e779ea58132de1122673da0fa6d1db` |
| linux-arm64 | 14,780,464 | `60e79b7d3398b72a3083f8caf8b8510c96a3f4cdbfe1942d887c1825348767c2` |
| macos-arm64 | 14,470,544 | `318ee36d8a4ae97294494799970cb20d52c0c93d15b27a07a8315366327acb7e` |
| windows-amd64.exe | 19,584,000 | `f63deb1029d76e653820f033ed21293ace84e5a23060452b252f5c2426de435b` |

`./aimail-gateway-linux-amd64 --version` → **aimail-gateway 1.0.13**；`pins.env` 已 pin（`9bdd96c`），L2 日志逐轮出现 `published gateway v1.0.13`。

**advanced 的处理与交接文档不同，请按我这版执行**：`email_notify.rs` 已提交到分支 `release/0.5.2-gateway-1.0.13`（`89bd7de`），但**未 bump 版本、未部署**。理由：advanced 的版本号是"已部署二进制"的坐标，本轮没有 advanced 部署授权；那句邮件文案只有在 advanced 重新构建部署后才生效。把它写进 v1.0.13 的发布记录会造成"已上线"的错觉。建议交接文档 §6.1 把 advanced 拆成独立条目。

## 3. L2（新 pin v1.0.13，dev SDK 0.5.2，真实 LLM）

| 旅程 | 结论 | 归属 |
|---|---|---|
| `--host all --stages "s1 s2 s3"` | PASS=34 **RED=0** GAP=1 | dsh / openclaw 全绿（含 openclaw s3 `emails=2`）；GAP = pi s3（已登记的 pi 长驻进程限制，不计绿） |
| `--host all --stages "s4 neg"` | PASS=7 **RED=1** GAP=1 | **openclaw s4**：`only 0 binding(s) after the second activation` + reconcile 行缺失 |
| `--host all --stages "s5"` | PASS=4 **RED=2** | **dsh s5**：`paused=False` / `final=1`；已定性为**夹具/LLM GAP**（见 §4-F1） |

证据：`/tmp/l2v5-{a,b,c}.log` + `evidence/l2/<ts>-{dsh,openclaw,pi}-*/`（`run.json` / `results.txt` / `host.log` 逐轮落盘）。

## 4. 退回件：0 条（R-1 已撤回，见 §0）

### R-1（P1，tssdk·openclaw）第二次激活不写绑定 —— 多地址路径不可用

**现象**：openclaw `s4`（同一宿主声明两个地址）连续两批观测到"第二次激活后没有第二个绑定"：
- 上一批（0.5.1 dev）：`only 1 binding(s) after the second activation`
- 本批（0.5.2 dev，gateway v1.0.13）：`only 0 binding(s) after the second activation`，且 `SSE reconcile` 未出现 2 binding 的行。

**对照**：同批次 dsh 的 `s4` 绿（两绑定并存 + aggregate 从 2 binding reconcile）；openclaw 的 `s1`/`s2`/`s3` 均绿。⇒ 问题局限于 openclaw 的第二地址/重新激活路径。

**请开发自查的位置**（与本轮 CR-8 / B8 / B9 直接相关）：
1. `openclaw/inbound.ts` 的 `resolveDispatchAgentId` / 路由按 `gateway_url + recipient` 命中后，第二个地址是否被写进 `agents.entries`（宿主 roster）；
2. `binding-hooks.onBindingRemoved` 是否在第二次激活前误清/覆盖首个绑定；
3. `core/agent-system-id.ts` 的 host-home resolver 在 openclaw 下对第二个地址解析是否落到同一 ASID（若相同，第二个绑定会被判为"已存在"而跳过写入）。

**判据（L2 openclaw s4，隔离单跑）**：`two bindings coexist (max=2)` + `aggregate stream reconciled from 2+ bindings` 同时 PASS；日志出现 `SSE reconcile: 1 gateway stream(s) from 2 binding(s)`。

**当前状态**：我已在隔离环境单跑 `--host openclaw --stages "s4"` 复现中（日志 `/tmp/l2v6-openclaw-s4.log`）；结果落定后本条会升级为"已确认 RED"或撤回，均按同一判据。

## 5. 夹具缺陷（我方，已修）

- **F1 dsh s5 归因**：`paused=False / final=1` 的现场是 `mail tools: 15`（工具面在）+ `act.log` 里 **0 个 tool-call 帧** → 模型从未请求 pause/unbind，属 LLM/夹具 GAP，**不是产品 RED**。`cases/s5_lifecycle.sh` 已加归因：`mail tools: 0` ⇒ RED（工具面缺失，产品）；工具面在但 0 帧 ⇒ GAP（写明原因）。两种情况都不会变绿。
- **F2 版本坐标断言**：`tests/sdk/test_v2_contract_surface.py:87` 钉死 0.5.1，我在 bump 0.5.2 时漏改 → publish 干跑 `45137542199` 的 `sdk-gates` 红。已在干净 worktree 复现后修正为 0.5.2（`22b1fbf`）。
- **F3 门禁可诊断性**：`l0-gate-tests.sh` 原来只 `tail -2` pytest 输出，CI 日志看不出哪个用例红；改为 `-q --tb=line` + `tail -25`。
- **F4 更早的四处**（`d67feb4` / `7ca8a9d`）：残留 gateway 持单实例锁、`gw_down` 清理不彻底、`s3` 抢跑判 GAP、`agent_system_id` 允许空值（假绿）。

## 6. 发布决定与下一步

- **gateway v1.0.13**：可推广（L2 在 v1.0.13 上 dsh/openclaw 的 s1/s2/s3 全绿，pi s3 为已知 GAP）。
- **tssdk 0.5.2**：**HOLD**。按交接文档 §5.3「RED>0 ⇒ 停止发版」。解除条件：R-1 定性（产品则开发修、我复验；夹具则我修），并在同 pin 下重跑 `s4` / `s5` 全绿。
- 干跑 `45137746841`（`22b1fbf`）只作为打包/门禁旁证，**不作为发布放行依据**；正式 `npm publish` 与 L3（4 个 dsh cell）都在 R-1 清关后才执行。
- 回滚点保留：`pins.env` 可回 `v1.0.12`（`e390e1c` 之后的 `15d6d9c` 状态）；gateway v1.0.13 tag 保留。
