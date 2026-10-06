# AIMail 责任边界与调用契约（**v1.0 · 已生效**）

**本文件为最高规则（owner 于 2026-10-06 签字生效）**；与代码冲突以本文件为准，并须同步修正代码或本文件。取证：aimail@main（2026-10-06，逐条 file:line）。

## 1 两域范围与硬边界

**CLI（`cli/`）**
- 目标形态：**纯 rust 单二进制 + 配置数据/资源**；不含 python；不产生 `bin/aimail-src`、`bin/mcp`。
- 职责：安装 / 接线 / 自检 / 维护；**只调用，不实现**平台适配与 SDK 逻辑。
- 命令面（**16**，按 `cli/src/cmd/*.rs` 实测）：address · bridge · check · domain · install · persona · ping · prompt · renew · repair · report · reset · stats · uninstall · version · welcome。（`mod.rs`、`stub.rs` 为内部文件，非命令）
- **bridge（硬边界）**：`bridge restart` 只能由 CLI 实施；**bridge 维护全在 CLI，SDK 不可见、不感知**。存在"SDK 反调 CLI 过程中**间接**触发 bridge 子命令"的可能，**该间接性在 SDK 内不被感知**。

**SDK（`pysdk/`=Python、`tssdk/`=TypeScript 真源；发布 PyPI `aimailsdk` + 5 个 npm 包）**
- 职责：运行时**实现**层——命名 / 注册 / 改名 / 绑定内容 / 入站实现 / 收发 / webhook / 预处理 / 工具。
- 按平台各自成包，**由该 agent 宿主自己的安装环节装入**；不由 CLI 提供。不写系统级配置；不感知 bridge。

**装配归属（由平台宿主能力决定，与 SDK 语言无关）**
- 能自装配（node/host：pi、dsh、openclaw）：宿主包管理器装入扩展，扩展自带注册器 ⇒ 用 `node_entry` / `host_command`，**无需** SDK 侧装配函数。
- 不能自装配（python 宿主：hermes、deerflow）：宿主无插件钩子 ⇒ 由 `sdk_install`（调 SDK 侧安装函数）或等价 `spawn` 承担。
- ⇒ **dsh 修正后与 pi 同形**：`node_entry` + 无 `sdk_install`。

## 2 写入物边界与 CLI 读取范围

**写入（硬边界）**
| 文件 | 维护者 | 读者 |
|---|---|---|
| `~/.aimail/systems/<sid>/aimail_gateway.json` | **CLI 唯一写**（setup.rs:181/565） | SDK 只读（deer-flow/manage.py:192） |
| per-agent `agentmail.json`（绑定文件） | **SDK 唯一写**（aimail_base.py:455；薄入口 :485/:496/:505） | CLI 只读；**CLI 禁写**（aimail_base.py:477-484） |

**CLI 的读取范围（完备性前提）**：读系统级 `aimail_gateway.json`（gateway_url / admin_key / system_id / domain / system_name / manager_address / runtime）；读 per-agent `agentmail.json`（webhook_url / api_key / persona / prompt_rules / manager_address，**只读**）；网关态由 CLI 自持 HMAC 客户端**直查**（证据 `welcome.rs:63-106`、`ping.rs:129-180`）。⇒ `check`/`repair`/`stats` 的取值**无需** SDK 接口。

## 3 系统级流程

**阶段1 领取（两条路径、两个不同 gateway 二进制）**
- 路径A **高级版 `aimail-advanced`**：领取页面（`/apply/address`·`/apply/system`·`/apply/dedicated`）+ `POST /api/v1/public/email-verify/{send,check}` ⇒ **激活码下发到申请人邮箱**（唯一路径，禁 API 造码）；`renew` 只对高级版；码四类 = 地址级 / 系统级共享域 / 系统级独享域 / 独立网关 license。
- 路径B **基础版 `aimail-gateway`（自建）**：启动打印的 `<sid>.system.key` 作 **admin-key**（无领取页、无申领链）。

**阶段2 自举（目标形态）**
① 用户**先设环境变量**（邮件参数）② 远端程序建根目录 + **探测系统环境下发正确版本二进制** ③ 放到正确位置（`~/.aimail/bin/aimail`）并可运行 ④ 环境初始化（machine_init = **sh**：网关锁定 / direct-vs-bridge 研判 / bridge 骨架；下一步指引 = **sh**）⇒ 不产生 python 依赖。
现状差异（待改造）：`bootstrap.sh:7-25` 需 python3≥3.10、拉源码快照、`machine_init.py`。

