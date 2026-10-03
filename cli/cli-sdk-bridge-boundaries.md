# 三边界（CLI / SDK / bridge）+ 契约优先的改造顺序 —— 2026-10-03 修订定稿（交付 `aimail/cli/`，CLI 迭代与 rust 化参考）

> **基线**：skill 侧 `aimail-development-workflow/references/cli-sdk-bridge-boundaries.draft-20260930.md`（462 行，含 §7
> 「2026-09-30 追加」）—— 2026-10-03 逐面复核到现码后**升格为交付稿**落 `aimail/cli/`（本文；权威副本在产品仓）。
> **10-03 复核范围**：产品锚点（子命令面/load_core/机器面）、两配置写权与反调 ABI、四组 grep 断言复跑、门禁拓扑（方案A）、版本态、桥版本；
> 差异以 ✘/◐ 更新并登记 §8；草稿后改动的来源会话未逐一追溯（§10-⑬）。
> 标记：`【增】`= 草稿没有 · `【改】`= 与草稿不同（附草稿行号） · `【裁】`= 裁剪/指针化建议（只登记不执行）。
> 仓别与实况（10-03）：`~/aimail`（产品仓，`main @ 415b17f`，树余 owner 的 `README_zh.md`×2；最新 tag **v0.1.34**）；
> `~/aimail-advanced`（测试仓，`main @ ed30a40`，**树净**）；桥 = `aimail-bridge 0.7.5`（`Cargo.toml:3`）。
> 所有事实带 `文件:行`（**行号一律 10-03 现码**；09-30 草稿行号已整族漂移，见 §8-3 新增 17-20 行）；查不到的写「未确认(查了哪些地方)」。

---

## 1. 三个边界（目标态，用它当评审尺子）

| 组件 | 定位 | 允许的对外面 |
|---|---|---|
| **CLI** (`cli/`) | 宿主侧**一次性命令**（不常驻、不做守护、不管别的服务生命周期） | 平台自己的工具链（pip/npm/平台 CLI）+ 网关 HTTP/SMTP |
| **bridge** | **后台服务**，自守护 | 自己的 pid 文件 + 配置文件 + 四个开关；对 CLI 只提供“生命周期契约” |
| **SDK** (`pysdk/` `tssdk/`) | 平台**进程内**常驻（Python/TS）；工具原生内嵌 | 文件格式 + 协议 |

**CLI 子命令面（2026-10-03 复核；09-30 计数全对、行号整族 +~74）**：
- 人面 = **15 个顶层子命令** ✅ 属实：`sub.add_parser` 现码 `cli/aimail:3462,3507,3518,3531,3536,3543,3547,3554,3563,3569,3577,3584,3591,3611,3653`（含多行 `prompt` `:3611-3613`）
  ；`prompt` 的 `add|list|rm|test|create-file` 是**嵌套** `psp.add_parser`（`cli/aimail:3621,3631,3633,3636,3647`），**不要数成 20**；
  `persona` 仅存指路壳（rc 2）✅（`cli/aimail:3577`）。
- 机器面 = **两种反调 argv 形态**（契约真值）：`install --system-only`（SDK 反调 L1 ABI，单行 JSON）
  与 `install --payload <install|dir|resource|source> [bundle]`；声明与隐藏在 `cli/aimail:3491-3504`（`--system-only` :3491 · `--payload` :3493）。
  ⚠ **计数订正(2026-10-01 实测, 10-03 行号复核)**: `help=argparse.SUPPRESS` 的隐藏参数**共 8 个**（`install` 上 6 个 :3492/:3495/:3500/:3501/:3503/:3504 + 另 2 个 :3601/:3603），**不是 2 个**；**仅其中前两种属反调 ABI**，其余（payload_name/--dest/--source-root/--force 等）是 install 的隐藏开关。
  独立子命令 `payload`/`ensure-system` 已按**裁决 B**（2026-09-23）删除，旧名 = argparse invalid choice **rc 2**
  ✅（`tests/test_install_machine_surface.py:66-71`）；黄金 = `tests/test_install_machine_surface.py`（**10 例** ✅ 属实，
  test 函数在 `:38,44,50,56,66,74,79,85,91,96`）。

三条硬规则：
1. **CLI 不硬依赖 SDK** —— 代码里实为**两种合法形态**（原稿第 1 条已准，本轮把“清单”落出来 → **见 §1.2 B**）：
   - **可选消费**：try/except + 本地回退（**3 处真·可选**：`cli/_common.py:29-34`、`cli/aimail:40-45`、
     `cli/check_status.py:47-52`）；
   - **核心消费**：`load_core()` 之后裸 `import aimail_base`（**`cli/aimail` 内 13 处** ✅ 属实 → §1.2）。
   评审判据：新命令必须落进其一，否则即违规（**可执行断言见 §4-B**）。
2. **SDK 不依赖 CLI**：SDK **不得**以子进程或 import 方式调用 `cli/*.py`（源码相对路径一律违规，见 §4）。
   **唯一合法的子进程方向 = 已安装的 `aimail` 可执行经 L1 反调 ABI**：`aimail install --system-only`
   与 `aimail install --payload …`（**→ §1.3 C 给出两侧 argv/stdout/实现/测试**）。独立子命令 `ensure-system` 已删（旧名 rc 2）。
3. **共享面只有两样：文件格式 + 协议**。跨边界只允许这两样；其余一律算泄漏。
   **已确认清单（新增共享面必须先入清单再动代码）**：
   - 文件格式：`agentmail.json`（含键 `prompt_rules` —— CLI `aimail prompt` 写、SDK 读、**匹配逻辑单真源在 pysdk**）·
     平台指针 `.agentmail` · stats/存储 JSON · 角色文件 `role_prompt/*.md` 三级查找序 ·
     **`aimail_gateway.json`（系统级）→ 完整契约见 §1.1 A**；
   - 协议：v1 API 签名 · `/aimail/inbound` 入站端点 · webhook HMAC · **header `X-AIMail-Prompt`**
     （网关扩展点：SDK 双源消费 = 邮件头 `payload.headers` 优先、HTTP 头兜底，大小写不敏感）·
     **SDK→CLI 反调 ABI 两参数（§1.3 C）**。

---

## 1.1 【增】A — 两份配置文件的契约（写权 / 读权 / 键 / 越界判据）

> 来源：`~/aimail/tests/contract/check-file-ownership.py`（裁决 + 判定器）、`tests/contract/file-ownership-baseline.json`、
> `aimail-sdk-release/references/config-file-ownership-cli-vs-sdk.md`（52 行，本轮并入）、两仓实际读写点。
> 判据原话（owner 2026-09-28）：**系统级 `aimail_gateway.json` ← 只由 CLI 写；per-agent `agentmail.json` ← 只由 SDK 写；反向只许读**
> （`check-file-ownership.py:5-17`）；“谁有权写 = 谁主导，读是信息流，写是权”。

### A-1 `~/.aimail/systems/{sid}/aimail_gateway.json`（系统级环境文件）

