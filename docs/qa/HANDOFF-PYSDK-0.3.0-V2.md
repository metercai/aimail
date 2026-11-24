# 交接（pysdk SSE-V2 重构 → 0.3.0 待发）：请质检复查复验并确定正式发版计划

日期：2026-11-24
交接方：开发（PI-Desktop，pysdk 轨道）
接收方：质检 / verifier
范围：`/home/ubuntu/aimail`（pysdk + 门禁夹具）；对照仓 `~/aimail-gateway`（base v1.0.15）；tssdk 未改产品代码

> **本文用途**：让质检在**不看开发对话**的前提下，独立复验 pysdk 的 SSE-V2 重构，并据此**确定 0.3.0 的正式发版计划**。
> 本文只写"可被验证的事实"，凡未执行或未验证的一律标红，不做美化。§7 是给质检的复验清单，§8 是发版决策输入。

---

## 0. 一句话结论

```text
开发侧自评：代码完成 + 三宿主门禁全绿，具备送质检复验的条件
VERDICT(开发自评，非质检结论): PASS
pysdk 0.3.0 @ 工作树（基线 commit 9b96630，本轮改动未提交）
L0      PASS（all gates green，含 tssdk build + vitest）
L2      hermes  PASS=14 (s1 s2 s3 s4 neg) + s5 PASS=2 · RED=0 GAP=0 SKIP_LEGACY=0 · **无宿主 patch**
L2      deerflow PASS=15 RED=0 GAP=0 SKIP_LEGACY=0  (s1 s2 s3 s4 neg) + s5 PASS=2
L2      dsh/ts  PASS=16 RED=0 GAP=0 SKIP_LEGACY=0   (回归，未变红)
单测    pytest tests/sdk → 522 passed / 0 failed
```

**⚠ 首要事实**：本轮全部改动**都在工作树、未提交**。质检复验的对象是"当前工作树"，不是任何 tag。

---

## 1. 背景：为什么要做这次重构

pysdk 旧模型的四个硬依赖，与宿主侧 SSE-V2 契约（`docs/aimail-host-sse-contract-v2.md`）冲突：

| 旧依赖 | 问题 |
|---|---|
| CLI 驱动安装（`aimail install`） | 安装即改宿主源码，宿主版本一动就碎；与"pip 安装即完成"冲突 |
| push/webhook 入站 | 需要 SDK 自己开端口、宿主 webhook 模块被 patch；per-profile 端口互相踩 |
| host 源码 patch | 升级宿主后 patch 锚点失效 |
| 每地址一条拉取 | 一宿主多地址即多流、多 key、ack 语义不一致 |

SSE-V2 的目标形态（契约 §5/§9）：**每 `(AgentSystemId, Gateway)` 一条聚合 SSE**，投递后由宿主接受再 ack，安装只剩 `pip install` + 宿主自己的插件机制。

方案与逐阶段记录：`docs/pysdk-v2-sse-refactor-plan.md`（§1–§14 设计；**§15 是滚动交付记录，共 10 节，含每一处真实缺陷的定位过程**）。设计细节请以 §15 为准，本文只做交接摘要。

---

## 2. 交付物地图（本轮新增/改动）

### 2.1 产品代码（新增，共 ~2063 行）

| 文件 | 行 | 职责 |
|---|---|---|
| `pysdk/v2/agent_system_id.py` | 132 | AgentSystemId 派生（宿主 home → 稳定 id）+ seam |
| `pysdk/v2/binding.py` | 159 | V1/V2 判定、分组（每 ASID+Gateway 一组）、逐投递重读 |
| `pysdk/v2/pull_client.py` | 64 | 与 tssdk 同形的 SSE/long-pull 客户端薄层 |
| `pysdk/v2/system_pull.py` | 230 | reconcile：建/停聚合流；`SSE reconcile: N … from M binding(s)` |
| `pysdk/v2/thread.py` | 110 | thread root（`references[0]→in_reply_to→message_id`）、`chat_id`/`thread_id` 派生、followup/new |
| `pysdk/v2/outbound.py` | 187 | **P4**：出站 Message-ID → thread root alias（回复追溯） |
| `pysdk/hermes_plugin/{__init__,plugin.yaml,adapter,runtime,identity,tools}` | 706 | hermes 插件（entry point `hermes_agent.plugins`） |
| `pysdk/deerflow_plugin/{__init__,runtime,identity,tools}` | 380 | deerflow 扩展（entry point `deerflow.extensions`） |

