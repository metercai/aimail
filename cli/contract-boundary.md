# AIMail CLI与SDK的责任边界与调用契约（**v1.0 · 已生效**）

**本文件为最高规则（owner 于 2026-10-06 签字生效）**；与代码冲突以本文件为准，并须同步修正代码或本文件。取证：aimail@main（2026-10-06，逐条 file:line）。

## 1 两域范围与硬边界

**CLI（`cli/`）**
- 目标形态：**纯 rust 单二进制**（平台静态知识 = 探测模块常量 + 契约常量 + 宿主自报；**无跨平台数据文件**，见 §5）；不含 python；不产生 `bin/aimail-src`、`bin/mcp`。
- 职责：安装 / 检测 / 接线 / 自检 / 维护；**只调用，不实现**平台适配与 SDK 逻辑。
- 命令面（**16**，按 `aimail --help` 实测，2026-10-11 复核）：address · bridge · check · domain · install · persona · ping · **platform** · prompt · renew · repair · reset · stats · uninstall · version · welcome。（`mod.rs`、`stub.rs`、`report.rs`（用户面输出助手 `ok/warn/fail`）为内部文件，非命令；2026-10-11 订正：旧表把内部助手 `report` 误列为命令、且缺 `platform`）
- **bridge（硬边界）**：`bridge restart` 只能由 CLI 实施；**bridge 的实施与维护全在 CLI**。SDK 侧**代码级零桥符号**（棘轮 `tests/contract/check-zero-bridge.py`，L0 生效）；但 webhook 语义可**按模式取值**——SDK 依 CLI 给出的 `webhook_host` **三态**决定 `webhook_url`：① **键缺席** = 直连 push（注册为 agent 本机入站端点，端口由入站 up 时确认，见 §4.2 入站 up 反馈）；② **空串** = 桥 pull（注册为空、桥长轮询取）；③ **URL 值** = 桥 push（注册为 BRIDGE_HOST URL，`host:port` 无 path）。**单 agent pull 是绑定级自包含路径**（绑定 `shared_addr_` 前缀），**不**是系统级 `webhook_host` 态、SDK **不**解析网关的 NULL 参数识别它（`pysdk/deer-flow/manage.py:383`、`:397`）。存在"SDK 反调 CLI 过程中**间接**触发 bridge 子命令"的可能。

**SDK（`pysdk/`=Python、`tssdk/`=TypeScript 真源；发布 PyPI `aimailsdk` + 5 个 npm 包）**
- 职责：运行时**实现**层——命名 / 注册 / 改名 / 绑定内容 / 入站实现 / 收发 / webhook / 预处理 / 工具。
- 按平台各自成包，版本号各自独立，**由该 agent 宿主自己的安装环节装入**；不由 CLI 提供。不写系统级配置；不感知 bridge。

**装配归属（由平台宿主能力决定，与 SDK 语言无关）**
- 能自装配（node/host：pi、dsh、openclaw）：宿主包管理器装入扩展，扩展自带注册器 ⇒ 用 `node_entry`（独立可执行入口 `<pkg>/dist/register-cli.js`），**无需** SDK 侧装配函数。
- 不能自装配（python 宿主：hermes、deerflow）：宿主无插件钩子 ⇒ 由 `sdk_install`（调 SDK 侧安装函数）或等价 `spawn` 承担。

**归属的落点（2026-10-11 补证）**
- node 宿主的装配实现**只在该平台自己的包内**：装配逻辑是共享的（`mail-core` 的装配步），宿主布局数据由该包提供（dsh：`tssdk/packages/dsh-aimail/src/register-cli.ts`）。
- `pysdk/` 只装配 python 宿主（hermes/deerflow）；**不得再出现 node 宿主的装配入口或载荷**（棘轮：`tests/sdk/test_sdk_install_step_contract.py`）。

## 2 写入物边界与 CLI 读取范围

**写入（硬边界）**