| 维度 | 实况（`文件:行`） |
|---|---|
| **写方 = CLI** | `cli/setup_system.py:331-370` `_save_gateway_config()`（调用点 `:435,:575`）· `cli/aimail:1021-1027`（docker 方式二记 `container_home`，经 `_atomic_json_write`）· `cli/aimail:2420-2421` `_set_default_agent_name()`（`default_agent_name`）· `cli/repair.py:478-519` `_repair_gateway_config()` · `cli/deploy_bridge.py:730-736`（`webhook_host`） |
| **写方 = SDK（⚠ 与裁决 A 有张力，本轮新发现）** | `pysdk/aimail_tools.py:528-536` `activate_address_code_persist()` **create-if-absent** 写 `{gateway_url, system_id, domain, scope:"agent"}`（`atomic_write_private`，0600）；`tssdk/packages/mail-core/src/address-code.ts:139-158` 同链 **create-if-absent** 写 `{gateway_url, admin_key:act.raw_key, system_id, domain}`（`fs.writeFile … mode:0o600`）。注释自陈：`pysdk/aimail_tools.py:473-475`、`address-code.ts:107-108` |
| **读方 = SDK** | `pysdk/gateway_api.py:16-31`（`gateway_config_path`/`load_gateway_config`）· `pysdk/aimail_base.py:260-262,270,299` · `pysdk/aimail_base.py:2288-2311,2336`（`_system_home_owned` 反调前的本地归属探测）· `pysdk/deer-flow/aimail_deerflow.py:107-110` · `tssdk/…/config.ts:38-49`（`GATEWAY_CONFIG_NAME`/`gatewayConfigPath`）· `tssdk/…/auto-bind.ts:28,104,163` |
| **读方 = CLI** | `cli/check_status.py:86,891-894` · `cli/repair.py:66,480,781` · `cli/deploy_bridge.py:585,614,721` · `cli/aimail:1021,2917,2998-2999,3180` · `cli/runtime_core.py:175-184,299-312` |
| **关键键位** | CLI 主写：`gateway_url, admin_key, system_id, system_name, save_raw_snapshots` + 条件键 `domain/manager_address/webhook_host/system_home`（`cli/setup_system.py:349-364`，0600 见 `:370`）；CLI 追加：`container_home`（`cli/aimail:1026`）、`default_agent_name`（`cli/aimail:2420`）。SDK 追加（仅缺文件时）：`gateway_url, system_id, domain, scope`（`pysdk/aimail_tools.py:531-536`）/ TS 侧 `gateway_url, admin_key, system_id, domain`（`address-code.ts:148-153`） |
| **越界判据** | 规则 `sdk-writes-gateway-config`：扫描根 `pysdk/`+`tssdk/`、字面量 `("aimail_gateway.json","agentmail-gateway.json")`（`check-file-ownership.py:56,61-62`）；写证据 = 写调用实参含**字面量或污染名**（`:263-281`），`atomic_write_private` 属 helper 首实参即路径位（`:87-89,271-272`）；只读一律不判（`:11-12`）；行内逃生门 `file-ownership-allowed: <理由>` 单独成节打印（`:36-37,102,210`） |

### A-2 `~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.json`（per-agent 绑定文件）

| 维度 | 实况（`文件:行`） |
|---|---|
| **写方 = SDK（100%）** | Python 唯一共享落盘 `pysdk/aimail_base.py:454-474` `save_agent_config()`（tmp 0600 + `tmp.replace`，对齐 TS）；语义化薄入口 `pysdk/aimail_base.py:484-501` `update_binding`/`backfill_binding`、`:504-513+` `rename_binding`（owner 裁决 A，`:477-482`）；注册链 `pysdk/aimail_base.py:2134+` `register_agent_email`；地址码自助链 `pysdk/aimail_tools.py:446-564`（落盘 `:521`）。TS 侧 `tssdk/…/auto-bind.ts:343` `saveBinding()`（0600 原子写）、`address-code.ts:137` |
| **写方 = CLI（只能“触发”，不得自持写调用）** | `cli/aimail:1597,1612`（`prompt add/rm` → `ab.update_binding`）、`cli/aimail:1891`（`address set-manager` → `update_binding`）、`cli/aimail:1932`（`address set-name` → `rename_binding`）、`cli/aimail:2193`（云端改名后本地迁移 → `rename_binding`）、`cli/repair.py:714`（回填 → `backfill_binding`）；**均不再出现绑定文件字面量**（实测 `_py_hits` = 0 命中，见下「门禁实测」） |
| **读方 = CLI** | `cli/aimail:1549,1594,1611`（prompt 规则读改）、`:3240-3241`（uninstall 扫描）、`cli/repair.py:204-207,320-322`（枚举 + 完整性）、`cli/check_status.py`（体检维度） |
| **读方 = SDK** | `pysdk/aimail_base.py:135-168` `read_prompt_rules()`（键 `prompt_rules`）、`iter_agentmail_configs`（`cli/repair.py:207` 经 `_ab` 调用）、`tssdk/…/config.ts:61-79` `loadAgentConfig` |
| **关键键位** | 链同构字段 `agent_id, email, gateway_url, domain, system_id, api_key`（`pysdk/aimail_tools.py:502-509`）+ `webhook_secret`（`:516-518`，本地生成复用不覆盖）+ `expires_at`（`:519-520`）；维护字段 `webhook_url / manager_address / prompt_rules`（`pysdk/aimail_base.py:487-488`、`cli/aimail:1594-1612`）；`prompt_rules` 项结构 `{name, file, enabled, subject/body/sender/recipient}`（`pysdk/aimail_base.py:120-168`）。原稿/CLI 注释称「agentmail.json 9 fields」（`cli/aimail:21-23`）——**9 这个数本轮未逐字段核（未确认）** |
| **越界判据** | 规则 `cli-writes-agent-binding`：根 `cli/`、字面量 `("agentmail.json",)`（`check-file-ownership.py:57,63`）；三层证据：(b) 文件字面量（`:16`）、(c) **绑定写 API 调用名**（`save_agent_config/saveAgentConfig/writeAgentConfig/write_agent_config`，含别名；`:18-20,74-76,223-233`）、(d) 仓内原子写 helper 首实参（`_atomic_json_write` 等，`:21-25,87-89,271-272`） |

### A-3 门禁实测（本轮只读复算，**非门禁跑批**：直接调检查器自己的判定函数）

- `pysdk/aimail_tools.py` / `pysdk/gateway_api.py` / `pysdk/aimail_base.py` 对 gateway 字面量 → **hits=[]**
- `tssdk/…/address-code.ts` 对 gateway 字面量 → **hits=[]**
- `cli/aimail` / `cli/repair.py` / `cli/check_status.py` 对 `agentmail.json` → **hits=[]**，`cli/aimail` 另有 2 条
  **allowed-by-marker**（`:1622` roles 目录、`:1626` 角色 `.md`，理由均为「非绑定文件」）。
- **⇒ 关键结论**：`pysdk/aimail_tools.py:531` 与 `address-code.ts:145` 确实写了 `aimail_gateway.json`，但检查器
  **不跨模块传播污点**（自陈：`check-file-ownership.py:27-33`「不做跨模块数据流」；污点只在同文件同作用域累积，`:196-262`），
  路径字面量分别在 `gateway_api.py:20` 与 `config.ts:38` ⇒ **当前门禁对这两处是假绿**。
  归属：是「裁决 A 未覆盖的地址码自助链例外」还是「未登记越界」，**本轮不下裁决**，见 §10-①。
- 基线 `tests/contract/file-ownership-baseline.json:2-4` **当前为空**（棘轮只许减不许增，`:39-42`）；
  接法在 `tests/sdk-release-gates/gate-tests.sh:53-63`（`_fo_rc != 0 → exit 1`，rc=2 也红）。

### A-4 【裁】并入 `aimail-sdk-release/references/config-file-ownership-cli-vs-sdk.md`（52 行）的处置建议

- **建议 = 并入后「降为一行指针」**（**不执行**）。理由：它是 `aimail-sdk-release` skill 的引用文件，
  直接删会断链；且它保有的两个入口本稿不替代 —— 复验工具
  `aimail-development-workflow/scripts/boundary-audit.py --no-endpoints`（该脚本实存：`scripts/boundary-audit.py`，7703 B）。
- 本稿吸收：边界矩阵（A-1/A-2）、`save_agent_config` 收口史、legacy 清除裁决、死代码查证法。
- **吸收时必须标注该文 3 处与实况不符**（详见 §8）：
  - `:11`「**SDK 零写**（只读）」→ 不成立（A-1 两处 create-if-absent 写）；
  - `:12`「CLI **有 3 处维护性写** agentmail.json（`cli:1385/1400`、`cli:1580`、`cli:1886/1888`）」→ 2026-09-28 裁决 A 后**已不成立**：
    现码 5 处全部经 SDK 薄函数（`cli/aimail:1597,1612,1891,1932`、`cli/repair.py:714`），行号全部漂移；
  - `:20`「TS 共享原子实现 `mail-core config.ts saveBinding`」→ `saveBinding` 实际在
    `tssdk/packages/mail-core/src/auto-bind.ts:343`（`config.ts` 全文 grep `saveBinding` = 0 命中）。

---

## 1.2 【增】B — CLI → SDK 调用面清单

### B-1 `load_core()` 调用点（计数核实）

- **`cli/aimail` 内 = 14 处 ✅（10-03 复核；09-30 草稿 13 → 其间 cli/aimail 有改动，来源未追溯 §10-⑬）**：`cli/aimail:829, 986, 1090, 1527, 1755, 2127, 2145, 2400, 2429, 2480, 2939, 2977, 3137, 3314`
  （另有 `cli/aimail:69` `def load_core():` 与 `:73` `from runtime_core import load_core as _lc` 两处非调用 ⇒ 全文件 `load_core` 命中 16 行）。