### 2.2 产品代码（改动）

| 文件 | 改动要点 |
|---|---|
| `pysdk/aimail_base.py` (+64) | `is_v1_agent_scope_binding` / `is_v2_agent_scope_binding` 拆分（V1 只看 `shared_addr_*` 前缀）；`set_binding_context_provider` / `get_binding_context` 宿主 seam |
| `pysdk/aimail_tools.py` (+195) | `_GatewayClient` 支持路径/额外头；`_deliver_batches` 支持 `skip`（不 ack 不报错）且**失败改为 warning 而非静默计数**；激活带 `X-Agent-System-Id`；persist 写 `agent_system_id`/`workspace_path`；单地址拒绝（C1）；`_record_outbound_alias`（P4） |
| `pyproject.toml` | 版本 → **0.3.0**；新增 `v2/*`、`*_plugin/*`、`v2/outbound.py` 的 force-include；**移除** patch 时代 5 个模块 |
| `pysdk/__init__.py` | `__version__ = "0.3.0"` |
| `pysdk/README.md` | 重写：安装 = `pip install agent-aimail`（无 CLI/patch/端口）+ 0.3.0 breaking 清单 |

### 2.3 门禁夹具（新增）

`tests/host-acceptance/v2/`：`hosts/hermes.sh`、`hosts/deerflow.sh`、`lib/pysdk.sh`、`lib/deerflow_drive.py`、`lib/deerflow_probe_ext.py`、`lib/deerflow-env-gate.sh`；**无任何宿主 patch**（原 `lib/hermes-host-patch.py` 已于 2026-11-24 删除，见 §5.6）；
`tests/agent-env/`：`docker/rebuild-deerflow-base.sh`、`Dockerfile.deerflow`、`set-llm-key.sh`、`llm-config.py`、`audit-hosts.sh`、`README.md`；`.github/workflows/host-images.yml`。

### 2.4 门禁**用例自身**被改过（请质检重点审查，§7-Q3）

| 文件 | 改动 | 是否放宽判定 |
|---|---|---|
| `cases/s4_multiaddr.sh` | 单/多地址分支从"硬编码 pi"改为读宿主能力 `HOST_ADDRESS_MODEL`（pi/hermes=单） | **否**：同一断言（`max_bindings ≤ 1`）只是对更多宿主成立 |
| `lib/gw.sh` | gateway 启动带 `--pid-file`，只清理**本 workdir 的**残留实例 | **否**：为并行跑隔离，避免互杀导致假绿/假红 |
| `run-matrix.sh` | 支持 `--sdk py|ts` | **否**：新增选择器，TS 路径行为不变（本轮 ts 回归 PASS=16） |
| `cases/s5_lifecycle.sh` | pysdk 宿主（`HOST_FRAME_EVIDENCE=0`）**跳过**"0 工具调用 ⇒ GAP"守卫，直接按 `paused`/`final` 判定 | **改变了归因方向，请重点裁定**：此前"模型没调 pause"= GAP（夹具/模型问题）；现在对 hermes/deerflow 会判 **RED**（产品形状）。好处是模型不动作不再被伪装成 GAP；代价是模型不动手时可能出现产品形状的假红。本轮 deerflow s5 实测 `paused=True`、`1 -> 0`，未触发该分支 |
| `hosts/hermes.sh`（2026-11-24） | ① **删除宿主 patch 调用**（原在 `gateway/config.py::by_platform` 顶部插 `discover_plugins()`）；② 安装方式从"复制成目录插件到 `$HERMES_HOME/plugins/aimail`"改为**只 `pip install` wheel、靠宿主 entry point 发现**；③ 自检改 `plugins doctor <site-packages 插件目录>` + `plugins list --user` | **否，反而更严**：门禁从此测的是 README 承诺的真实安装面，且宿主**未被改动**（`host_patch` 证据文件消失）。改后 hermes `s1 s2 s3 s4 neg`=14、`s5`=2 仍全绿。详见 §5.6 |