**阶段3 安装（一步，两入口：`aimail install --home <agent根>` / 宿主插件自装）**
- 两层，**时序硬约束：先激活、后适配**，由 CLI 流程控制：① 系统级激活（产出 `system_id`/域/系统级 key ⇒ 写 `aimail_gateway.json`；install.rs:824）② 平台适配（CLI 按注册表分派步骤；install.rs:944。步骤 `when: cfg_complete` 即该时序依赖的体现）。
- **两条入口 = 同一子流程（步骤集相同），差异仅两点**：触发者不同（CLI / 宿主插件管理器）；系统级激活承担者不同（CLI install 内 / 插件路径由 SDK 反调 `install --system-only`）。
- **SDK 的安装由宿主包管理器执行、由 CLI 控制时机**：`pip install aimailsdk` / `dsh plugin add dsh-aimail` / `pi install npm:pi-aimail` / `openclaw plugins install …`；SDK **不需要 install 触点**。

**阶段4 闭环**：welcome / check / stats。**阶段5 日常**：运行时在 SDK；CLI 只维护。

## 4 双向接口

### 4.1 CLI → SDK

**(1) 标准通用（每个 transport 都必须提供、不可替代、收敛后保留）= 3 个动作 op**
| op | 语义 | 不可替代的原因 |
|---|---|---|
| `assemble` | 命名（规则）→ 构建注册器调用 → 执行平台注册器（transport 分派）→ 必要时改名 → 写绑定 | 命名/注册规则在 SDK；CLI **无绑定写权限** ⇒ 不调即无法注册 |
| `update` | 受控字段更新：`manager_address` · `prompt_rules` · **`persona`** · `rename` · `webhook-secret` · `backfill` · **`repair`**（缺口判定与修复） | 绑定内容判定与落盘在 SDK ⇒ `address set-manager` / `address set-name` / `prompt add\|rm` / `persona` / `repair` 均依赖它 |
| `teardown` | 注销（`deregister_agent_email`）· 白名单清理（`cleanup_system_whitelists`）· 绑定回填（`backfill_binding`） | 同上，缺则卸载不干净 |
**取值类不在其列**：数据类由 CLI 依 §2 **直读**；规则类（命名/webhook 推导）由 SDK **在动作内消化**，不外露中间值。三 op 的 ABI 一致：入参为单个 JSON；返回**平铺 payload + 恒有 `ok`/`sdk_version`**（门在外层加信封），错误种类仅 `usage`/`call`，输出为**单行 JSON**。规则判定（含 repair 的缺口判定与修复）**归 SDK**，CLI 只触发。

**(2) 因 py / ts 环境差异**定向保留**
- 传输形态（两种 transport 承载**同一套语义**）：
  - **python 侧**：经门 `<宿主解释器> -m aimail.sdk_ops <op>`。其中
    · `<op>` **= §4.1(1) 的恰好 3 个**：`assemble` / `update` / `teardown`（**已收敛**：门内即这 3 个，旧 6 个 op 已移除）；
    · `<宿主解释器>` **必须是 agent 宿主自己配置的环境**（如 hermes / deerflow 的 venv），**不是** CLI 自带的 python ✗ —— CLI 只负责**定位**它（`$AIMAIL_PYTHON` → `~/.aimail/bin` 之外的宿主 venv 探测 → PATH `python3`；定位方式属本契约的传输细节）。宿主自装 SDK 包时也必须装进**同一个**解释器。
  - **node 侧**：经平台注册器（`node_entry`）或宿主命令（`host_command`），同样只承载上述语义。
  两者的**语义与 JSON 形状一致**，仅传输不同。
- python 宿主装配：hermes / deerflow 暂以 `sdk_install`（调 SDK 侧安装函数）保留 —— 属"宿主无插件钩子"的定向保留；宿主提供安装钩子后可去。
- 平台扩展安装：由**宿主包管理器**执行（CLI 以 `spawn` 触发）⇒ **不属 SDK 接口**。
- CLI 定位契约：`$AIMAIL_BIN` → `~/.aimail/bin/aimail` → PATH（aimail_base.py:1708-1719）。