本表只列**跨域不许写的硬边界两个**；`systems/<sid>/` 下其余落点（如 `webhook_host`）由归属方自持，CLI 读写皆允许（`cli/src/core/bridge_deploy.rs:861`、`:902`）。
| 文件 | 维护者 | 读者 |
|---|---|---|
| `~/.aimail/systems/<sid>/aimail_gateway.json` | **CLI 唯一写**（setup.rs:181/565） | SDK 只读（deer-flow/manage.py:192） |
| per-agent `agentmail.json`（绑定文件） | **SDK 唯一写**（aimail_base.py:455；薄入口 :485/:496/:505） | CLI 只读；**CLI 禁写**（aimail_base.py:477-484） |

**CLI 的读取范围（完备性前提）**：读系统级 `aimail_gateway.json`（gateway_url / admin_key / system_id / domain / system_name / manager_address / runtime）；读 per-agent `agentmail.json`（webhook_url / api_key / persona / prompt_rules / manager_address，**只读**）；网关态由 CLI 自持 HMAC 客户端**直查**（证据 `welcome.rs:63-106`、`ping.rs:129-180`）。⇒ `check`/`repair`/`stats` 的取值**无需** SDK 接口。

## 3 系统对接流程

**阶段1 参数领取（两条路径、两个不同 gateway 二进制）**
- 路径A **高级版 `aimail-advanced`**：领取页面（`/apply/address`·`/apply/system`·`/apply/dedicated`）+ `POST /api/v1/public/email-verify/{send,check}` ⇒ **激活码下发到申请人邮箱**（唯一路径，禁 API 造码）；`renew` 只对高级版；码四类 = 地址级 / 系统级共享域 / 系统级独享域 / 独立网关 license。
- 路径B **基础版 `aimail-gateway`（自建）**：启动打印的 `<sid>.system.key` 作 **admin-key**（无领取页、无申领链）。

**阶段2 CLI自举（目标形态）**
① 用户**先设环境变量**（邮件里的参数）② 运行远端的自举程序，在自举程序驱动下完成环境初始化：
- 建根目录 `~/.aimail` ，如果该目录硬盘空间大小<1GB，提示保存邮件的预留空间不足，但继续。
- **探测用户系统的硬件环境，下发正确版本二进制** 放到正确位置（`~/.aimail/bin/aimail`）
- 网关锁定： direct-vs-bridge 研判 / bridge 复制到位并配置启动
- 下一步操作指引的提示
> 现状（2026-10 已改造）：`bootstrap.sh` 零 python 依赖、按 OS/arch 从 GitHub Release 取单一 Rust 二进制；机器级网关锁定/桥部署归 `aimail install`（本地网关→direct、远程→bridge）。

**阶段3 SDK安装（一步，两入口：`aimail install --home <agent根>` / 宿主插件自装）**
- 两层，**时序硬约束：先激活、后适配**，由 CLI 流程控制：① 系统级激活（检测判定agent环境的解析器-包管理-SDK根/产出 `system_id`/域/系统级 key ⇒ 写 `aimail_gateway.json`；install.rs:824）② 平台适配（CLI 按注册表分派步骤；install.rs:944。步骤 `when: cfg_complete` 即该时序依赖的体现）。
- **两条入口 = 同一子流程（步骤集相同），差异仅两点**：触发者不同（CLI / 宿主插件管理器）；系统级激活承担者不同（CLI install 内 / 插件路径由 SDK 反调 `install --system-only`）。
- **SDK 的安装由宿主包管理器执行、由 CLI 控制时机**：`pip install aimailsdk` / `dsh plugin add dsh-aimail` / `pi install npm:pi-aimail` / `openclaw plugins install …`；SDK **不需要 install 触点**。

**阶段4 闭环运行**：`aimail welcome` 闭环检测覆盖所有路径，用户可感知的在运行。

**阶段5 日常维护**：运行时在 SDK；CLI 提供维护子命令。

## 4 双向接口

### 4.1 CLI → SDK