- **全 `cli/` 裸调用 = **24** 处（10-03 复核；09-30 草稿 23 → 差 +1 在 cli/aimail）**（供棘轮）：上 14 + `cli/repair.py:78,200,324` + `cli/send_welcome.py:62,267` +
  `cli/setup_system.py:25` + `cli/deploy_bridge.py:11` + `cli/ping_test.py:52` + `cli/runtime_core.py:86,423`（:86 在 `load_adapter` 内）。
- 语义真源：`cli/runtime_core.py:65-74`（挂 `sys.path`、幂等、返回核心目录）；链 =
  仓内 `pysdk/` 优先 > pip `aimail` 兜底，`cli/runtime_core.py:43-62`（找不到 ⇒ `SystemExit` 明确报错，`:62`）。
- **形态铁律**：`load_core()` 之后的裸 SDK import（`aimail_base` / `gateway_api` / `aimail_tools` 均算）
  必须在同一函数/模块作用域内先出现 `load_core()`。**10-03 逐点复核**：14 个调用点全部满足
  （例：`:829→:830` `from gateway_api import …`、`:1755→:1756` `from aimail_tools import …`、`:1527` 区块内 `aimail_base`、`:986/:2429` = load_core 后进 `try:` 可选形）。

### B-2 可选消费（try/except 回退）**逐点**

| # | 位置 | 形态 | 是否先 `load_core()` |
|---|---|---|---|
| 1 | `cli/_common.py:29-34` `aimail_home()` | try `from aimail_base import aimail_home` → except 按同公式回退 `$AIMAIL_HOME → ~/.aimail` | 否（真·可选） |
| 2 | `cli/aimail:40-45`（模块级） | try `from aimail_base import aimail_home as _aimail_home_canon` → except 同公式回退 | 否（真·可选） |
| 3 | `cli/check_status.py:47-52`（模块级） | try `aimail_home` → except 同公式回退（自陈「离线自包含」`:45-46`） | 否（真·可选） |
| 4 | `cli/ping_test.py:53-61` | `load_core()`(`:52`) **后** try `from aimail_base import email_for_agent` → except **本地复刻实现**(`:56-61`) | 是（**混合：核心消费 + 防御性复刻**） |
| 5 | `cli/send_welcome.py:63-71` | `load_core()`(`:62`) **后** try `email_for_agent` → except 本地复刻(`:66-71`) | 是（**混合**） |
| 6 | `cli/send_welcome.py:266-275` `_agent_log_path` | try `load_core(); import aimail_base; return _abm.aimail_log_path(…)` → except 手算路径 | 函数内（真·可选） |

**【改】原稿 `:29-32`** 把 `send_welcome`/`ping_test` 与 `_common.aimail_home()` 并列成“可选消费”，
**不精确**：这两个文件在 try 之前已 `load_core()`（`:52`/`:62`），except 分支是「SDK 缺席时的第二保险 + 本地复刻」，
不是“可选消费”主形态；`check_status` 才是同列的真·可选。

### B-3 CLI 实际用到的 `aimail_base.*`（= **SDK 对 CLI 的公共契约**，改名/改签名即破坏 CLI）

**（i）可选消费面（必须在无 SDK 环境也可判读的三件）**：`aimail_home`（`cli/_common.py:30`、`cli/aimail:41`、`cli/check_status.py:48`）、
`email_for_agent`（`cli/ping_test.py:54`、`cli/send_welcome.py:64`）、`aimail_log_path`（`cli/send_welcome.py:268`）。

**（ii）核心消费面（`load_core()` 之后调用）**：

| 函数 | 调用点 |
|---|---|
| `prompt_rule_name_ok` | `cli/aimail:1543` |
| `read_prompt_rules` | `cli/aimail:1678` |
| `prompt_rule_matches` | `cli/aimail:1679` |
| `update_binding` | `cli/aimail:1597,1612` |
| `rename_binding` | `cli/aimail:1932,2193` |
| `deregister_agent_email` | `cli/aimail:3253,3307`（import 于 `:3236,3275`） |
| `iter_agentmail_configs` | `cli/repair.py:207` |
| `ensure_binding_webhook_secret` | `cli/repair.py:215` |
| `resolve_register_webhook_url` | `cli/repair.py:236,330` |
| `register_agent_email` | `cli/repair.py:245,328` |
| `backfill_binding` | `cli/repair.py:714` |
| `compute_api_signature` | `cli/send_welcome.py:245` |

**（iii）同属 SDK 但非 `aimail_base` 的公共面（一并登记，改名同样破坏 CLI）**：
`aimail_tools._GatewayClient`（`cli/aimail:968,1876,2194,3235,3274`、`cli/repair.py:79`）、
`gateway_api.load_gateway_config`（`cli/aimail:818,1071`）、`gateway_api.gateway_config_path`（`cli/setup_system.py:366` 经 import）、
`runtime_core.load_adapter`（`cli/runtime_core.py:77-92`）。

**（iv）纯注释/同构说明，不构成调用面**（防误读）：`resolve_manager_address`(`cli/aimail:539`)、
`register_agent_email`(`:659`)、`_read_role_file`(`:1528`)、`email_for_agent`(`:1995`)、
`ensure_bridge_routes_for_system`(`:2275`)、`_clean_agent_dir_name`(`:2802` 与 `cli/_common.py:38` 的“同构”注)。

---

## 1.3 【增】C — SDK → CLI 反调面（两个隐藏参数）

### C-1 两个参数的 argv 形状（字面量唯一）

| 侧 | argv 拼装 | 证据 |
|---|---|---|
| **Python** | `argv = [cli, "install", "--system-only"]`，`system_home` 非空时追加 `["-H", system_home]` | `pysdk/aimail_base.py:2642`（`ensure_system()` 定义 `:2612`；09-30 草稿 :2344-2346 已漂 +298） |
| **TS** | `const args = ['install', '--system-only']`，`opts.systemHome` 存在时 `args.push('-H', …)`；命令 = `opts.cliPath ?? process.env.AIMAIL_CLI ?? 'aimail'` | `tssdk/packages/mail-core/src/ensure-system.ts:148`（`ensureSystem()` 定义 `:126`；草稿 :141-143 已漂 +7） |
| **payload 侧（SDK 脚本经公开面调 CLI，非反调 ABI）** | shell：`aimail install --payload install mcp` / `aimail install --payload dir mcp` / `--payload resource skills` / `--payload source` | `pysdk/deer-flow/install-mcp.sh:79-80`、`pysdk/deer-flow/install-skill.sh:48`、`scripts/bootstrap.sh:140`、`pysdk/README.md:90` |

CLI 侧 dispatch（`cmd_install` def `cli/aimail:777`）：互斥校验 `:782`（两 flag 同给 ⇒ stderr + **rc 2**）、错侧参数拒收 `:784-816`
（payload 通道不接激活/接线参数；system-only 通道不接 `--dest/--source-root/--force/payload operand`）、
转发 `cmd_ensure_system`（def `:1076`）/ `cmd_payload`（def `:304`）。

### C-2 stdout 形状

- **契约**：`stdout` **恰一行 JSON**（成功/失败皆一行），人话一律走 stderr；`exit 0=ok, 1=error`
  （CLI 自陈 `cli/aimail:1088`「logs go to stderr; exit 0 = ok, 1 = error」；实现 `cmd_ensure_system` def `cli/aimail:1076`，错误出口 `_err()`
  `:1104-1109` = `print(json.dumps(out, ensure_ascii=False))` + `print(…, file=sys.stderr)`；**10-03 行号复核**，草稿 :1056-1090 系漂移前）。
- **解析方**：Python `pysdk/aimail_base.py:2355-2368`（`json.loads(stdout.strip())` → `success is not True or rc!=0` ⇒ 报错；
  成功取 `system_id`，`activated = (path == "activation")`）；TS `ensure-system.ts:152-176`（同判据，另取 `gateway_url/domain/system_name`）。
- **payload 通道 stdout = 字节级黄金**：`tests/test_install_machine_surface.py:25-26,38-47`
  （`dir mcp` ⇒ `/aimail-test-home/bin/mcp`；`source` ⇒ `repo\t<repo pysdk>`）。

### C-3 现有测试覆盖到哪

