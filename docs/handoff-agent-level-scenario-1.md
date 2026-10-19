# 交接：场景 1（agent 级对话安装）产品改造 — 复审与发版

> 交接对象：具有**最终审验与发版权限**的专属 agent。
> 本文只写产品侧事实（改了什么、怎么验、证据在哪、怎么发），门禁判据仍以
> `aimail-advanced/tests/SDK/agent-level/HANDOFF.md` 为准。
> 门禁目录（cases/断言/夹具/入口）由验证方所有：本批**一行未改**，也未提交。
> 反馈路径：复验结论 / 发版结果 / 异议，请直接回给 owner（本交接由实现方出具）。

## 0. 一页结论

- 场景 1 = 用户把激活 prompt 粘进对话，agent 自己装 SDK → 激活 → 收信回信。
  **不需要 CLI、bridge、系统级凭据、admin key；不监听任何端口（纯 pull）。**
- 门禁 `GATE a1 all: PASS`（L1-PY 29/29、L1-TS 13/13、L2-PY 14/14、L2-TS 14/14，
  四份 JSON `fail=0`、无 GAP）。
- 额外用两个**干净环境探针**（无 CLI、空 HOME、只给 prompt 文本）验证了真实宿主路径：
  Python 16/16、TypeScript 17/17；另有一个 board 成员链路探针 `board_status` = 200。
- 待发版：gateway 1.0.3→1.0.4、advanced 1.0.1→1.0.2、`@aimail/mail-core`
  0.1.54→0.1.55、pysdk 0.1.55→0.1.56，外加三个适配包同批升（bundled 内容变了）。
  **未打 tag、未 publish、未 push**（push main = 发布，等 owner 点头）。

## 1. 产品侧新增与变更

### 1.1 Rust 服务端

| 域 | 位置 | 变更 | 配对 / 影响面 |
|---|---|---|---|
| advanced | `src/advanced/api/address.rs:182` `act_err_to_app` | `ActErr` 保留语义映射到 `AppError`（Invalid/NoBareDomain/DomainConflict→400、BoundToOther→403、AlreadyActive→409、Db→500）；删除旧死代码与 `act_err_msg` | 仅激活路径；错误文案不变 |
| advanced | `src/advanced/api/address.rs:437-496` | 激活事务 6c 写 `domain_addr_meta` 后新增 6d：按**回读的 effective manager** 插 `direction='all'` 的 agent↔manager 白名单（与 register 同款，`category='system'`，`ON CONFLICT` 保 `is_active=1`）；`ActivationOutcome` 增加 `manager_address`（:154）并随响应返回（:264） | 续期沿用已存 manager，不被本轮申请人覆盖；白名单插入不走 API 配额 |
| gateway | `src/core/api/send.rs:1327` `normalize_thread_headers` | 合并 headers 后、board 注入与 Auto-gen 之前，把 `Message-ID`/`In-Reply-To`/`References` 按名归一为 `message_id`/`in_reply_to`/`references`（大小写与 `-`/`_` 不敏感，规范键优先），其它 header（含 `X-Board-*`）不动；Auto-gen 只看规范化后的 `message_id` | **D3 单点修在网关**，SDK 保持发 RFC 大写键不动；gateway+advanced 同批发 |

### 1.2 SDK（Python pysdk / TypeScript @aimail/mail-core）