## 3. 单测现状

```text
pytest tests/sdk            → 522 passed / 0 failed
```
（基线 2026-11-24 之前为 489 passed / 1 failed，红项即下文 §5.4 的兄弟仓文案测试，已修）

本轮新增/重写的套件：

| 套件 | 项数 | 覆盖 |
|---|---|---|
| `test_v2_binding_and_identity.py` | 17 | ASID 派生稳定/宿主变化、V1/V2 判定、分组 rep 选择、paused/foreign 跳过、逐投递重读 |
| `test_v2_pull_client_and_reconcile.py` | 10 | 聚合 client 路径+头（B2 回归）、每 ASID+Gw 一条流、全 paused 不建流、多 Gateway 不交叉 ack |
| `test_v2_outbound.py` | 18 | P4 回复追溯：存取/幂等/LRU/损坏降级、回复命中原始 thread、链式与环、新邮件新 thread、跨 binding 不串、出站头形状 |
| `test_hermes_plugin.py` | 15 | 装载/工具注册/装卸、运行时投递与拒绝、**回复我们的回复留在同一 session**（走真实 runtime） |
| `test_deerflow_plugin.py` | 9 | 扩展装载、工具面、run 提交与幂等、拒绝必须抛错 |
| `test_wheel_manifest.py` | 8 | **打包守护**：每个 `v2/`、`*_plugin/` 模块必须在 force-include 表；legacy 五个不得在表内但文件必须在；目标必须在 `aimail/` 下；无悬空源 |

## 4. 门禁与证据（三宿主）

### 4.1 结果

| 宿主 | 命令阶段 | 结果 | 说明 |
|---|---|---|---|
| hermes | `s1 s2 s3 s4 neg` | **PASS=14** RED=0 GAP=0 SKIP_LEGACY=0 | 单地址模型：S4 断言第二地址被拒绝（C1）；**无宿主 patch + pip entry point** |
| hermes | `s5` | **PASS=2** | pause 标记 + unbind 移除（同样无 patch） |
| deerflow | `s1 s2 s3 s4 neg` | **PASS=15** RED=0 GAP=0 SKIP_LEGACY=0 | 多地址：S4 断言两 binding 共存、`from 2 binding(s)` |
| deerflow | `s5` | **PASS=2** | pause 标记 + unbind 移除 |
| dsh（tssdk 回归） | `s1 s2 s3 s4 neg` | **PASS=16** RED=0 GAP=0 | 确认 pysdk 改动未连带弄红 TS |

> **注（2026-11-24 更新）**：`hermes` 的 `s5` 已补跑并为 **PASS=2**（在"无宿主 patch + pip entry point"夹具下）。
> 因此 hermes 的完整口径为 `s1 s2 s3 s4 neg` = PASS=14 **加** `s5` = PASS=2，`RED=0 GAP=0 SKIP_LEGACY=0`。

### 4.2 复现命令（质检直接可用）