| 测试 | 覆盖 | 证据 |
|---|---|---|
| `tests/test_install_machine_surface.py`（**10 例**） | ① payload 黄金逐字 ② `--system-only` 失败路径**恰一行 JSON + rc 1** ③ 旧子命令名 rc 2 ④ 两 flag 互斥 rc 2 ⑤ 错侧参数 rc 2 ⑥ 孤儿操作数 rc 2 ⑦ 人面 `--help` 不含机器面字样 | `:38,44,50,56,66,74,79,85,91,96`；单行 JSON 断言 `:56-63` |
| `tssdk/packages/mail-core/test/ensure-system.test.ts`（**10 例**） | 无调用短路 / 归属短路 / 模糊归属 / 陈旧指针 / **空机反调并解析 JSON** / 复用路径 `activated=false` / CLI 错误 JSON / ENOENT 提示 / 不可解析输出；**argv 形状断言** `['install','--system-only','-H',…]` | `:41-206`，argv 断言 `:77,165` |
| `tssdk/packages/dsh-aimail/test/mail-service-ensure.test.ts` | 宿主插件 apply 的短路与反调 | `:51,59,64` |
| `tests/test_gateway_url_reuse_fallback.py` | 复用入口口径在 **install + install --system-only 两处**共用同一 prev 值 | `:9,19,119` |
| `tests/test_install_resolve.py` | `--home`/`--system-id` 歧义 ⇒ 不能回落已消耗激活码 | `:66` |
| **缺口** | **pysdk `ensure_system()` 自身无直接单测**：`grep -rln "ensure_system" tests/` 只命中 `test_gateway_url_reuse_fallback.py`；`--system-only` **成功路径**的“恰一行 JSON”用例缺（现只测失败路径） | 见 §10-② |

### C-4 现存边界测试（可直接复用）

`tests/test_sdk_cli_boundary.py:144` —— 断言「pysdk 可执行路径引用了 CLI 程序 ⇒ 提示改用 `aimail install --payload …`」（读法同 §4 第 1 条）。

---


## 1.4 【增·10-03】D — `install_steps` 表驱动执行器契约（`platforms.json` = CLI↔SDK 映射层；rust 化必须逐语义复刻）

> 三层分域（owner 裁决）：系统级 `aimail_gateway.json` CRUD 归 **CLI**；per-agent `agentmail.json` CRUD 归 **SDK**；
> `cli/platforms.json` = **CLI↔SDK 映射层**（平台注册表 + 安装动作表）。执行器 = `cli/aimail` `_run_install_steps`（`cli/aimail:570`，分发 `:608-677`）。

**kind 词表（6 个；未知 kind ⇒ `_warn` 跳过 `cli/aimail:677`）**：
`print` · `warn` · `spawn`（argv 表驱动，`subprocess.call` 继承 stdout ⇒ 步的 echo 直通安装输出，`:608-633`）·
`sdk_install`（转 `pysdk.install`）· `register_default` · `register_all`。

**两道硬门（rust 化必须等价复刻）**：
- **P1**：`register_default`/`register_all` 前置 `_require_mgr` 非空 manager（`:638,654`）——空 ⇒ 直接 fail，**不许**被 F9 吞成 install 成功；
- **F9**：`spawn` 的 `on_error=warn`（默认）= SDK 安装失败只打 `warn_hint`、**不阻塞** install 主流程（`:625-631`）。

**when 前置词表（6 个，求值 `cli/aimail:482-504+`）**：`path_exists` · `command_exists` · `sid_set` · `cfg_complete` · `runtime` · `runtime_not`（值 = `docker`）。
**spawn 附加机制**：`skip_if`（探测命中 ⇒ 跳过并打 already present，`:610-613`）· `on_missing=fail|warn`（argv[0] 缺失，`:619-624`）·
`env`/`*_hint`/`ok_text` 经 `_tmpl` 做 `{占位符}` 替换（`{home}` `{container}` `{container_home}` `{rc}` …）。

**现役契约步（10-03 现码，`cli/platforms.json`）**：
| 平台 | 步 | 锚 | 要点 |
|---|---|---|---|
| hermes | host 自适配 `sh -c` | `:244`（argv `:248`） | **uv → venv pip → ensurepip** 检测序（owner 10-03：aimailsdk 安装按 agent 环境包管理器自适配）；`when={path_exists:{home}/hermes-agent/venv/bin/python, runtime_not:docker}`；on_error=warn |
| hermes | docker 自适配 `docker exec sh -c` | `:259`（argv `:266`） | 同序在容器内，目标 `{container_home}/.venv`；`runtime=docker`；**r57 实证**容器内 rc=0、装后 venv import aimailsdk 0.1.34、零 warn |
| hermes | `sdk_install` → `register_default` → `register_all` | `:300` 起 | 见 kind 词表 |
| dsh | `dsh --profile web add dsh-aimail --config.minimumReleaseAge=0` | `:152` 起（年龄闸 `:162`） | **pnpm 12 年龄闸**（0.1.31 回溯事故的修法）；缺此开关 ⇒ 回溯装旧版 |
| deerflow | MCP 装配 spawn ×3 | `:466` 起（`:569,575,595`） | mcp **仅 deerflow**；`platforms.json` 无任何 mcp 标志位（§2） |

**双真源提醒**：契约字面量真源 = `contract/aimail-contract.json`（常量落 `pysdk/aimail_contract.py` 与 tssdk `contract.ts`）；
`platforms.json` 只放**动作表**。新增共享面 / 新 kind / 新 when 键 ⇒ 先入 §1 硬规则 3 清单再动码。
**SDK 安装归属**：`pip/uv install aimailsdk` 属**产品路径**（§7-A 口径 2/3），CLI 测试期不旁路安装。

## 2. MCP 的归属（用户概念，已用代码验证）【复核无改】

- **MCP ⊂ SDK，不属 CLI**。`pysdk/aimail_mcp_server.py` 是“平台无关 stdio MCP server”，CLI 只负责部署（`~/.aimail/bin/mcp`，bundle 名 `mcp`）。
- **tssdk 不需要 MCP**：TS SDK 原生内嵌工具，只把那份 Python 的 `TOOLS` 注册表当**逐字对标的规格源**（parity 测试读它）。
- **openclaw / dsh / pi 等 TS 平台不由 CLI 装 MCP**：payload bundle 只有 6 个 ✅ 属实 ——
  `cli/runtime_bundle.py:25,59-95` `BUNDLES = {mcp, deer-flow, skill-hermes, skill-openclaw, skill-deerflow, skill-dsh}`；
  `cli/platforms.json` 里**没有任何 mcp 标志位** ✅ 属实（全文 3 处 `mcp` 均为 deer-flow spawn argv/提示文案：
  `cli/platforms.json:592,598,618`；三者为 **step 条目**，`install_steps` 键见 `:569`、注册处 `:42`）。
- 结论：MCP 是**给无法原生内嵌的 Python 平台的兜底**（当前仅 deer-flow 装）⇒ 不要收进 CLI 二进制。
- 清理口径：把 MCP 写成“上游契约参照 / 通用第三种模式”的注释与文档属误导性残留。

---

## 3. 服务二进制 ↔ CLI 的**生命周期契约**（实现模板）

**【改】原稿 `:69-71`「现状缺口（都实测过）」整段已过时** —— 契约已在 bridge 侧落地：

- **原稿**：服务只有 `--daemon/--pid-file/--log-file/--config`，**没有** `stop/status/check-config`；pid 文件写了但退出不删；
  `--daemon` 在 Windows 是空桩；CLI **越界**自己管进程。
- **实况（2026-09-30）**：
  - bridge 已实现三开关：`aimail-bridge/src/main.rs:43-46,69-71,85-89,238-246`；语义实现
    `src/lifecycle.rs:1-14`（`--status/--stop/--check-config` 各自职责）、`src/lifecycle.rs:212-215`
    （`--status` 0=运行中 / 3=未运行，**绝不创建或删除 pid 文件**）、`:306-308`（`--check-config` 0 合法 / 2 非法）。
  - schema 契约已实现并有单测：`src/config.rs:9-21`（`schema_version` 缺省 1）、`:382-386`（更高版本拒绝启动）、
    `:461-478`（**缺省=1 / 未知键容忍 / 更高版本拒启** 三条单测）。
  - pid 存废新规则已实现：`src/main.rs:272-274`（正常退出删除 · 崩溃保留供 `stale-pid`）。
  - bridge 版本 = **0.7.5**（`aimail-bridge/Cargo.toml:3`；`~/aimail/bridge/` 四 zip 同号）。
  - CLI 已优先走契约：`cli/_common.py:43-60`（`--status --json` 单一真源）、`cli/deploy_bridge.py:427,440`
    （不支持契约才回退旧清理 + 打升级告警）；`cli/check_status.py:1330`。
  - **但 CLI 仍保留回退 kill 路径**：`cli/deploy_bridge.py:445-481`（`os.kill(15/9)` + `pgrep` 兜底）、
    `cli/aimail:2563-2579`、`cli/_common.py:92-98,141` ⇒ 原稿「客户端侧必须做的两件事」（停止失败绝不硬启 · 保留旧版回退）
    仍然有效，**保留**。