| 位置 | 变更 |
|---|---|
| `pysdk/aimail_tools.py:529`、`tssdk/.../address-code.ts` | 激活响应里的 `manager_address` 落进 binding（空则不写键）；这是 manager 的**第一来源**（网关在激活事务内回读后返回），不解析 prompt、不事后 whoami 反查 |
| `pysdk/aimail_tools.py:845`、tssdk `address-code.ts:150` | 连接文件只写 `gateway_url/system_id/domain/scope=agent`，**不再写 `admin_key`**（D4）；激活成功后自动 `set_agent_context`（G2），显式再调幂等 |
| `pysdk/aimail_agent.py`（新）、`tssdk/.../agent-level.ts`（新） | agent 级运行时：`agent_toolset()`/`agentTools()`（15 个工具，含 6 个 board 工具，每条自带 handler）、`install_agent_skill()`/`installAgentSkill()`（幂等装 `<skills>/agentmail/SKILL.md`）、`start_agent_level()`/`startAgentLevel()`（装 skill + toolset + pull 循环；默认链 = 共享入站管线，把 enriched 交给 `on_agent`/`onAgent`；ping/pong 不入回合；单绑定注入上下文） |
| `tssdk/.../meta.ts:218` `saveInboundSnapshot` | TS 补齐 Python 的入站快照落盘（meta 常写 + `in-*.json` 受 `save_raw_snapshots` 控制 + FTS 行）。原因：TS 链原先不写入站快照，`search_mail` 只能搜到出站 |
| `pysdk/aimail_base.py` `_extract_board_gateway` | board 邀请解析：**`Board ID:` 优先**；回退用**发件人地址的邮件域**算 `sha256(board address)[:20]`（不再用网关 host）；pull 报文只有 `sender` 时也认 |
| `tssdk/.../board.ts:55` `boardInviteFrom` + `preprocess.ts` | 同上，单一解析器；`processInboundMail` 用它注册/落盘 token |
| `pysdk/aimail_board.py` `_board_id_of` | board 工具接受**裸 board id**（邀请邮件里的就是裸 id）、`b_<id>`、task id；原先把裸 id 走 task-id 解析器得到空串 ⇒ 跳过 token ⇒ 用 agent key ⇒ 401 |
| `tssdk/.../package.json` + `scripts/stage-resources.mjs` | 通用核也随包发 `resources/**`（prepack 暂存、gitignore），非适配 TS 宿主因此能拿到 skill 源 |

### 1.3 文档

`docs/agent-self-setup.md` / `_zh.md` 重写为 0–9 节（en/zh 标题逐条一致）：

- 只按语言分（py→`pysdk`、ts→`@aimail/mail-core`），**不按 agent 产品分**；
- §2 下载并加载：`pip install aimailsdk` + **必须先 `import aimail`**（实测：干净 venv 直接
  `from aimail_tools import …` = ModuleNotFoundError）；`npm install @aimail/mail-core`；
- §3 只写两次 SDK 调用（toolset + skill 安装），不再教宿主抄代码；
- §5 一次 `start_agent_level` 起 pull：**不开端口/不暴露路由/不注册 push 目标**，
  信交进程内 `on_agent`；`webhook_url` 为空合法；
- §6 首次运行：身份名片（agent 自写 `public_whoami`）+ 签名（**非自助**，manager 邮件
  审批，pull 模式下该审批信由网关消费、不投给 agent）；
- 系统级能力清单**不在文档里**（属运维信息，不进 prompt）；平台端点事实留在
  `docs/agent-integration*.md`。

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
cd ~/aimail && python3 -m pytest tests/sdk -q         # 期望 275 passed
cd ~/aimail/tssdk && pnpm build && pnpm test          # 期望 287 passed / 35 files
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
2. 把 `docs/agent-self-setup.md` 里任一契约值改坏 → `check-docs-consistency.py` 必红；
3. 同一 L1 连跑两次结论一致（不允许“重跑当修复”）；
4. 抽查四份 JSON 的 `evidence` 与 `fixture/server.log`，确认无密钥泄漏；
5. 复核 `board_status`：用 agent key（不带 board token）调用必须 401 —— 证明
   “board 可用”来自成员 token，而不是放宽了鉴权。

## 3. 自验凭据（2026-10-19 本批）

