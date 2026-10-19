# 交接：场景 1（agent 级对话安装）产品改造 — 复审与发版

> 交接对象：具有**最终审验与发版权限**的专属 agent。
> 本文只写产品侧事实（改了什么、怎么验、证据在哪、怎么发），门禁判据仍以
> `aimail-advanced/tests/SDK/agent-level/HANDOFF.md` 为准。
> 门禁目录（cases/断言/夹具/入口）由验证方所有：本批**一行未改**，也未提交。
> 反馈路径：复验结论 / 发版结果 / 异议，请直接回给 owner（本交接由实现方出具）。

## 0. 一页结论

- 场景 1 = 用户在对话里把激活行交给一个干净 agent：它拿到 shell → 装控制面 CLI →
  doctor 自证宿主 → setup 装适配包/skill → **在 agent 内部**激活 → 校准身份 → 自主收发。
  **数据面不经过 CLI；不用 bridge、系统级凭据、admin key；不监听任何端口（纯 pull）。**
- 闭环已实测（同一进程）：0 绑定起运行时 → 进程内激活 → 句柄 0→1（**hot-add，无需第二次
  重启**）→ 来信成为回合 → 回信 → 对端收到（`/tmp/hotadd-driver.py` 6/6）。
- 门禁 `GATE a1 all: PASS`（29/13/14/14，`fail=0`，证据 `/tmp/a1-final7`）；L0 PASS；
  pytest 277；vitest 288；CLI 集成测试 4 + 10；适配选型矩阵 11/11。
- 待发版：**CLI 0.1.55→0.1.56（必须先发）**、gateway 1.0.3→1.0.4、advanced 1.0.1→1.0.2、
  `@aimail/mail-core` 0.1.54→0.1.55、pysdk 0.1.55→0.1.56，外加三适配包同批升。
- 代码已 push（`aimail` main `e56b9d1`（= `b377f99` 代码提交 + `c1943bd`/`e56b9d1` 两个文档提交）、
  gateway `1ca59da`、advanced `b7f207b`）；
  **未 bump、未打 tag、未 publish**。

## 1. 产品侧新增与变更

### 1.1 Rust 服务端

| 域 | 位置 | 变更 | 配对 / 影响面 |
|---|---|---|---|
| advanced | `src/advanced/api/address.rs:182` `act_err_to_app` | `ActErr` 保留语义映射到 `AppError`（Invalid/NoBareDomain/DomainConflict→400、BoundToOther→403、AlreadyActive→409、Db→500）；删除旧死代码与 `act_err_msg` | 仅激活路径；错误文案不变 |
| advanced | `src/advanced/api/address.rs:437-496` | 激活事务 6c 写 `domain_addr_meta` 后新增 6d：按**回读的 effective manager** 插 `direction='all'` 的 agent↔manager 白名单（与 register 同款，`category='system'`，`ON CONFLICT` 保 `is_active=1`）；`ActivationOutcome` 增加 `manager_address`（:154）并随响应返回（:264） | 续期沿用已存 manager，不被本轮申请人覆盖；白名单插入不走 API 配额 |
| advanced | `src/advanced/storage.rs` `get_active_board_count` / `scan_active_boards`、`src/advanced/board_quota.rs:53` | 板配额**按系统计数**：产品配额是每系统的 ⇒ 只数该系统自己的板；网关 `[board]` 默认与 standalone/license 授权是整机口径 ⇒ 数全部板。计数缓存带作用域（每系统答案不会被当成全局答案或别的系统答案）。原先用**全局** actively 数对比**每系统**上限，一个系统的板会吃掉另一个系统的额度 | 仅准入判断；地址码产品 `max_active_boards=Some(0)` 不变（owner 裁定：只能当成员） |
| gateway 1ca59da | `src/core/api/webhook.rs` `notify_agent_persona_applied` + `outbound.rs` `AckCtx: Copy` | 角色批准后**同时**给 agent 本人发一封 `[AIMail] Persona applied` 闭环通知（复述已落库的 persona/signature），安全官仍收回执。内部收件人不能走 `enqueue_outbound_simple`（pull 地址无 webhook ⇒ 退 MX ⇒ loopback 丢弃），改为按 board 通知同款 `create_inbound`，自建 `message_id` 头 | 仅 persona 批准路径；通知自身不会被当指令消费（发件人是系统 noreply） |
| gateway | `src/core/api/send.rs:1327` `normalize_thread_headers` | 合并 headers 后、board 注入与 Auto-gen 之前，把 `Message-ID`/`In-Reply-To`/`References` 按名归一为 `message_id`/`in_reply_to`/`references`（大小写与 `-`/`_` 不敏感，规范键优先），其它 header（含 `X-Board-*`）不动；Auto-gen 只看规范化后的 `message_id` | **D3 单点修在网关**，SDK 保持发 RFC 大写键不动；gateway+advanced 同批发 |