```bash
# LLM 凭据：运行时注入，不打印、不写文件
set -a; . ~/.hermes/profiles/verifier/.env; set +a    # 只载入环境，不回显
export LLM_API_KEY="$XIAOMI_API_KEY"
cd /home/ubuntu/aimail
GW_HTTP_PORT=18210 GW_SMTP_PORT=12710 \
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host hermes --stages "s1 s2 s3 s4 neg"

# deerflow（pysdk）—— s5 必须单独一轮（LIFECYCLE=1 会跳过邮件注入，见 GATE.md）
GW_HTTP_PORT=18210 GW_SMTP_PORT=12710 \
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host deerflow --stages "s1 s2 s3 s4 neg"
GW_HTTP_PORT=18210 GW_SMTP_PORT=12710 \
  bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host deerflow --stages "s5"

# dsh 回归（tssdk，用另一组端口避免与上并行冲突）
GW_HTTP_PORT=18220 GW_SMTP_PORT=12720 \
  bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s2 s3 s4 neg"

# 单测 + 发布门禁
python3 -m pytest tests/sdk -q
bash tests/sdk/l0-gate-tests.sh          # 期望最后一行：═══ [L0] PASS — all gates green ═══
```

**环境前置（缺一即红，且红得不像产品问题）**

| 项 | 值 / 做法 |
|---|---|
| 模型端点 | 镜像内置 gate 端点；镜像里 `api_key` 是占位符 `__LLM_API_KEY__`，**夹具会在 phase 1 运行期注入真 key 并自检**（见 §5.1） |
| deerflow 宿主镜像 | `aimail-host-deerflow:latest`（`DEERFLOW_REV=fc26204d`，可 `tests/agent-env/docker/rebuild-deerflow-base.sh --check` 校验） |
| hermes 宿主 | **不打任何 patch**：门禁只做 `pip install` wheel，宿主通过自己的 entry point 发现插件（`hermes plugins list --user` 显示 `source=entrypoint`），配置里的 `platforms.aimail` 由宿主自己解析（见 §5.6） |
| 端口 | 默认 18210/12710；并行跑务必换端口 + 依赖 `--pid-file` 隔离 |
| umask | 本机为 `0077`；有一个测试曾因 `mkdir(mode=0o755)` 被削成 0700 而假红，已改为显式 `os.chmod` |

### 4.3 证据位置

每次运行在 `/tmp/aimail-l2-<rand>/<host>/` 落盘：`host.log`（宿主日志 + 全部 turn）、`turn_*.log`、`inject.log`（`sent / observed max=… final=… / emails=… / acked_after_poll=…`）、`binding.json`、`journey_state`、`gateway_state`、`mode`。

**注意：`/tmp` 是临时的**。质检复验请以自己那轮的目录为准，不要引用本文出现的旧目录名。

---

## 5. 本轮定位并修掉的**真实缺陷**（质检重点复核对象）

这几条都是"先红后绿"，每条的定位依据都在 `docs/pysdk-v2-sse-refactor-plan.md` §15 有完整过程。

### 5.1 deerflow 全阶段 RED：`401 Invalid API Key`

- 现象：bind turn `status=error`，`messages` 只有 human 消息、模型毫无响应。
- 根因：deerflow **只读** `config.yaml: models[].api_key`，**不读** `$LLM_API_KEY`；镜像里是占位符，而夹具只传了环境变量。
- 修法：夹具 phase 1 先跑 `set-llm-key.sh deerflow`（运行时注入，不烘焙密钥），并**自检占位符残留**——残留即 `boot_error` 直接失败。
- 复核点：`hosts/deerflow.sh` 顶部注入块；篡改 `/app/config.yaml` 为占位符后应立刻失败而不是跑到 turn。

### 5.2 deerflow 投递被拉走但**永不 ack**