**(1) 标准通用（每个 transport 都必须提供、不可替代、收敛后保留）= 3 个动作 op**
| op | 语义 | 不可替代的原因 |
|---|---|---|
| `assemble` | 命名（规则）→ 构建注册器调用 → 执行平台注册器（transport 分派）→ 必要时改名 → 写绑定 | 命名/注册规则在 SDK；CLI **无绑定写权限** ⇒ 不调即无法注册 |
| `update` | 受控字段更新：`manager_address` · `prompt_rules` · **`persona`** · `rename` · `webhook-secret` · `backfill` · **`repair`**（缺口判定与修复） | 绑定内容判定与落盘在 SDK ⇒ `address set-manager` / `address set-name` / `prompt add\|rm` / `persona` / `repair` 均依赖它 |
| `teardown` | 注销（`deregister_agent_email`）· 白名单清理（`cleanup_system_whitelists`）· 绑定回填（`backfill_binding`） | 同上，缺则卸载不干净 |
**取值类不在其列**：数据类由 CLI 依 §2 **直读**；规则类（命名/webhook 推导）由 SDK **在动作内消化**，不外露中间值。三 op 的 ABI 一致：入参为单个 JSON；返回**平铺 payload + 恒有 `ok`/`sdk_version`**（门在外层加信封），错误种类仅 `usage`/`call`，输出为**单行 JSON**。规则判定（含 repair 的缺口判定与修复）**归 SDK**，CLI 只触发。

**(1b) 只读判定 op（owner 2026-10-10 (a) 裁定增列）**
| op | 语义 | 边界 |
|---|---|---|
| `prompt-test` | CLI 传 `system_id` + 规则 + 文本；SDK 返 `name_ok` / 字段内或·字段间且的匹配结果 | **不落盘**；判定在 SDK，CLI 只传参、**不复刻谓词**（`cli/src/cmd/prompt.rs:8`、`:49`、`:86`） |

**(2) 因 py / ts 环境差异**定向保留**
- 传输形态（两种 transport 承载**同一套语义**）：
  - **python 侧**：经门 `<宿主解释器> -m aimail.sdk_ops <op>`。其中
    · `<op>` 动作类 **= §4.1(1) 的 3 个**：`assemble` / `update` / `teardown`；只读类 **= 1 个**：`prompt-test`（§4.1(1b)）；另有 6 个**工具/取值 op**（`version` / `iter_bindings` / `ensure_webhook_secret` / `resolve_register_webhook_url` / `register_agent_email` / `backfill_binding`）—— 工具/取值类不属标准动作 op（§4.1(1) 已界定）。门内 op 全集见 `pysdk/sdk_ops.py:638-651`；
    · `<宿主解释器>` **必须是 agent 宿主自己配置的环境**（如 hermes / deerflow 的 venv），**不是** CLI 自带的 python ✗ —— CLI 只负责**定位**它（`$AIMAIL_PYTHON` → `~/.aimail/bin` 之外的宿主 venv 探测 → PATH `python3`；定位方式属本契约的传输细节）。宿主自装 SDK 包时也必须装进**同一个**解释器。
    · **注册与装配同样经门（`assemble`）**：CLI **不得**直调宿主模块/脚本（hermes `_auto_register_email`、deerflow `manage.py` 之类调用一律移入门内；owner 2026-10-11 裁定）。
  - **node 侧**：**一律**经平台注册器（`node_entry`，独立可执行入口 `<pkg>/dist/register-cli.js`，**不加载宿主插件** ⇒ 生命周期诊断不得进 stdout），同样只承载上述语义（owner 2026-10-11 裁定：`host_command` 形态废弃）。
  两者的**语义与 JSON 形状一致**，仅传输不同。
- python 宿主装配：hermes / deerflow 暂以 `sdk_install`（调 SDK 侧安装函数）保留 —— 属"宿主无插件钩子"的定向保留；宿主提供安装钩子后可去。
- 平台扩展安装：由**宿主包管理器**执行（CLI 以 `spawn` 触发）⇒ **不属 SDK 接口**。
- 宿主解释器定位契约（**优先级语义**；单真源 `cli/src/core/sdkroot.rs::probe_in_domain`）：`$AIMAIL_PYTHON` → 静态候选 → **宿主树内嵌套 venv 探测** → **PROBE_SH** → **活进程探测** → PATH `python3`（2026-10-11 补证；细节以该函数为唯一真源）。
- CLI 定位契约：`$AIMAIL_BIN` → `~/.aimail/bin/aimail` → PATH（aimail_base.py:1708-1719）。

### 4.2 SDK → CLI（reverse-call ABI）