### 1.2 SDK（Python pysdk / TypeScript @aimail/mail-core）

| 位置 | 变更 |
|---|---|
| `pysdk/aimail_tools.py:529`、`tssdk/.../address-code.ts` | 激活响应里的 `manager_address` 落进 binding（空则不写键）；这是 manager 的**第一来源**（网关在激活事务内回读后返回），不解析 prompt、不事后 whoami 反查 |
| `pysdk/aimail_tools.py:845`、tssdk `address-code.ts:150` | 连接文件只写 `gateway_url/system_id/domain/scope=agent`，**不再写 `admin_key`**（D4）；激活成功后自动 `set_agent_context`（G2），显式再调幂等 |
| `pysdk/aimail_agent.py`（新）、`tssdk/.../agent-level.ts`（新） | agent 级运行时：`agent_toolset()`/`agentTools()`（15 个工具，含 6 个 board 工具，每条自带 handler）、`install_agent_skill()`/`installAgentSkill()`（幂等装到 `<skills>/<skill name>/SKILL.md`）、`start_agent_level()`/`startAgentLevel()`（装 skill + toolset + pull 循环；默认链 = 共享入站管线，把 enriched 交给 `on_agent`/`onAgent`；ping/pong 不入回合；单绑定注入上下文） |
| `tssdk/.../meta.ts:218` `saveInboundSnapshot` | TS 补齐 Python 的入站快照落盘（meta 常写 + `in-*.json` 受 `save_raw_snapshots` 控制 + FTS 行）。原因：TS 链原先不写入站快照，`search_mail` 只能搜到出站 |
| `pysdk/aimail_base.py` `_extract_board_gateway` | board 邀请解析：**`Board ID:` 优先**；回退用**发件人地址的邮件域**算 `sha256(board address)[:20]`（不再用网关 host）；pull 报文只有 `sender` 时也认 |
| `tssdk/.../board.ts:55` `boardInviteFrom` + `preprocess.ts` | 同上，单一解析器；`processInboundMail` 用它注册/落盘 token |
| `pysdk/aimail_board.py` `_board_id_of` | board 工具接受**裸 board id**（邀请邮件里的就是裸 id）、`b_<id>`、task id；原先把裸 id 走 task-id 解析器得到空串 ⇒ 跳过 token ⇒ 用 agent key ⇒ 401 |
| `tssdk/.../package.json` + `scripts/stage-resources.mjs` | 通用核也随包发 `resources/**`（prepack 暂存、gitignore），非适配 TS 宿主因此能拿到 skill 源 |
| `pysdk/aimail_agent.py:main` + `pyproject.toml [project.scripts]` | 引导命令行 `aimail-agent setup --skills-dir DIR`：只做"装 skill + 报 toolset/注册方式（MCP 或进程内）"，**不代跑激活**。装 SDK 用语言的包管理器（py: `pip install aimailsdk`；ts: `npm install @aimail/mail-core`），注册并重载后 agent 才看到 `activate_address_code`，激活回到 agent 内部调工具 | 无状态语义变更；TS 无 CLI（toolset = `agentTools()`，skill = 包内文件） |

### 1.3 文档

`docs/agent-self-setup.md` / `_zh.md` 重写为 **agent 面三步**（4 个标题，en/zh 逐条同构）：