```
组件 / 版本 / commit
  aimail-gateway    2c2e65c   version 1.0.3（未 bump）
  aimail-advanced   d6be955   version 1.0.1（未 bump）binary md5 2ad5173e，0 个源码新于二进制
  aimail (SDK)      65dd3af f416aa6 ec7a2f4 863127e 1c8f8bf 0ccac9f b4affe9 d07a9ae
                    27d104b 2bd24a2 eddc454 36b850e d670c63
  安装产物          aimailsdk 0.1.55 / @aimail/mail-core 0.1.54（**仍是旧产物**，见 §4）

门禁（证据 /tmp/a1-final4）
  RESULT: 29 cases · 29 pass · 0 fail        l1-py.json
  RESULT: 13 cases · 13 pass · 0 fail        l1-ts.json
  RESULT: 14 cases · 14 pass · 0 fail        l2-py.json
  RESULT: 14 cases · 14 pass · 0 fail        l2-ts.json
  GATE a1 all: PASS

SDK
  tests/sdk/l0-gate-tests.sh  → [L0] PASS（契约 4/4 + 平台 5/5 + frontmatter 5/5 + literal 棘轮）
  pytest tests/sdk            → 275 passed
  pnpm test (vitest)          → 287 passed / 35 files

干净环境探针
  driver_py.py  16/16 PASS   （fresh venv，安装 wheel；空 HOME；只给 prompt）
  driver_ts.py  17/17 PASS   （fresh npm 工程，只装 @aimail/mail-core + typebox）
  focus.py      PASS         （board 成员：邀请 → board_creds.json → board_status 200）

回归
  tests/advanced/e2e-open-apply.py → 42 PASS / 0 FAIL
```

探针逐项证据（要点）：

- host 只收到 prompt 文本，自行解析 address/code/manager/gateway，随后激活成功；
- binding 的 `manager_address` == 网关写入值；连接文件键 ==
  `[domain, gateway_url, scope, system_id]`（无 admin key）；
- toolset = **15** 个（含 6 个 board 工具）；skill 落在 `<skills>/agentmail/SKILL.md`；
- pull 运行时 1 条循环；SDK 把 enriched 交回合（`message_id` 非空）；
- 身份名片写入 200 且能读回；签名经 manager 审批后 whoami 可见；
- 本地 `search_mail` 命中入站 token（两语言一致）；
- 同线程回信 201，manager sink 收到正文。

## 4. 发版计划（待 owner 点头后执行）

| 顺序 | 包 / 仓 | 当前 → 目标 | 内容 | 配对声明 |
|---|---|---|---|---|
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

## 5. 未做 / 不可为（区分范围与约束）

| 项 | 类型 | 说明 / 代价 |
|---|---|---|
| `tests/run-e2e.sh --release advanced` 全量档（category-21..25 + warning-gate） | 未做（范围） | 本批只跑了与激活语义直接相关的 `e2e-open-apply.py`（42 PASS）。发版前要跑，约 10+ 分钟 |
| Docker 多宿主回归、云端线上投递 | 未做（范围） | 需要生产资源，按既有边界不在本批 |
| 真 LLM 自主选工具 | 不可为（约束） | 需固定模型/key，属另轮 report-only |
| 地址级 agent **创建** board | 不可为（产品裁决） | `advanced/src/server.rs:1124` 地址码产品 `max_active_boards: Some(0)`；owner 已裁定“只能当成员、不能自己开 board”合理，本次保留 |
| board 配额计数口径 | 已知遗留 | `advanced/src/advanced/board_quota.rs:53-66` 用**全局** active 计数对比**每系统** max；地址码 max=0 时无影响，一旦有系统配额>0 会互相挤占。属独立缺陷，建议另开 |
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
- 门禁证据：`/tmp/a1-final4/`（四份 JSON + 日志）

## 8. 复验请回传

1. `GATE a1 all` 的 rc 与四份 JSON 的 `fail`/`GAP`；
2. 是否认可 §4 的版本/顺序/配对（尤其适配三包必须重发）；
3. 若发现本文与代码不符，直接指出 `file:line`，不要改门禁判据；
4. 发版后请在 PyPI/npm 实测一次 `pip install aimailsdk` / `npm i @aimail/mail-core`
   再跑 `driver_py.py` / `driver_ts.py`（这是“干净环境照文档可跑”的最终判据）。