- 契约内容块（`--stop/--status/--check-config` 语义、退出码表、身份校验按 basename 比对、pid 存废顺序不可颠倒）
  **继续有效，原样保留**（`aimail-bridge/src/lifecycle.rs:128` 是该判据的**注释**；实现于 `:150/:171/:185`）。

---

## 4. 边界泄漏的检测（可写成 grep 断言）【保留 + 修订 + 三条新断言】

**2026-09-30 复扫（本轮实跑，原 5 条断言逐条验）**：
- ① SDK 侧 `cli/` 路径引用：`grep -rn "cli/runtime_bundle\|cli/aimail\|cli/check_status" pysdk/ tssdk/` → **9 处，全部非可执行**
  （`pysdk/deer-flow/manage.py:665` 注、`pysdk/deer-flow/install-mcp.sh:35,39` 注、`pysdk/install.py:530` docstring、
  `pysdk/hermes/patch_profiles.py:290` / `toolsets.py:37` / `patch_webhook.py:582` “Migrated from” 注、
  `tssdk/packages/pi-aimail/{src/register-cli.ts:4, dist/register-cli.js:4}` 平台边界注）。
  **【改】原稿 `:105-114` 的“保留命中清单”漏了 `pysdk/install.py:530` 与 `tssdk/…/dist/register-cli.js:4`**。
- ② `grep -rn "^from aimail_base import\|^import aimail_base" cli/ | grep -v "try:"` → **0 命中**，
  但 **【改】此断言恒绿、判据错位**：真正的裸 import 全是**缩进的**（函数内，如 `cli/aimail:1494,1891,1932,2193,3236,3275`），
  `^` 锚定使它对任何新违规都失明 —— 换成 **§4-B** 的 AST 断言。
- ③ `grep -cn "def prompt_rule_matches\|def read_prompt_rules" cli/aimail` → **0** ✅。
- ④ `grep -rn "'ensure-system'\|\"ensure-system\"\|'payload'," pysdk/aimail_base.py tssdk/…/ensure-system.ts` → **0** ✅。
- ⑤ `grep -c "prompt_rules" pysdk/aimail_base.py tssdk/packages/mail-core/src/*.ts` → pysdk=**10**，
  TS 侧仅 `preprocess.ts=6`、`types.ts=1`，**其余 17 个文件 = 0** ⇒ **【改】「两侧 >0」若按文件逐个判必误判**，
  应改成 `grep -l`（任一文件命中即该侧 >0）或显式合计。

**【改】原稿断言块本身的两处缺陷**（`原稿:121-122`）：
- 过滤正则 `grep -vE "^\S+:\s*#|…"` 与 `grep -rn` 的输出格式 `file:line:content` **不匹配**
  （`file:LINE:` 之后是行号，不是 `#`）⇒ 该分支永不生效，实际靠 `注释|Migrated from|边界` 关键字兜住；
  修法：`grep -vE '^[^ ]+:[0-9]+:\s*(#|"""|\*)'` 并显式排除 `dist/`、`lib/` 产物目录。
- 判据应写成“**可执行引用为 0**”，并给出命令而非隐含期望。

### 4-A 【增】A 类（配置文件归属）可执行断言建议

**放哪**：`~/aimail/tests/sdk-release-gates/gate-tests.sh`，紧接现有 `_fo_rc` 段（`:53-63`）之后。
**怎么判**：
1. 保留 `python3 tests/contract/check-file-ownership.py .`（rc≠0 ⇒ FAIL，含 rc=2 fail-closed）；
2. **新增**「跨模块盲区」子断言（当前检查器自陈不跨模块，A-3 已实证两处假绿）：
   ```bash
   # SDK 写系统级环境文件的站点集合必须 ⊆ 显式白名单，且每个站点必须带理由
   python3 tests/contract/sdk-gateway-config-writers.py . || _gw_rc=$?
   # 站点提取 = (写调用 ∧ 路径可溯到 gateway_config_path/gatewayConfigPath/… 字面量源函数)
   # 期望集合 = {pysdk/aimail_tools.py::activate_address_code_persist,
   #             tssdk/packages/mail-core/src/address-code.ts::activateAddressCodePersist}
   # 集合外新增站点 ⇒ rc=1；集合内站点缺 file-ownership-allowed:<理由> ⇒ rc=1；集合为空 ⇒ rc=0
   ```
   同时把「根因修复」列为前置：给检查器加**跨模块路径 helper 白名单污点源**
   （`gateway_config_path` / `gatewayConfigPath` / `agentConfigPath`），否则断言 2 是唯一有效防线。

### 4-B 【增】B 类（CLI→SDK 两种合法形态）可执行断言建议

**放哪**：CLI 门禁 `~/aimail-advanced/tests/cli/run-cli-gate.sh` 的 L0 段（pytest 步 `:120-124` 之前），
或直接作为 `tests/test_sdk_consume_shapes.py` 用例（会被 `:121` 的 `pytest tests/ -q` 自动收编）。
**怎么判**（AST，非 grep）：
1. 遍历 `cli/*.py` + `cli/aimail`：每个 `from aimail_base import …` / `import aimail_base` 节点，
   若其**不在 `try:` 分支**，则其所在函数/模块作用域内必须存在先序 `load_core()` 调用 ⇒ 否则 **rc=1**；
2. 棘轮：`cli/aimail` 内 `load_core()` 调用点计数 **≤ 14**（只许减不许增，10-03 基线），全 `cli/` ≤ 24 ⇒ 超出 **rc=1**；
3. 可选消费三件套 `aimail_home/email_for_agent/aimail_log_path` 的 except 分支必须存在本地回退（缺 ⇒ rc=1），
   保证「无 SDK 也能跑」的承诺不被删掉。

### 4-C 【增】C 类（反调 ABI）可执行断言建议

**放哪**：**双侧** —— argv/字面量唯一性放 **SDK 门禁** `tests/sdk-release-gates/gate-tests.sh`；
stdout 形状放 **CLI 门禁**（`tests/test_install_machine_surface.py` 已被 `run-cli-gate.sh:121` 收编）。
**怎么判**：
1. `grep -c` 两侧 argv 构造字面量各 **== 1**（`pysdk/aimail_base.py` 的 `["install", "--system-only"]`、
   `tssdk/…/ensure-system.ts` 的 `['install', '--system-only']`），且 `-H` 追加只允许紧随其后一行；
2. 旧名归零（沿用原稿第 4 条）：`'ensure-system'` / `"ensure-system"` / 独立 `'payload',` 在两份反调实现中 **== 0**；
3. **补一条 stdout 成功路径用例**：`aimail install --system-only` 在**成功**时 stdout 也恰一行 JSON
   （现 `tests/test_install_machine_surface.py:56-63` 只断言失败路径 rc 1），判据 = `len([l for l in out.splitlines() if l.strip()]) == 1` 且 `rc == 0`。

---

## 5. 大改造的顺序与判据（用户口径，逐字）

**四条件硬门禁（2026-09-23 用户裁决，逐字；状态表与复核命令见 `agentmail-cli/references/cli-rust-migration-eval.md` 末节）**：

> “在执行rust前, gateway/advanced已完成修订且走完上线流程部署生产环境,
>  bridge也已经稳定部署发版打包到bridge目录, 且最新版已经在本地运行.
>  SDK也已稳定并发布新版, 而且人工参与用新激活码完成5种agent系统的对接.
>  这四个条件不满足就不能进行rust化的操作.”