- 只按语言分（py→`pysdk`、ts→`@aimail/mail-core`），**不按 agent 产品分**；
- §1 你的工具：以 **toolset/工具名**表述，不再写 Python/TS 函数调用；
- §2 激活：只调 `activate_address_code`（address / code / gateway_url 三值），manager 由网关记录；
- §3 校准身份：agent 主动执行 role_calibrator（persona / signature / current_time → 安全官 approve）——**完成即激活完成**，文档到此结束；
- §1 宿主接线按**语言**分（py: pip + `aimail-agent setup` + MCP/进程内注册 + `start_agent_level`；ts: npm + `agentTools()` + 手拷 skill + `startAgentLevel`），装完重载后必须看到 `activate_address_code`；**激活与收信都在 agent 内部**，shell 只做安装与注册，两者都没有则明说"agent 内部无法完成"；
- §3 写明闭环：批准后安全官收回执、**agent 收到 `[AIMail] Persona applied` 通知**（复述 persona/signature）；
- 契约值（绑定文件/指针/前缀/入站路径/端口）**全部移出本文**，单一归属平台文档（checker 相应按层重钉：自举文档 0 项，平台文档 9 项）。

## 2. 产物的验证方法（可复现）

### 2.1 门禁（发版口径）

```bash
cd ~/aimail-advanced && cargo build --bin aimail-advanced   # 源码新于二进制 ⇒ rc=2
cd ~/aimail/tssdk && pnpm build                              # TS 改 src 后必须先建 lib/
cd ~/aimail-advanced && A1_EVIDENCE=/tmp/<dir> bash tests/SDK/agent-level/gate.sh
# 期望：RESULT: PASS；GATE a1 all: PASS（rc=0）；四份 JSON fail=0、GAP=0
```

### 2.2 SDK 全量门禁

```bash
cd ~/aimail && bash tests/sdk/l0-gate-tests.sh        # 契约单真源 + frontmatter 5/5 + literal 棘轮 + tsc + pytest + vitest
cd ~/aimail && python3 -m pytest tests/sdk -q         # 期望 277 passed
cd ~/aimail/tssdk && pnpm build && pnpm test          # 期望 288 passed / 37 files
```

### 2.3 干净环境探针（真实宿主路径，无 CLI）

```bash
/tmp/clean-env-verify/driver_py.py    # fresh venv + 空 HOME + 仅 prompt 文本
/tmp/clean-env-verify/driver_ts.py    # fresh npm 工程只装 mail-core tarball
/tmp/board-probe/focus.py             # board 成员链路（邀请 → token → board_status 200）
```

探针只读引用门禁夹具（`aimail-advanced/tests/SDK/agent-level/lib/gateway_fixture.py`），
不改任何门禁文件；board 探针在**本地夹具**里把该产品 `max_active_boards` 临时改为 2，
只为越过配额闸验证其余链路（产品代码未改）。

### 2.4 回归

```bash
cd ~/aimail-advanced && python3 tests/advanced/e2e-open-apply.py   # 期望 42 PASS / 0 FAIL
```

### 2.5 对抗性复核建议

1. 断网/停网关跑 `gate.sh`：应 rc=2 或对应 case 红，不得静默通过；
2. 改坏 `docs/agent-integration.md` 里任一契约值（或改坏其中任一符号）→
   `tests/contract/check-docs-consistency.py` 必红。自举文档 `docs/agent-self-setup.md`
   按设计只带 **0 项**契约值（全部归平台文档），所以对它只能做**符号**对抗（改坏被引用的
   符号/命令名），不能期待"改契约值变红"；
3. 同一 L1 连跑两次结论一致（不允许“重跑当修复”）；
4. 抽查四份 JSON 的 `evidence` 与 `fixture/server.log`，确认无密钥泄漏；
5. 复核 `board_status`：用 agent key（不带 board token）调用必须 401 —— 证明
   “board 可用”来自成员 token，而不是放宽了鉴权。

## 3. 自验凭据（2026-10-19 本批）