- 现象：S3 `observed max=1 final=1`（队列不排空），宿主日志里 delivery 之后没有任何后续。
- 根因：插件经宿主自己的 HTTP API 提交 run，`api_base` 默认镜像端口 `:8001`，而本次 uvicorn 在 `--port 18001` ⇒ 自调用被拒 ⇒ 抛错 ⇒ 不 ack（共享 client 只在 accepted 后 ack）。
- 修法：① 夹具导出 `AIMAIL_DEERFLOW_API=http://127.0.0.1:<实际端口>`；② 产品侧新增 `_post_when_ready()`：**只在"没人监听"(status 0) 时**按 0.25→4s 退避重试到 60s——扩展是在宿主 lifespan 启动过程中装载的，首封邮件可能早于可连接。
- 复核点：故意让 `AIMAIL_DEERFLOW_API` 指向错误端口 ⇒ 应看到 `not accepted` warning 且**不** ack（而不是静默丢）。

### 5.3 S3 证据假象：`observed max=0 final=0`

- 现象：修完 5.2 后仍 RED，但日志显示 run 已被接受——即邮件确实被取走并 ack。
- 根因：ack 发生在**第二次启动的瞬间**，启动后才开始轮询的探针只看到 0→0，无法区分"已 ack"与"从未入队"。
- 修法：探针改为**跨启动观测**（phase 2 之前起 watcher），要求"至少见过一次 pending 且随后归零"才写 `acked_after_poll`。
- 复核点：该改动让"没排队"不再能被判成通过——请审查 `hosts/deerflow.sh` 的 watcher 段是否可能空转通过。

### 5.4 长期红项：兄弟仓文案测试（原被我标为"无关"）

- 现象：`test_v2_contract_surface.py` 一直红，我一度归因"兄弟仓文案变化，与本次无关"。
- 事实：网关 v1.0.15（`c65823d`）**有意**重写了激活邮件提示语，而测试把整句钉死 ⇒ 对方每改文案 SDK 必红。**它阻断 L0 门禁，属发布阻断项**。
- 修法：改为断言**不变量**（必须指向 `aimail_address` 工具、必须提 `agent-aimail` 包、必须走宿主插件机制而非 CLI、地址块不得含 `agent-self-setup.md`/`{manager}`）。
- 复核点：请确认新断言仍能拦住"激活邮件让人去用 CLI"这类回归，而不是被弱化成空断言。

### 5.5 打包陷阱：新模块静默不进 wheel

- 现象：加了 `v2/outbound.py` 但忘了 `pyproject.toml` 的 **hand-maintained force-include 表** ⇒ 容器内 `cannot import name 'outbound' from 'v2'` ⇒ hermes `s1/s2/s3` 全红（`plugin_doctor: registrations: 0 tool(s)`）。
- 修法：补表 + 新增 `tests/sdk/test_wheel_manifest.py`（8 项）把"漏打包"和"legacy 回流"都钉在单测。
- 复核点：删掉任意一行 force-include 后跑该测试应立刻红。

---

### 5.6 **误判**：以为 Hermes 不支持插件平台，于是打了宿主 patch（2026-11-24 推翻）

- 过去的结论（写在方案 §15.5 的 R17、以及上一版交接文档）：宿主不支持"用户插件平台"，`gateway run`
  输出 `No messaging platforms enabled.`，因此写 `lib/hermes-host-patch.py` 在
  `gateway/config.py::by_platform()` 顶部补一次 `discover_plugins()`。
- **实测推翻**。同一镜像（钉版宿主 0.23.2），**整棵树不 patch**，只 `pip install` wheel（不复制任何插件目录）：

```text
hermes plugins list --user      → aimail │ enabled │ 0.3.0 │ … │ entrypoint
hermes plugins doctor <plugdir> → registrations: 15 tool(s), 0 hook(s)
gateway.log                     → Connecting to aimail... / ✓ aimail connected / Gateway running with 1 platform(s)
L2 hermes s1 s2 s3 s4 neg       → PASS=14 RED=0 GAP=0 SKIP_LEGACY=0   （无 patch）
L2 hermes s5                    → PASS=2                              （无 patch）
```