**(3) 现状 → 终局（收敛路径）**
| 现状（约 11 处引用） | 终局 |
|---|---|
| 门 op ×6：`version` `iter_bindings` `ensure_webhook_secret` `resolve_register_webhook_url` `register_agent_email` `backfill_binding` | 并入 `assemble`/`update`/`teardown`；bindings/version 改 CLI **直读** |
| 薄入口 ×5：`plan_address_name` `rename_address` `set_agent_manager` `update_binding` `deregister_agent_email`(+`cleanup_system_whitelists`) | 并入动作 op；`plan_address_name`/`rename_address` **不再外露** |
| 各平台注册器 transport | 保留，但**由 SDK 内部调用**（CLI 不拼 argv） |
**步骤（四步边界序）**：① SDK 侧新增三 op（内部整合命名/argv/改名/绑定/webhook 推导）② SDK 发版 + L2 ③ CLI 侧改调用点（11 → 3）④ 契约更新 ⑤ rust 复核。**验收判据**：CLI 侧引用 SDK 的入口数 = **3**（平台扩展安装仍走宿主包管理器）。

### 4.2 SDK → CLI（reverse-call ABI）

**(1) 标准通用（不可替代：CLI 持系统生命周期与入站控制权；**禁直连网关**）**
| 场景 | CLI 命令 | 证据 |
|---|---|---|
| 系统级激活 | `aimail install --system-only`（L1-only） | gateway_api.py:90 · mail-service.ts:123 · aimail_base.py:2612-2658 · hermes/aimail_hermes.py:422 |
| 卸载 | `aimail uninstall` | pysdk/install.py:540 |
| 入站 up / down | `aimail address -a <addr> --inbound-live` / `--inbound-down` | dsh-aimail/inbound.ts:361/:394 · register-cli.ts:142 |
| **预留**：agent 新增 / 删除 | 属 `address` 子命令一部分（当前 SDK 无入口） | owner 裁决 2026-10-06 |

**(2) py / ts 差异**：**无** —— 两环境调同一 CLI 命令；差异仅在"如何定位 CLI"（见 §4.1(2) 末条）。
**(3) 现状 → 终局**：4 条已实测 + 1 条预留 ⇒ **已基本达终局**；唯一差异 = 实现 agent 新增/删除的 `address` 入口。

## 5 SDK 门禁的两个层次（顺序不可颠倒）与边界锁定

**第一序（主体）：SDK 自身内部流程/逻辑正确性**
- **基线**：以**旧版为基础**，既有 SDK 门禁**已能保证内部流程逻辑正确** ✓（不需重建验证 ✗）。
- **本次唯一硬要求**：新增改动（3 个 op 与内部整合）**不得破坏既有内部流程逻辑** ✓ —— 即既有门禁**继续全绿** ✓。
- 用例处置：既有用例**基本不动** ✓；仅当某用例的目标入口被合并/移除时，**改指向新入口** ✓；只有新增能力才补新用例 ✓。**功能覆盖不减** ✗。

**第二序（附加）：边界固化稳定**
- 边界锁定 = 下列双向棘轮；它是**附加检查层**，**不得以边界断言替代或削弱第一序** ✗。
- 两者**独立报告**：功能门禁红 ⇒ SDK 实现问题；边界棘轮红 ⇒ 越界/接口漂移问题。

**边界锁定（双向棘轮）**
1. **单一版本线（以终为始）**：CLI 只面向**当前** SDK 契约（§4.1(1) 的三个 op）。**不保留旧 CLI 的兼容性** —— 旧实现彻底放弃，不设双写期、不设旧入口保留。
2. **版本握手**：CLI 调用前核对 SDK 版本；SDK 过旧（缺所需 op）⇒ **响亮失败**并给出升级指引（禁止崩溃、禁止静默降级）。
3. **边界锁定（双向棘轮，必须进 SDK 门禁）**：
   - **门暴露面 == {assemble, update, teardown}**（集合相等；多一个 op 即判红）；
   - **SDK 侧不得写**系统级 `aimail_gateway.json`（只读消费），不得承担 §1 中属 CLI 的职责；
   - **SDK 反向调用 CLI 仅限 ABI 白名单**（`install --system-only` / `uninstall` / `address --inbound-live|--inbound-down`）；白名单外调用即判红；
   - **CLI 侧不得写** per-agent `agentmail.json`（§2），不得内联实现平台适配逻辑。
4. **宿主环境前提**：SDK 新版本的 python/node 版本要求不得高于上一版本（`python_requires` / `engines` 显式声明）。

## 6 生效与违界判定
- 生效：owner 签字后置为最高规则（`docs/` 或仓库根 `CONTRACT.md`），并在 `README*.md` / `AGENTS.md` 引用。
- 违界判定（出现即缺陷）：① CLI 写 `agentmail.json`；② SDK 直连网关激活 API（未走 `install --system-only`）；③ SDK 感知/操作 bridge；④ CLI 载荷含 python/SDK，或形成 SDK↔CLI 依赖环。