```
组件 / 版本 / commit
  aimail-gateway    2c2e65c + 本次闭环提交   version 1.0.3（未 bump）
  aimail-advanced   d6be955 + b7f207b（板配额按系统计数）
                    version 1.0.1（未 bump）binary md5 106df369，0 个源码新于二进制
  aimail (SDK)      65dd3af f416aa6 ec7a2f4 863127e 1c8f8bf 0ccac9f b4affe9 d07a9ae
                    27d104b 2bd24a2 eddc454 36b850e d670c63
  安装产物          aimailsdk 0.1.55 / @aimail/mail-core 0.1.54（**仍是旧产物**，见 §4）

门禁（证据 /tmp/a1-final6）
  RESULT: 29 cases · 29 pass · 0 fail        l1-py.json
  RESULT: 13 cases · 13 pass · 0 fail        l1-ts.json
  RESULT: 14 cases · 14 pass · 0 fail        l2-py.json
  RESULT: 14 cases · 14 pass · 0 fail        l2-ts.json
  GATE a1 all: PASS（含板配额修复 + 闭环通知的二进制 106df369）

SDK
  tests/sdk/l0-gate-tests.sh  → [L0] PASS（契约 4/4 + 平台 5/5 + frontmatter 5/5 + literal 棘轮）
  pytest tests/sdk            → 277 passed
  pnpm test (vitest)          → 288 passed / 37 files

干净环境探针
  driver_py.py  16/16 PASS   （fresh venv，安装 wheel；空 HOME；只给 prompt）
  driver_ts.py  17/17 PASS   （fresh npm 工程，只装 @aimail/mail-core + typebox）
  focus.py      PASS         （board 成员：邀请 → board_creds.json → board_status 200）

回归
  tests/advanced/e2e-open-apply.py → 42 PASS / 0 FAIL
```

探针逐项证据（要点）：

- `/tmp/cli-loop/probe.py` 9/9：shell 侧只做 `pip install aimailsdk` + `aimail-agent setup`
  （打印 15 个工具、装 skill）；agent 侧用工具 `activate_address_code` 激活，pull 运行时把信
  交成回合；审批后 agent 收到 `[AIMail] Persona applied`，安全官收回执，meta 落库；
- host 只收到 prompt 文本，自行解析 address/code/manager/gateway，随后激活成功；
- binding 的 `manager_address` == 网关写入值；连接文件键 ==
  `[domain, gateway_url, scope, system_id]`（无 admin key）；
- toolset = **15** 个（含 6 个 board 工具）；skill 落在 `<skills>/<skill name>/SKILL.md`；
- pull 运行时 1 条循环；SDK 把 enriched 交回合（`message_id` 非空）；
- 签名/persona 经 manager 审批后 whoami 可见（role_calibrator 的批准环节）；
- 本地 `search_mail` 命中入站 token（两语言一致）；
- 同线程回信 201，manager sink 收到正文。

## 4. 发版计划（待 owner 点头后执行）

| 顺序 | 包 / 仓 | 当前 → 目标 | 内容 | 配对声明 |
|---|---|---|---|---|
| **0** | **CLI（`cli-v*`）** | 0.1.55 → 0.1.56 | 新增 `aimail agent doctor/setup/uninstall`；`scripts/agent-install.sh` 装的就是这个二进制 ⇒ **必须先发**，否则 S1 装到旧二进制（无 `agent` 子命令） | 独立版本线；与 SDK 无版本绑定 |
| 1 | aimail-gateway | 1.0.3 → 1.0.4 | D3 线程头归一（单点） | advanced 以 path 依赖编入，两者同批；SDK 不改 |
| 1 | aimail-advanced | 1.0.1 → 1.0.2 | D1 白名单、D2 错误映射、激活响应 `manager_address` | 与 gateway 同批 |
| 2 | `@aimail/mail-core` | 0.1.54 → 0.1.55 | D4 连接文件、G1 manager 落盘、agent-level 运行时、入站快照、board 邀请解析、board 裸 id、随包 skill | 先发；旧 pysdk 忽略新字段不受影响 |
| 3 | pysdk (`aimailsdk`) | 0.1.55 → 0.1.56 | 同 mail-core 的语义 + G2 上下文注入 | 与 mail-core 语义等价，可并行 |
| 4 | `@aimail/mail` | 0.1.54 → 0.1.55 | 依赖 mail-core（五包齐升约定） | 无代码变更，仅版本对齐 |
| 4 | openclaw-aimail / pi-aimail / dsh-aimail | 0.1.57 / 0.1.56 / 0.1.58 → +1 | bundled mail-core 内容变了，必须重发 | 三个都必须重发；否则用户装到的还是旧 bundle |