**【改】状态（原稿 `:144-148` 写 2026-09-23 口径）**：
- ① ✅ 已部署复核（`advanced-a887512+gw0b5bbaf`）— 本轮未复跑，**沿用原记录**。
- ② ✅ bridge **0.7.5**（`aimail-bridge/Cargo.toml:3` + `~/aimail/bridge/` 四 zip + §3 三开关实装）。
- ③ ✅ **SDK 已发新版**，版本号随发布滚动：09-23 记 0.1.15 → 09-30 记 0.1.30 → **10-03 实况 = v0.1.34**
  （`git tag` 最新 = v0.1.34、`tssdk/packages/mail-core/package.json` 版本字段 = 0.1.34）。
- ④ ◇ **未确认**（09-30 查 `~/.hermes` 会话库与两仓 `docs/` 无对接记录；**10-03 复核仍无新记录**，维持 ◇）。
- **④ 未清之前，rust 化（含 P0 骨架）一律禁止开工** —— 口径不变。

前置状态：
- ✅ 两 SDK/脚本可执行边界违规清零（§4 2026-09-30 复扫）；
- ✅ 机器面 ABI 折叠完成且有黄金契约测试（裁决 B，`tests/test_install_machine_surface.py` 10 例）；
- ✅ 桥契约三开关实装（§3，`src/main.rs:43-71`、`src/lifecycle.rs`）；
- ◇ `--check-config` 的**独立**单测：`src/config.rs:461-479` 有 **1 个 `#[test]` 内含 3 场景**（缺省=1/未知键容忍/更高版本拒启；拒启断言在 `:479` 的 `unwrap_err()`），但 `--check-config` 命令本身
  是否有 CLI 级单测 **未确认**（查了 `aimail-bridge/src/*.rs` 的 `grep check_config` 与 `aimail/tests/*.py`）；
- **【改】退役判据 ②「本仓 6 个 `tests/test_*.py` 直接 import `cli/*.py`」过时**：实测 **10 个**用
  `SourceFileLoader`/`spec_from_file_location` 加载 `cli/*.py`
  （`test_bridge_route_target.py:13,17`、`test_bridge_upgrade.py:12,16`、`test_gateway_url_reuse_fallback.py:34,40`、
  `test_install_plugin_ensure.py:15,19`、`test_install_register_nonblocking.py:16,20`、`test_ping_smtp_host_parse.py:27,57`、
  `test_register_address_alias.py:19,23`、`test_reset_platform_override.py:28,35`、`test_sdk_dispatch.py:13,17`、
  `test_cli_symlink_invocation.py:11,15`）+ `test_repair_bridge_mode.py:24` 走 `sys.path.insert(cli/)` ⇒ 11 个库方式消费方（**10-03 复测**：`SourceFileLoader/spec ∧ cli/` = **10** ✓；`sys.path` 式 = `test_repair_bridge_mode` 1 个 ⇒ 仍 11）。

其余（顺序 / 不要 / 文件归属）原样保留。

---

## 6. 文档↔代码比对的方法（原样保留）

1. 把文档里的**可验证断言**逐条列出（路径、字段名、命令与开关、字面量标签、默认值、退出码、外部 URL/分支）。
2. 每条都在代码里找**真源**：常量、字面量、argparse 的 `add_parser/add_argument`、模板文件。
3. 分类：`✔ 准确` / `✘ 过时或错误`（改文档） / `◐ 不精确`（补限定语） / `◇ 未能核实`（**如实标注，不猜**）。
4. 文档 = 产品目标：文档承诺了而实现没有 ⇒ **补实现**，不要删文档迁就实现。
5. 交叉引用会随结构变化失效 —— 改结构时把交叉引用一起 grep 一遍。

---

## 7. 2026-09-30 追加：toolset/skills 归属与门禁分工（owner 裁决）【两处修订】

- agentmail 的 toolset/skills 无效 ⇒ 一律在 SDK 范围内解决；判定与修补在 **SDK 门禁侧**
  （`tests/SDK/docker-regression/`，第五维 E5：存在 + 有效，只有“存在”过而“有效”未验 ⇒ 不许记 PASS）—— 原样保留。
- 两门各盯各的、不互相引用、不互相兜底 —— 原样保留，但**两处表述需按工作树实况改写**：
  - `journey-in-host.sh` J5-2 的 `toolset`：**10-03 现态（`ed30a40` 树净）——已随提交进入** `J5_ALLOW`
    （`tests/cli/docker/journey-in-host.sh:1921`，理由 `:1922` = 移交 SDK L2 E5、登记=移交不作绿放行；hermes 追加形 `:1948`）
    ⇒ 草稿「已摘 vs 工作树回加」之争已收口，现态 = **移交登记**。
  - `cli-in-host.sh` `CHECK_ALLOW`：**10-03 现态（树净）**——含 `toolset` 的登记与理由在
    `tests/cli/docker/cli-in-host.sh:341-342`（“hand-off, NOT a waiver”，同段登记 pointer 自报/判读不一致=归属未定）
    ⇒「待与 runner 分账收口」之说**已废**：分账被方案A物理分域取代（§7-A）。
- ~~CLI L2 与 SDK 段共用 FAIL 计数 ⇒ runner 分账 `CLI_STAGE_FAIL`/`SDK_DIM_FAIL`~~ —— **已销账（10-03，方案A）**：
  两门物理分域（`run-hosts-from-host.sh:153-156` CLI 模式跳过 SDK 回归段 · `run-all-hosts.sh:223` 起 E 段归 SDK ·
  25 格台账迁 SDK 模式分支 `run-all-hosts.sh:354`），rc 各自独立（r55/r56 双绿实证）；`grep CLI_STAGE_FAIL|SDK_DIM_FAIL` = 0（从未实施，也不再需要）。
- 契约五维 × 五平台 = 25 格无 N/A、第五维零白名单、CLI 参数五平台一致、`manager` env 兜底不可为空（`5137e34`）、
  rust 化防漂移（验收面 = §1 边界 + 共享面清单 + 两套门禁三层 + 四条件硬门禁）—— 原样保留。


### 7-A 【增·10-03】方案A 双门分域 —— 已实施并双绿（CLI 迭代与 rust 化的门禁前提）

> owner 裁决（10-03 原话要点）：进入 CLI L2 之前，原生 agent 环境必须干净可用（LLM 已配好）**固定住**；
> CLI 测试期 SDK = **线上最新且固化、不碰 SDK 内部** —— 多变量固定才能测目标量；SDK L2 对称：固定 agent 环境和 CLI，测 SDK 内部。
> 落地：`aimail-advanced @ ed30a40`（树净）· `aimail @ 415b17f`（产品面自适配）。

**变量矩阵（两门不互引）**：
| | 固定量（不动） | 被测量 |
|---|---|---|
| **CLI L2** | 原生 agent 镜像（`build-hosts.sh` 五镜像 `:native`：零 SDK 插件 + LLM 预置 + venv 环境层）+ SDK=线上最新（**只经产品路径 `aimail install` 安装**） | CLI：自举 → `aimail install` → `aimail welcome` → 15 顶层+prompt5 子命令及恢复面 |
| **SDK L2** | 原生 agent 镜像 + CLI | SDK 内部：E1/E3/E4/E5 契约段 + E2 push/pull + DIM2 行 + 25 格矩阵 + install-sdk 安装面 |

**口径三条（owner 10-03 三次修正，代码注释已固化）**：
1. **python 虚拟环境（venv）= 环境** → 镜像预置（`Dockerfile.hermes` 顶部注释写明区分）；
2. **`pip/uv install aimailsdk` = SDK 的安装**（非依赖包、非环境）→ 镜像**不带** aimailsdk（旧 wheel + 新快照 CLI = 混装炸弹，`pysdk/install.py:41-48` 实证）；
3. **包管理器自适配权威在 `aimail install` 产品**（§1.4 D hermes 两步）；测试单源 `install-sdk.sh` 同口径（uv 优先、否则 pip+ensurepip；版本断言走解释器 `importlib.metadata`，与管理器无关）。