**(1) 标准通用**
| 场景 | CLI 命令 | 证据 |
|---|---|---|
| 系统级激活 | `aimail install --system-only`（L1-only） | gateway_api.py:90 · mail-service.ts:123 · aimail_base.py:2612-2658 · hermes/aimail_hermes.py:422 |
| 卸载 | `aimail uninstall` | pysdk/install.py:540 |
| 入站 up / down | `aimail address -a <addr> --inbound-live --inbound-url <确认入站URL>` / `--inbound-down` | dsh-aimail/inbound.ts:361/:394 · register-cli.ts:142 |
| **预留**：agent 新增 / 删除 | 属 `address` 子命令一部分（当前 SDK 无入口） | owner 裁决 2026-10-06 |

**(2) py / ts 差异**：**无** —— 两环境调同一 CLI 命令；差异仅在"如何定位 CLI"（见 §4.1(2) 末条）。

### 4.3 入站端点 up 反馈（端口确定性闭环，owner 2026-10-13）

- **端口确定性落在入站 up**：install 时刻只知"最佳已知"入站口（AGENT_HOST 锁定 / 平台默认）；入站 server 实际 listen 后端口才**确定可用**。入站 up 回调**必须携带确认入站 URL**（`--inbound-url <http(s)://host:port/path>`，= 监听器实际服务的 URL），CLI 据态把确定 URL 写回路由层（覆盖 install 的预注册值）。
- **多 agent 循环**：一个入站端点承载多个 agent address 时，SDK **逐地址**回调 `address`，确保每个地址都有路由/注册（`inbound-notify.ts::notifyInboundForSystem`）。
- **按态写回**：桥 push ⇒ 外部桥 route（admin `sync_route` action=live，target=确认 URL）；桥 pull ⇒ 本机桥 route（同 API，127.0.0.1）；直连 push ⇒ **复用注册（重发地址）**幂等 upsert 网关 per-address webhook_url=确认 URL。
- **down 对称**：`--inbound-down` ⇒ 桥 route withdraw / 网关注册撤回。
- **竞态安全**：桥 pull 无 route 不 ack（下轮重试）⇒ route 补上前信停 pending，不丢。
- **监听器必须服务在确认 URL 上**：确认 URL 为外网 IP（AGENT_HOST）时监听绑该 IP / 0.0.0.0（**非仅回环**）；端口被占 ⇒ agent 启动失败（锁定端口语义）。

## 5 CLI 检测面（2026-10-11 增列）

平台静态知识**无跨平台数据文件**（`cli/platforms.json` 已退役），按来源三分落位：

| 类别 | 内容 | 落点 |
|---|---|---|
| 可推导 | `pointer.kind`（由容器形状推出）、agents 容器形状与多 profile 名字、op 入口模板（两类型各一条常量）、插件 `register-cli.js` 路径（装后 glob `**/node_modules/*-aimail/dist/register-cli.js` —— 入口路径与 ABI 三包统一，owner 2026-10-11：不存在已装旧版 ⇒ 一律 `dist/`） | CLI 探测模块 |
| 常量 | home 目录名、宿主特征标记、宿主包管理器安装命令、**宿主布局模式**（插件入口在平台 home 内的相对路径模式）、自检探针词表 | CLI 探测模块（单份） |
| 宿主自报 / 内部 | 固定 agent 名、别名（`default` / `main`）、平台私有注册参数（如 dsh `--preset`） | SDK 内部 |

- 探测入口：`detect_static(home_dir, explicit_platform) -> StaticInfo{platform, transport, agent_keys, pointer, entry, installed_version}`；**`home_dir` 为外部输入**。
- 优先级：显式 `--platform`（校验，不静默回落）→ 已装组件（包名 / 模块）→ 宿主特征标记（未装态兜底）→ `unknown` ⇒ **响亮失败**并提示 `--platform`。
- 未装态**只允许**上述两法（标记探针 + 显式 `--platform`），禁任何隐式猜测。
- 导出面：`aimail platform spec --json`（供门禁与夹具断言；单一真源仍是探测模块内的常量）。
- 边界不变：探测属 §1 的"检测/自检"职责；CLI **仍不实现**平台适配与 SDK 逻辑（§1）。