发版注意：

- 适配包带 `bundleDependencies`，实测 tarball 内 SKILL.md 份数：openclaw 3（411K）、
  pi 3（395K）、dsh 1（26K，不 bundle）。**重复是既有打包方式的必然结果**，净增 1 份/包；
  去重属后续优化（改三个适配包的资源读取路径 + 现 5/5 frontmatter 门禁），不建议塞进本批。
- 发版后验证（按既有 release gate）：PyPI integrity/provenance、npm 可见性与
  `npm pack` 内容（`resources/skills/SKILL.md` 必须在）、安装后
  `import aimail; aimail_agent.start_agent_level` 可导入。
- 回滚：按包回退 tag/版本即可；本批无 DB schema 变更，advanced 回退后旧 SDK 仍可用
  （只是没有 manager/快照等新行为）。
- **`pip install aimailsdk` 目前仍拿 0.1.55，`npm i @aimail/mail-core` 仍是 0.1.54
  （旧代码）**：干净环境按新文档一步不落，以本批发版为前提。
- **CLI 必须先发**：`agent-install.sh` 拉的是 release 资产；0.1.55 没有 `agent` 子命令，
  发 CLI 之前 S1→S3 跑不起来（安装器本身可用，`aimail version` 会过）。
- 建议同批给 release 增加 `<asset>.sha256` sidecar：安装器已支持"有则校验"，当前只记录哈希。

## 5. 未做 / 不可为（区分范围与约束）

| 项 | 类型 | 说明 / 代价 |
|---|---|---|
| `tests/run-e2e.sh --release advanced` 全量档（category-21..25 + warning-gate） | 未做（范围） | 本批只跑了与激活语义直接相关的 `e2e-open-apply.py`（42 PASS）。发版前要跑，约 10+ 分钟 |
| Docker 多宿主回归、云端线上投递 | 未做（范围） | 需要生产资源，按既有边界不在本批 |
| 真 LLM 自主选工具 | 不可为（约束） | 需固定模型/key，属另轮 report-only |
| 地址级 agent **创建** board | 不可为（产品裁决） | `advanced/src/server.rs:1124` 地址码产品 `max_active_boards: Some(0)`；owner 已裁定“只能当成员、不能自己开 board”合理，本次保留 |
| ~~board 配额计数口径~~ | **已在本批修复** | `board_quota.rs` + `storage.rs` 改为“配额与计数同作用域”；回归测试：某系统配额 1、另系统已有 1 块板时，新系统仍应放行（修复前会被误拒）。advanced 单测 94 passed |
| ~~批准后 agent 无闭环通知~~ | **已在本批修复** | 网关新增 agent 侧 `[AIMail] Persona applied` 通知（内部收件人走 `create_inbound`，绕开 loopback）；`/tmp/cli-loop/probe.py` 实测 9/9（shell 只装+注册，激活在 agent 内部） |
| Python 身份名片无模块级 helper | 已知不对称 | 用 `client.agent_state_put("public_whoami", …)`（文档已按此写并实测）；TS 有 `setPublicWhoami`。是否补同名 helper 待定，不影响可用性 |

## 6. 门禁改动声明（SDK 侧，非本场景门禁）

本场景门禁（`aimail-advanced/tests/SDK/agent-level/` 的 cases/断言/夹具/`run-e2e.sh`/
`RELEASE-GATES.md`/`tests/README.md`）**一行未动**；advanced 工作树里仍是验证方那批
未提交改动，落库由验证方做。

动了两个**产品**门禁（owner 已授权“去品牌”）：

- `tests/contract/check-docs-consistency.py`：契约值断言**按层重钉**而非删减 ——
  自举文档 4 项（skill 名/绑定文件/指针/前缀），平台文档 5 项（入站路径 + Hermes 例外 +
  9099/9101/8001）；新增 `--platform-en/--platform-zh` 便于自验。输出仍为
  `符号 20/20、标题 11 条逐条同构、契约值 4/4 + 5/5`。