- **官方机制本来就在**（三处，缺一不可，宿主全都有）：
  1. `hermes_cli/plugins_context.py::PluginContext.register_platform()` —— 插件声明平台适配器；
  2. `gateway/platform_registry.py::platform_registry` —— 平台的运行时注册表；
  3. `gateway/config.py::Platform._missing_()` —— 枚举解析时查注册表（"runtime-registered plugin adapters"），
     并且宿主自身的 plugin 文档把 **Pip plugins（`hermes_agent.plugins` 组）** 列为一级机制。
- **真实原因**：旧夹具把插件装成**目录插件**（复制 `plugin.yaml` + `__init__.py` 到
  `$HERMES_HOME/plugins/aimail`），它既不是用户的安装方式，又叠加了当时我们自己的两处宿主 API 误用
  （`BasePlatformAdapter.__init__(config, platform)`、`_mark_connected(*, listener_base)`）。
  症状看起来像"宿主不认插件平台"，实际是**夹具造出来的**。
- **处置**：删除 `lib/hermes-host-patch.py`；hermes 夹具改为**只 pip 装 wheel + 宿主 entry point 发现**，
  自检改为 `plugins doctor <site-packages 里的插件目录>` + `plugins list --user`（不再伪造目录插件）。
- **对发版的意义**：G2「无 host 源码 patch」现在**产品侧与门禁侧同时成立**；同时门禁第一次真正测到了
  README 承诺的安装面（`pip install` + entry point）。
- 复核点（Q9，新增）：`grep -r "hermes-host-patch" tests/` 应为空；任选一轮 L2 的 `plugin_list` 应显示
  `entrypoint`，`host.log` 应有 `✓ aimail connected`。

---

## 6. 未验证 / 已知缺口（**请质检据此判断能否发版**）

按严重度排序，全部为开发侧主动声明，非待质检发现。

| # | 缺口 | 影响 | 开发侧说明 |
|---|---|---|---|
| **G-1** | **C8 "回复我们的回复"缺宿主侧实机证据** | 契约 C8 的一半只有单测 + runtime 级用例（同一 `chat_id`），无 `s6` 夹具阶段 | 需要一个能拿到我方出站 Message-ID 的夹具步骤（先让 agent 发一封，再注入对它带回信的邮件）。建议作为 0.3.0 **发布后**补测项，或由质检决定是否阻断 |
| **G-2** | legacy 未物理删除 | `§6.8` 原计划删除 `register_profiles.py`、下线 `install.py`/`sdk_ops.py` 写操作、下线 `aimail_hermes.py` replay 三函数；实际改为**移出 wheel + 显式开关可达** | 删除会牵动 `install.py` 的 legacy 命令与 `hosts/registry.py` 的 `register_module` 数据，且入站路径已全走 `v2/*`。**请质检裁定是否可接受** |
| **G-3** | PyPI 上传未执行 | 发布物只到 wheel，未进索引 | `scripts/` 内**没有** pysdk 发布脚本（只有 `publish-npm.sh` 给 tssdk）；发布方式/账号/凭据需 owner 决定 |
| **G-4** | `aimail-agent` console script 仍在 wheel | G1"无 SDK 自带 CLI"的字面判定 | 它是 agent 运行时入口（scenario 1），不是安装 CLI；门禁全程只用 `pip install` + entry point。请质检确认口径 |
| **G-5** | 敏感信息（C11） | **无机器断言**：门禁 S1 只断言 binding `has_key=true`（密钥落 0600 文件），不检查日志卫生；我本轮是人工翻了 `host.log`/`turn_*.log` 未见 raw key / 激活 code 原文 | 建议质检把"日志不含密钥/code"做成可执行断言（本轮**未做**）。抽查命令：`grep -iE "raw_key|addr-[0-9a-f]{4}-" /tmp/aimail-l2-*/<host>/host.log` |
| **G-6** | 单测全部基于 fake host | 真实宿主行为由 L2 覆盖 | hermes/deerflow 的宿主对象在单测里是重建的（仓库不可导入真宿主），请以 L2 为真实行为依据 |
| **G-7** | pip 安装路径下 `plugin.yaml` 的**元数据未被宿主采用** | `hermes plugins show aimail` 显示的是**合成 manifest**：description 取自包摘要、"Runs in: … portable plugin (no Python)"，我们声明的 `kind: platform` 与 `config_schema`（`AIMAIL_PULL*`）**都不出现** | 功能不受影响（平台仍注册、`Platform("aimail")` 仍解析、拉取开关一直是从**环境变量**读的，与 tssdk 同名）；但"声明的配置面"与"实际读取方式"不一致，建议 0.3.1 二选一：删掉 `config_schema`，或改为经宿主 config API 读取。**请质检裁定是否需要先修** |