**两门拓扑（10-03 实装）**：
- **CLI L2 车**：`tests/cli/run-cli-gate[-split].sh` → `l2-docker.sh` → `run-all-hosts.sh(CLI_L2=1)` → `run-hosts-from-host.sh`
  - `CLI_L2=1` ⇒ **跳过容器内 SDK 回归段**（`run-hosts-from-host.sh:153-156` 打印跳过行；E1/E3/E4/E5 归 SDK 门禁）；
  - E2-pull/E2-push/DIM2 行整块由 `run-all-hosts.sh:223` 起的 SDK 模式门跳过；
  - **25 格台账迁出 CLI 车**（`run-all-hosts.sh:354` 后的无 CLI_L2 分支打印；`l2-docker.sh` 只打 CLI 判读）；
  - **stage-0 开测前置**（`tests/cli/docker/cli-in-host.sh`）＝环境固定三连：预置面零插件残留（hermes=venv 在位）·
    LLM 配置在位（`llm-config.py verify`，装前模式回退）· LLM 真探活（`llm-fixture.py probe`）；
  - **装后环境前提**＝`环境前提·SDK固化`：产品路径安装版 == `npm view` 最新（TS 三平台）。
- **SDK L2 车**：`run-all-hosts.sh` **原生模式（无 CLI_L2）** = 回归段 + D1 + E2 + DIM2 行 + **25 格台账**；
  独立入口 = `tests/SDK/docker-regression/hosts/run-sdk-l2.sh`（与本稿同轮落地；l2-docker 的 env 剥掉 CLI_L2）。
  `install-sdk.sh` 消费方 = 回归段起点 + E2 探针前，**仅 SDK 门禁**（头注释已改写）。

**读数（同路径实证）**：
- r55 纯 CLI 门禁：五平台 rc=0 · `AGG_GATE_RC=0` · gap=0 · 合并红=none（`/tmp/cli-gate/r55/split-1003-191039`）；
- r56-sdk SDK 独立门禁：**25 格 25/25** · DIM2 五平台 PASS · agent docker PASS=*** FAIL=0 · rc=0（`/tmp/sdk-l2-logs/20261003-193803`）；
- r57 hermes（产品自适配落地后）：rc=0 · D1 PASS=*** 含容器 venv import 0.1.34 · 零 warn。

**对 rust 化的直接含义**：
1. rust CLI 必须复刻**表驱动执行器语义**（§1.4 D：kind × when × skip_if × F9 warn × P1 硬门 × `_tmpl` 占位符）——
   这是 CLI↔SDK 映射层的行为契约，不是实现细节；
2. 两门**判读面不可再合并**（草稿 §9 第 2 步的「runner 分账」已被物理分域取代，勿按旧稿实现）；
3. 环境前提断言（干净 / LLM 在位 / SDK 固化）是**测试前置**而非被测项，rust 化验收沿用同一前置形态。

---

## 8. 【增】裁剪与“与实况不符”登记（本轮只登记，不改既有文件）

### 8-1 `aimail-sdk-release/references/config-file-ownership-cli-vs-sdk.md`（52 行）
- 处置建议：**并入 §1.1 后降为一行指针**（保留 `boundary-audit.py` 复验入口）；**不执行**。
- 三处与实况不符：`:11`「SDK 零写」✘；`:12`「CLI 有 3 处维护性写 + 旧行号」✘（裁决 A 后已改走 SDK 薄函数）；
  `:20`「config.ts saveBinding」✘（实为 `auto-bind.ts:343`）。

### 8-2 `aimail-sdk-release/references/platform-registry-boundary.md`（70 行）→ 指针化建议（**不执行**）
与**主文档**的重复点（语义级，共 3 处）：
| platform-registry-boundary.md | 主文档重复处 | 建议 |
|---|---|---|
| `:3-7` CLI 平台无关调度器 / 适配实现按语言归属落 SDK / 禁止 `cli/aimail` 新增平台 if-elif | `:10-14` 三边界表 + `:38-42` 硬规则 2 | pb 该段压成 2 行 + 指针到主文档 §1 |
| `:30-35` TS 平台适配不得进 pysdk、`register-cli.ts` 入口（dsh lib / pi dist） | `:103-116` §4 边界泄漏检测（判据“可执行引用为 0”） | pb 改指针到 §4，保留 outDir 这条**独有**坑 |
| `:38-39` register-cli ABI：stdout 单行 JSON `{ok,…}`、exit 0/1、日志走 stderr「对齐 ensure-system 契约」 | `:20-24` 机器面 `--system-only` 单行 JSON + `:39-42` 硬规则 2 | pb 只留 register-cli **特有字段**，契约句指针到 §1.3 C |
另注（**更严重的重复在别处，一并登记**）：`platform-registry-boundary.md` 与
`aimail-agent-integration/references/platform-registry-boundary-2026-09-08.md`（93 行）**近乎全文重叠**
（schema `pb:7-18` ↔ `pri:9-17`、register 委托 `pb:19-28` ↔ `pri:19-30`、共享系统冲突 `pb:46-48` ↔ `pri:43-51`、
register-cli ABI `pb:38-39` ↔ `pri:53-57`、实证 `pb:50-61` ↔ `pri:59-63`）⇒ 建议二者合一、另一份降指针。
**pb 自身还有一段与实况不符**：`pb:63-69`「Phase 2（**未实施**，方案备档）」✘ —— Phase 2/2b/3 早已实施
（`platform-registry-phases-2-3.md:7-30,32-45,47-63`；`cli/platforms.json` 已有 `install_steps` 见 `:592` 起）。

### 8-3 主文档（`cli-sdk-bridge-boundaries.md`）中与实况不符 / 不精确的表述（逐条）

| # | 位置 | 判定 | 实况证据 |
|---|---|---|---|
| 1 | `:69-71` §3「现状缺口：没有 stop/status/check-config；pid 退出不删；CLI 越界自己管进程」 | **✘ 过时** | `aimail-bridge/src/main.rs:43-71,238-246`、`src/lifecycle.rs:1-14,212-215,306-308`、`src/config.rs:382-478`、`src/main.rs:272-274`；CLI 已优先走契约 `cli/deploy_bridge.py:427,440` |
| 2 | `:144-147` §5「SDK 已发新版 **0.1.15**」 | **✘ 版本过时(滚动)** | 09-30 时点 v0.1.30；**10-03 现点 = v0.1.34**（tag + mail-core 版本字段） |
| 3 | `:165`「本仓 **6 个** `tests/test_*.py` 直接 import `cli/*.py`」 | **✘ 过时** | 实测 10 个 loader 式 + 1 个 `sys.path` 式（清单见 §5） |
| 4 | `:121-122` 断言过滤正则 `^\S+:\s*#` | **✘ 失效** | 与 `grep -rn` 的 `file:line:content` 格式不匹配（见 §4 开头） |
| 5 | `:124`「`grep -rn "^from aimail_base import…" cli/ \| grep -v "try:"` 期望 0」 | **◐ 恒绿、判据错位** | 实测 0 命中，但裸 import 全是缩进的（`cli/aimail:1494` 等），锚定 `^` ⇒ 对新违规失明 |
| 6 | `:131`「`grep -c prompt_rules … 两侧 >0`」 | **◐ 不精确** | pysdk=10；TS **19/21** 文件=0（21 个 .ts：仅 `preprocess.ts=6`、`types.ts=1` 非 0）⇒ 应 `grep -l`/合计 |
| 7 | `:105-114` §4「保留命中全部是注释/历史引用」的清单 | **◐ 不完整** | 漏 `pysdk/install.py:530`（docstring）、`tssdk/packages/pi-aimail/dist/register-cli.js:4`（构建产物内注释） |
| 8 | `:29-32` 硬规则 1 把 `send_welcome`/`ping_test` 归入“可选消费” | **◐ 不精确** | 两文件在 try 前已 `load_core()`（`cli/ping_test.py:52`、`cli/send_welcome.py:62`）⇒ 是“核心消费 + 防御性复刻” |
| 9 | `:196`「toolset 已摘（`7c4a2ca`）」 | **✘ 已收口** | 10-03 现态：`toolset` 在 `journey-in-host.sh:1921-1922`（已提交、移交 E5 理由）+ hermes 追加 `:1948` |
| 10 | `:197`「cli-in-host 含 toolset=待修」 | **✘ 已收口** | 10-03 现态：登记+理由在 `cli-in-host.sh:341-342`（hand-off NOT waiver），树净；收口路径=方案A(§7-A) |
| 11 | `:33`「`cli/aimail` 内 13 处 `load_core()`」 | **◐ 已漂** | 10-03 现码 = **14**（`:829…:3314`）；全 cli/ = 24（§1.2 B-1） |
| 12 | `:17-19`「15 顶层子命令 / prompt 嵌套 / persona rc 2」 | **✔ 准确** | 见 §1 复核 |
| 13 | `:24`「黄金 = `tests/test_install_machine_surface.py`（10 例）」 | **✔ 准确** | 10 个 test 函数 |
| 14 | `:59-60`「bundle 只有 6 个 / `platforms.json` 无 mcp 标志位」 | **✔ 准确(行漂)** | `cli/runtime_bundle.py:59-95`；mcp 现码 `cli/platforms.json:569,575,595`（deerflow install_steps `:466`；09-30 行号系 hermes 自适配两步等改动前） |
| 15 | `:158`「`--check-config` 单测仍 ◇」 | **◇ 未能核实** | bridge 有 schema 单测 `src/config.rs:461-478`；命令级单测未找到（查了 `aimail-bridge/src/*.rs`、`aimail/tests/*.py`） |
| 16 | `:116`「`pysdk/hermes/*.py` 逐行比对未重跑」 | **◇ 未能核实** | 本轮未做逐行比对（额度与只读约束下未展开） |
| 17 | —（10-03 新增） | **✘ 行号整族漂移** | 09-30→10-03 两仓改动：`cli/aimail` +~74、`pysdk/aimail_base` 反调 +298（:2314→:2612）、`ensure-system.ts` +6、`platforms.json` hermes 两步自适配 + dsh 年龄闸（§1.4 D） ⇒ **草稿行号一律以 10-03 现码为准**，来源会话未追溯（§10-⑬） |
| 18 | —（10-03 新增） | **✘ 门禁拓扑重构** | 方案A 分域（§7-A）：CLI L2 车不再含 SDK 回归段/E 段/25 格；SDK 门禁独立车（`tests/SDK/docker-regression/hosts/run-sdk-l2.sh`）；草稿 §7/§9/§10 的「runner 分账」设计被取代 |
| 19 | —（10-03 新增） | **✘ 新增产品契约面** | `cli/platforms.json` hermes 安装步 = uv→venv pip→ensurepip 自适配（host/docker 两步，§1.4 D）；dsh 安装步含 `--config.minimumReleaseAge=0`（`:162`）；棘轮 `tests/test_hermes_pkgmgr_adapt.py` 3 例 |
| 20 | —（10-03 新增） | **◐ 阶段前置新规** | CLI L2 开测前置 = 环境固定三连（预置面零插件残留/venv 在位 · LLM 配置在位 · LLM 真探活）+ 装后 `环境前提·SDK固化`，落 `tests/cli/docker/cli-in-host.sh`（§7-A） |