- `tests/sdk/test_docs_impl_consistency.py`：红样例改用 `binding_file`，并新增
  “平台文档路径写错必须红”一条（证明未放宽）。

未新增 allow-list、未跳过任何断言、未改阈值。

## 7. 交接物清单

- 本文：`docs/handoff-agent-level-scenario-1.md`
- 门禁交接单（验证方）：`aimail-advanced/tests/SDK/agent-level/HANDOFF.md`
- 首轮失败报告（对照基线）：`aimail-advanced/tests/SDK/agent-level/reports/2026-10-19-first-run.md`
- 探针（本地、不入库）：`/tmp/clean-env-verify/driver_py.py`、`driver_ts.py`、
  `/tmp/board-probe/focus.py`
- 门禁证据：`/tmp/a1-final6/`（四份 JSON + 日志）

## 8. 复验请回传

1. `GATE a1 all` 的 rc 与四份 JSON 的 `fail`/`GAP`（本批证据 `/tmp/a1-final7`）；
2. 是否认可 §4 的版本/顺序/配对（尤其 **CLI 必须先发**、适配三包必须重发）；
3. 若发现本文与代码不符，直接指出 `file:line`，不要改门禁判据；
4. 发版后请在 PyPI/npm 实测一次 `pip install aimailsdk` / `npm i @aimail/mail-core`，
   再跑 `driver_py.py` / `driver_ts.py`（"干净环境照文档可跑"的最终判据）；
5. 发版后用**发布出去的**二进制跑一遍：`scripts/agent-install.sh` → `aimail version`
   → `aimail agent doctor --questions`，确认装到的版本**带 `agent` 子命令**（这是 §10 的 S1/S2 门槛）；
6. 真机冒烟若已具备条件，贴 `tests/cli/agent_host_smoke.py --json` 的表格；不具备就写"未跑（无平台）"。

## 9. agent 级自举（本轮新增，M1–M6）

**做了什么**

| 产物 | 位置 | 说明 |
|---|---|---|
| 地址级安装入口 | `scripts/agent-install.sh` | 只要 shell+curl：平台探测 → 静态二进制 → sha256（有 sidecar 则校验，否则记录）→ `~/.local/bin/aimail` → `aimail version` 冒烟 → `~/.aimail/agent-install.json`。无环境变量、无 bridge、不写 `.env` |
| `aimail agent doctor` | `cli/src/cmd/agent.rs` | `--questions` 输出 1-shot 提问块（表格+反例+自检）；`--answers` 逐条验证（`command -v`/`test -d`/`test -w`/读示例插件取注册形态/抽 Q7 注入符号）→ host profile + **SDK 选型** + verdict（ready/blocked/unsupported，rc 0/5/4） |
| SDK 选型 | 同上 §2 | 命中已适配宿主 → 其包（aimailsdk/dsh-aimail/openclaw-aimail/pi-aimail）；否则按 runtime → `@aimail/mail-core` / `aimailsdk`；other → unsupported |
| `aimail agent setup` | 同上 | 按 profile 渲染 node/python 适配包（工具从 SDK 注册表取、pull 在 init 起、投递走 Q7 给的入站调用）+ 装 skill；写 `<plugin>/aimail-<host>/` 与 `<skills>/<skill 名>/`，state 记 sha256；默认 dry-run，`--apply` 才写，可选调 reload |
| `aimail agent uninstall` | 同上 | 只删 sha256 未变的本次文件，外来插件不动 |
| **SDK hot-add** | `pysdk/aimail_base.py`、`aimail_tools.py`、`aimail_agent.py`；`tssdk/.../poll-entry.ts`、`binding-hooks.ts`、`agent-level.ts`、`address-code.ts` | 激活落盘触发 binding-created 钩子 → 同进程为新绑定起 pull 句柄（按 email 去重、可 stop）。**激活后不需要第二次重启** |