---

## 7. 请质检执行的复验清单

建议顺序：**先审 diff（Q1–Q4）→ 再跑三宿主（Q5）→ 最后裁定 G-1/G-2（Q6–Q7）**。

- [ ] **Q1 安装面**：`pip install /tmp/wh030/agent_aimail-0.3.0-py3-none-any.whl` 到干净 venv，确认
      `aimail.__version__ == "0.3.0"`、`from aimail.v2 import outbound, thread, binding` 可用、
      `import aimail.hermes.patch_webhook` → `ModuleNotFoundError`、entry points 含
      `hermes_agent.plugins` 与 `deerflow.extensions`。
- [ ] **Q2 API 面变更**：确认 `aimail_tools._deliver_batches` 的三种结果语义（accepted ⇒ ack；
      `skip` ⇒ 不 ack 不报错；raise ⇒ 不 ack 记 error）与 tssdk `deliverBatches` 逐条一致。
- [ ] **Q3 门禁用例自身**：逐条审 §2.4 四个夹具改动，判断是否有**放宽判定**（开发侧自评"否"）。
- [ ] **Q4 打包守护**：删一行 force-include 跑 `test_wheel_manifest.py` 应红；恢复后应绿。
- [ ] **Q5 三宿主实跑**：按 §4.2 跑 hermes / deerflow（含 s5 单轮）/ dsh 回归，比对本轮数字
      （14 / 15+2 / 16）。关注 `inject.log` 是否同时出现 `emails=1` 与 `acked_after_poll=…`。
- [ ] **Q6 G-1 裁定**：C8 宿主侧无实机证据，是否阻断 0.3.0？
- [ ] **Q7 G-2 裁定**：legacy "移出 wheel 但不删文件"是否满足 `§6.8`/§13 最后一条？
- [ ] **Q8 凭据卫生**：随机抽 2–3 个 `host.log`/`turn_*.log`，确认无 key、无 activation code 原文。
- [ ] **Q9 无宿主 patch（新增，2026-11-24）**：`grep -r "hermes-host-patch" tests/ docs/` 只应命中历史记录文档；
      任选一轮 hermes L2，`plugin_list` 应显示 `entrypoint`、`host.log` 应有 `✓ aimail connected`、
      `plugin_doctor` 应为 `registrations: 15 tool(s)`。若镜像里存在 `gateway/config.py` 的
      `# aimail-host-patch` 标记说明有人在打 patch —— 门禁不该有。

---

## 8. 给质检的发版决策输入（不预设立场）

### 8.1 发布物

| 项 | 现状 |
|---|---|
| 版本 | `0.3.0`（`pyproject.toml` + `pysdk/__init__.py` 双处一致） |
| 产物 | `agent_aimail-0.3.0-py3-none-any.whl`（本地构建；本轮最后一份在 `/tmp/wh030/`，重建：`PIP_NO_BUILD_ISOLATION=1 python3 -m pip wheel . -w <dir> --no-deps`） |
| 内容校验 | `v2/` 7 模块 + 两插件 + 两 entry point 齐全；patch 时代 5 模块不在包内 |
| 变更说明 | `pysdk/README.md`（PyPI 长描述）"0.3.0 — breaking" 段 |