### 8-4 其它文档的过时处（**只登记，本轮不改** —— 与任务已知的三处同列）
- `sdk-gate-tiers.md:43`、`cli-rust-ization-plan-v3.md:163`（“pytest 95”）、`cli/README.md:200` —— **复核结论：不成立**（该行实为 `Dimension order (user-mandated): **config files → platform runtime …`，全文 `grep '12'` 0 命中，无“12 子命令”表述）。
- 本轮新登记：`config-file-ownership-cli-vs-sdk.md:11,12,20`（见 8-1）、`platform-registry-boundary.md:63-69`（见 8-2）。

---

## 9. 【增】“固化分两步”清单

> 原则：**两门各盯各的、不互相兜底**（§7）；先固化“归属清晰”的一侧，再固化“消费形态”的一侧。

**第 1 步 — SDK 门禁（`~/aimail/tests/sdk-release-gates/gate-tests.sh`）固化「谁能写 / 反调字面量唯一」**
1. 保留 `check-file-ownership.py`（`:58`）+ 空基线棘轮；**新增 §4-A** 的跨模块盲区子断言（站点集合 ⊆ 白名单 + 理由必填）。
2. 固化 §4-C 的 argv 侧：两份 `["install","--system-only"]` 各 ==1、`-H` 追加位置、旧名 `'ensure-system'`/独立 `'payload',` ==0。
3. 固化共享面清单（§1.3 payload 公开面 4 处 + §1.1 两文件键位）：新增共享键 ⇒ 必须先入清单（可复用
   `tests/contract/check-docs-consistency.py` 的“契约值 == manifest”机制，`gate-tests.sh:64-73`）。
4. 验收判据：`gate-tests.sh` rc 0，且 A-3 两处假绿站点**已在白名单里带理由**（不是“没被发现”）。
5. 回滚：子断言独立成脚本，失败只 `exit 1` 于新增行，不影响既有 `_fo_rc/_zb_rc/_doc_rc`。

**第 2 步 — CLI 门禁（`~/aimail-advanced/tests/cli/run-cli-gate.sh`）固化「怎么消费 / stdout 形状」**
1. 新增 §4-B 的 AST 断言（两种合法形态 + `load_core` 计数棘轮 13/22 + 可选消费回退存在），接在 L0 pytest（`:120-124`）之前或做成 `tests/test_sdk_consume_shapes.py`。
2. 补 §4-C 的 **成功路径单行 JSON** 用例进 `tests/test_install_machine_surface.py`（现仅失败路径 `:56-63`），由 `run-cli-gate.sh:121` 的 `pytest tests/ -q` 自动执行。
3. ~~runner 分账 `CLI_STAGE_FAIL`/`SDK_DIM_FAIL`~~ → **已由方案A物理分域取代并实施**（10-03，§7-A）：CLI 车不跑 SDK 段，SDK 红不再传导 CLI，无需变量分账。
4. 验收判据：`run-cli-gate.sh` L0 全绿 + 人为注入一条违规（函数内裸 `import aimail_base` 不带 `load_core()`）必须 rc≠0。
5. 回滚：两处均为**新增**判定行，删除即回到现状；不动既有 10 例黄金。

---

## 10. 【增】未确认 / 拿不准（逐条，附查过的地方）

1. **两处 SDK 写 `aimail_gateway.json` 的归属**：是“裁决 A 未覆盖的地址码自助链例外”，还是“未登记越界写”？
   —— 查了 `check-file-ownership.py:5-25`（裁决只写“系统级 env ← CLI”）、`pysdk/aimail_tools.py:523-536`、
   `address-code.ts:139-158`、`file-ownership-baseline.json`（空）。**未查** owner 会话/裁决记录，故不下结论。
2. **pysdk `ensure_system()` 无直接单测**：`grep -rln "ensure_system" tests/` 只命中 `test_gateway_url_reuse_fallback.py`；
   未找到覆盖「Python 侧反调 argv / stdout 解析」的用例（TS 侧 10 例齐全）。
3. **`--system-only` 成功路径的 stdout 形状**无断言（现只测失败 rc1，`test_install_machine_surface.py:56-63`）。
4. **`agentmail.json` “9 fields”** 的逐字段清单未核（线索：`cli/aimail:21-23`；查了 `pysdk/aimail_tools.py:502-520` 只列 7+2 键）。
5. **`aimail-bridge --check-config` 命令级单测**未找到（查了 `aimail-bridge/src/*.rs`、`aimail/tests/*.py`）。
6. **四条件之 ④（5 系统新激活码对接）进度**未确认（09-30 查两仓 `docs/`、`.hermes/pending/` 无记录；**10-03 复核仍无**）。
7. **`pysdk/hermes/*.py` 与 `cli/` 逐行比对**（原稿 `:116` 的 ◇）本轮未做。
8. ~~runner 分账是否已实施~~ → **已销账（10-03）**：变量从未实施（grep=0），被方案A物理分域取代（§7-A），设计不再需要。
9. **TS 侧 `address-code.ts:150` 仍写 `admin_key: act.raw_key`**，与 Python 侧 D3（`scope:"agent"`、不再写 admin_key）**不同步** —— **10-03 复核 `:150` 仍写、仍无断言**，差异维持未确认。
10. **`platform-registry-boundary.md` 是否已被别处标注重复**：未查其他 skill 文档的交叉引用。
11. **任务口径 187 行 vs 实测 206 行**：差额 = 原稿 §7 整节（`:189-206`）；若任务所指是历史版本，需按 git 历史再核（**未查该 skill 目录的 git 记录**）。
12. 本轮**未跑** `boundary-audit.py --no-endpoints`（不跑批），其 `--no-endpoints` 输出未取。
13. **草稿后（09-30→10-03）两仓改动的来源会话未逐一追溯**（`cli/aimail` +~74、`aimail_base` +~298、`platforms.json` 两处产品面新增等）；
    本稿只保证 10-03 现码锚点，历史归因未核（git log 未逐条比对草稿基线 37873da/69dd5c1）。