**验证（本机实测）**
- 单进程热加 `/tmp/hotadd-driver.py` **6/6 PASS**：0 绑定起 runtime → 进程内激活 → handles 0→1 → 来信成为回合 → 回信 → 对端收到。无 reload、无重启、数据面不经 CLI。
- 适配选型矩阵 `/tmp/m6-matrix.py` **11/11 PASS**：hermes/dsh/openclaw/pi/deerflow 各命中自己的包；未知 node 宿主 → generic mail-core 并成功落盘；runtime=other ⇒ unsupported(4)；无 Q7 ⇒ unsupported(4)；目录不可写 ⇒ blocked(5)；reload 失败 ⇒ `"ok": false` 且安装仍记录。
- 单测/门禁：pytest 277、vitest 288、`cargo test --test agent_bootstrap` 3 passed、cli_surface 10 passed、L0 PASS、`GATE a1 all PASS`（/tmp/a1-final7）。

**发版顺序（新增，重要）**
1. **CLI 必须先发**（`cli-v*`）：`agent-install.sh` 装的是 release 二进制，当前 0.1.55 没有 `agent` 子命令 ⇒ 先发 CLI，S1→S3 才成立。
2. SDK 两包按 §4 顺序；适配包同批（skill 已含首运行/边界规则，bundled 内容变化）。
3. 建议同时给 release 补 `<asset>.sha256` sidecar（安装器已支持校验，缺 sidecar 时只记录）。

**未做（本机无这些平台）**
- 四家宿主**真机**冒烟（hermes/dsh/openclaw/pi 未安装在本机）：本批只做了"适配选型 + 假宿主全链路"，真机冒烟需在装有对应平台的机器上跑 `agent-install.sh → doctor --questions → setup --apply`，并记录是零重启还是一次重启。

**已知边界**
- 无 shell ⇒ 无法自举（明确告知用户开权限，不做假装）。
- 宿主无"入站注入"能力（Q7=none）⇒ 判 unsupported：没有它就没有进程内 pull，收信不成立。
- 未识别宿主且插件 API 无法用两种 runtime 模板表达 ⇒ unsupported，profile 留档供后续加模板。

**冒烟脚本（新增）**：`tests/cli/agent_host_smoke.py`（手动，不入 L0）。读 `aimail platform spec`，
对本机已装宿主输出 detected / mechanism / package / installed / profile / native_cmd / restart，
`--json` 给全量命令；directory 类宿主可 `--answers-dir` + `--apply` 走 doctor/setup。
本机实跑：openclaw 适配包已装，pi/dsh/hermes 缺包（各自 native 命令已列出），deerflow 未安装。

**由此确认的机制分裂（不可混用）**：五家已适配宿主是 **package 机制**（包管理器装
`openclaw-aimail` / `pi-aimail` / `dsh-aimail` / `aimailsdk`）；**directory 机制**（未知宿主）
才用模板渲染。

**package 分支已补**：`aimail agent setup --host <name>` 只挑平台表里"argv 含该适配包"的
`spawn` 步（不跑 sdk_install/register 系统级步、不跑 docker 步），复用 `core::steps::run_steps`
的占位符与 `skip_if` 语义，跑完用 `pkg_present` 校验（python 走 importlib.metadata，node 走
home / npm root 查找），并回放平台表里的重启提示。实测：`--host openclaw --apply` 命中
`skip_if`（已装 ⇒ 跳过安装、校验在位、rc=0，**未改动现场环境**）；`--host pi|hermes` dry-run
给出各自命令；未知 host ⇒ rc=2 并列出已知宿主。

## 10. 发版执行清单（verifier 可直接照做）

### 10.1 版本 bump（一次改完，再统一跑门禁）

| 文件 | 现在 | 目标 | 备注 |
|---|---|---|---|
| `cli/Cargo.toml` `version` | 0.1.55 | **0.1.56** | 独立版本线；publish.yml 由此推导 `cli-v<ver>` |
| `aimail-gateway/Cargo.toml` | 1.0.3 | 1.0.4 | 与 advanced 同批 |
| `aimail-advanced/Cargo.toml` | 1.0.1 | 1.0.2 | path 依赖 gateway |
| `pyproject.toml` (`aimailsdk`) | 0.1.55 | 0.1.56 | |
| `tssdk/packages/mail-core/package.json` | 0.1.54 | 0.1.55 | |
| `tssdk/packages/mail/package.json` | 0.1.54 | 0.1.55 | 五包齐升约定（无代码变更，仅对齐） |
| `tssdk/packages/openclaw-aimail/package.json` | 0.1.57 | 0.1.58 | bundled mail-core 变了 ⇒ 必须重发 |
| `tssdk/packages/pi-aimail/package.json` | 0.1.56 | 0.1.57 | 同上 |
| `tssdk/packages/dsh-aimail/package.json` | 0.1.58 | 0.1.59 | 同上 |