### 8.2 breaking 面（发版说明必须覆盖）

1. push/webhook 入站**移除**：无入站监听、无 `webhook_*` binding 字段、不 patch 宿主 webhook。
2. `aimail install` **退役**：安装 = `pip install` + 宿主插件机制；patch 时代模块不再随包发布。
3. 激活**仅 code**：绑定携带 `agent_system_id`/`workspace_path`；单地址宿主拒绝第二地址。
4. ack 语义：只在宿主接受后 ack；paused/unknown ⇒ `skip`（不 ack、不算错）。
5. 回复追溯显式化：`send_mail` 记录出站 Message-ID alias（此前回复我们的回复会开新 session）。

### 8.3 依赖与兼容

| 项 | 要求 |
|---|---|
| gateway | ≥ **v1.0.15**（聚合 `/stream`）；更老版本自动回落 long-pull |
| Python | ≥ 3.9（宿主自身解释器；不依赖 vendored 路径） |
| tssdk | 无需同步发版；本轮 ts 回归 PASS=16 未变红 |

### 8.4 回滚

- legacy 套件默认 `SKIP_LEGACY`（门禁输出可见），设 `AIMAIL_LEGACY=1` 可显式执行 —— 这是**测试侧**回滚开关。
- 产品侧回滚 = 退回 0.2.9 wheel（但 0.2.9 依赖被移除的 push/patch 面，需连宿主一起退）；请质检在发版计划中明确"回滚可用性"口径。

### 8.5 需要质检决定的三个问题

1. **G-1（C8 宿主侧证据）是否阻断 0.3.0**？若不阻断，建议把 `s6` 阶段列为 0.3.1 的强制项。
2. **G-2（legacy 未物理删除）是否满足 §6.8**？若不满足，开发侧可再做一轮删除，但会触及 `install.py`/`hosts/registry.py`。
3. **发布通道**：PyPI 上传由谁执行、用何凭据；是否需要在 CI 增加 `pysdk` 发布 job（当前只有 `publish-npm.sh`）。

---

## 9. 阅读顺序（给质检的索引）

1. 本文 §0/§6/§7 —— 结论、缺口、复验清单
2. `docs/pysdk-v2-sse-refactor-plan.md` **§15**（15.1 E 轨道 … 15.11 无 patch 更正）—— 每个缺陷/误判的定位过程与证据
3. `tests/host-acceptance/v2/GATE.md` —— 门禁定义（s1..s5/neg 各自断言什么、journey 拆分规则）
4. `docs/aimail-host-sse-contract-v2.md` —— 契约原文（C1–C13 的出处）
5. `pysdk/README.md` —— 对外安装与 breaking 说明

---

## 10. 附：本轮对话中曾出现的误判（供质检评估开发可信度）

开发侧坦白记录，避免质检重走弯路：

| 误判 | 事实 | 代价 |
|---|---|---|
| 把 `test_v2_contract_surface.py` 的红项长期当作"兄弟仓无关" | 它阻断 L0，是发布阻断项 | 数轮重复报告"唯一红项无关"，直到 P6 才修正 |
| 曾认为 deerflow bind turn 失败是"模型没调工具" | 实为 401 无凭据 | 一轮 |
| 曾用 `pkill -f uvicorn…` 停网关 | 匹配到夹具自身命令行，把自己杀掉、日志静默中断 | 两轮 |
| 曾在 `docker -lc '…'` 体内写撇号（`host's`） | 截断外层单引号，`unexpected EOF` | 一轮 |
| **以为宿主不支持插件平台，打了宿主 patch** | 官方机制本来就在（`register_platform` + `platform_registry` + `Platform._missing_`），旧夹具用目录插件造出了假症状 | 一整轮 + 一份 patch 脚本，直到 2026-11-24 被推翻 |

这些已写进夹具注释与 §15，作为后续改夹具的人的护栏。