### 10.2 本地预检（bump 之后）

```bash
cd aimail && bash tests/sdk/l0-gate-tests.sh              # 期望 L0 PASS
cd aimail-gateway && cargo test --lib                     # 期望 290 passed（含 persona 闭环通知）
cd aimail-advanced && cargo build --bin aimail-advanced && cargo test --bin aimail-advanced
A1_EVIDENCE=/tmp/a1-rel bash tests/SDK/agent-level/gate.sh   # 期望 GATE a1 all: PASS
cd aimail/cli && cargo test --test cli_surface --test agent_bootstrap   # 10 + 4
python3 tests/cli/agent_host_smoke.py --json /tmp/host-smoke.json        # 只读，记录表格
```

### 10.3 发布（dispatch，不手打 tag）

1. `publish.yml` → **dry_run=true** 先验：构建四平台 CLI 资产、SDK 打包、门禁；不建 tag、不发布。
2. dry-run 绿后 → `publish.yml` → dry_run=false（`release-type` 留空按版本推导，或显式 `stable`）：
   自动建 `cli-v0.1.56` 与 SDK tag，发 Release 资产，publish npm/PyPI。
3. **顺序不可倒**：CLI 先落地（`agent-install.sh` 拉的是 release 资产；0.1.55 没有 `agent`
   子命令，先发 SDK 也跑不通 S1→S3）。SDK / 适配包可在同一 dispatch 内完成。

### 10.4 发版后验收（硬证据，非"job 绿"）

```bash
curl -s https://pypi.org/integrity/aimailsdk/0.1.56/<wheel>.whl/provenance   # workflow=publish.yml, environment=pypi-release
npm view @aimail/mail-core@0.1.55 version          # 可见
npm view openclaw-aimail versions --json | tail -3  # 三个适配包都在
# 干净容器（无 SDK、无 HOME 残留）：
curl -fsSL https://raw.githubusercontent.com/metercai/aimail/main/scripts/agent-install.sh | bash
aimail version                                      # 期望 0.1.56
aimail agent doctor --questions | head -3           # 期望打印提问块（证明 agent 子命令在）
aimail agent setup --host openclaw                  # dry-run，期望给出 native 命令
pip install aimailsdk==0.1.56 && python -c "import aimail, aimail_agent; print(aimail_agent.agent_toolset() and 'ok')"
npm i @aimail/mail-core@0.1.55 && node -e "const m=require('@aimail/mail-core');console.log(m.agentTools().length)"   # 期望 15
```

### 10.5 回滚

- 逐包回退版本（tag 保留，Release 可下架）；本批**无 DB schema 变更**，advanced 回退后旧 SDK 仍可用。
- CLI 回退 = 重新 dispatch 上一版本（`cli-v0.1.55`），`agent-install.sh --version cli-v0.1.55` 可钉住。
- 适配包必须与 mail-core 同批回退，避免 bundle 内 SKILL.md 与核心包语义不一致。

## 11. 交接边界（我做了什么、没做什么）

**做了**：产品代码（gateway/advanced/SDK/CLI/文档/skill）、自验（门禁 + 单测 + 单进程探针 +
适配矩阵 + 冒烟脚本）、交接文档、push 代码仓。

**没做（不在实现方权限内）**：bump 版本、打 tag、dispatch publish、PyPI/npm 发布；
在 owner 机器上安装缺失的适配包（`pi-aimail`/`dsh-aimail`/`aimailsdk`）——那会改动其现有 agent 环境；
四家宿主真机端到端（本机四家宿主程序在位，但只跑了只读冒烟，未改动）。

**判据归属**：门禁判据、场景用例、断言、夹具归验证方；本文只描述产品事实与证据，两者冲突时以
`aimail-advanced/tests/SDK/agent-level/HANDOFF.md` 的门禁口径为准，并把冲突指回来。
