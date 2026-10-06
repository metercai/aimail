# P1 改动清单：SDK 侧 op 收敛（映射表 + 接口契约）· 待 owner 过目后动刀

> 计划文档（非规范）。规范见 `docs/contract-boundary_zh.md`。原则：**复用既有实现、只加编排** ⇒ 不破坏既有内部流程逻辑。

## 1 现状 → 终局的映射表（每条含调用点）

### 1.1 旧门 op（6）→ 归宿
| 现 op | 现调用点 | 归宿 |
|---|---|---|
| `version` | 门表 | 删除；改由 CLI **直读包元数据**（或新 op 响应内含 `sdk_version`） |
| `iter_bindings` | repair.rs:828 | 删除；CLI **直读**绑定文件（§2 读权限） |
| `ensure_webhook_secret` | repair.rs:869 | 并入 `update`（内部保障） |
| `resolve_register_webhook_url` | repair.rs:915 / :1099 | 并入 `assemble` / `update` 内部 |
| `register_agent_email` | repair.rs:936 / :1107 | 并入 `assemble` |
| `backfill_binding` | repair.rs:2083 | 并入 `update` |

### 1.2 旧薄入口（5）→ 归宿
| 薄入口 | 现调用点 | 归宿 |
|---|---|---|
| `plan_address_name` | register.rs:202 | 并入 `assemble` 内部（**不再外露**） |
| `rename_address` | register.rs:108 · address.rs:612 | 并入 `assemble`（注册后改名）/ `update`（用户改名） |
| `set_agent_manager` | address.rs:571 | 并入 `update` |
| `update_binding` | prompt.rs:50（prompt / persona） | 并入 `update` |
| `deregister_agent_email` | uninstall.rs:237 / :320 | 并入 `teardown` |
| `cleanup_system_whitelists` | uninstall.rs:339 | 并入 `teardown` |

## 2 新接口契约（草案，待定稿）

| op | 入参（JSON） | 出参 | 内部动作（**复用既有函数**） |
|---|---|---|---|
| `assemble` | `platform, home, system_id, domain, system_name?, manager_address, requested_name?, agent_dir?` | `{ok, address, binding_path, sdk_version}` | 命名计划 → 构建注册器调用 → 执行（**transport 分派**：node_entry / python_module / python_script / host_command）→ 必要时改名 → 写绑定 |
| `update` | `system_id, home, agent?, fields{manager_address?, prompt_rules?, persona?, webhook_secret?}` | `{ok, changed[]}` | 受控字段更新（判定+落盘在 SDK）；含 webhook secret 保障 |
| `teardown` | `system_id, home, agent?, mode{unregister?, whitelist?, backfill?}` | `{ok, actions[]}` | 注销 / 白名单清理 / 绑定回填 |

- **版本握手**：任一 op 响应内含 `sdk_version`；CLI 侧维护最低版本要求，过旧 ⇒ 响亮失败 + 升级指引。
- **实现方式**：3 个 op 加入**同一张分发表**（`pysdk/sdk_ops.py:168-173`），内部调用既有 `aimail_base.*` 等函数 ⇒ **不新写逻辑、只做编排**。

## 3 门禁变更（按已批准口径）
- **第一序（主体）**：既有用例**基本不动**（仅在入口被合并处**改指向**），目的 = 证明改动**未破坏既有内部流程** ✓；新增 3 op 的**最小用例** ✓。
- **第二序（附加）**：新增 **1 项边界棘轮** —— op 集合 == {assemble, update, teardown} ✓；SDK→CLI 调用白名单 ✓；SDK 对系统级配置只读 ✓。
- **L3**：随版本重跑，脚本不变 ✓。

## 4 已定（按已生效规范与代码推断，不再向你确认）

**4.1 `version` 的归宿：CLI 直读包元数据，不进接口** ✓
- 依据（规范已锁定，非新决策）：§4.1(1)「取值类不在接口清单内」+ §2「CLI 读取范围」⇒ `version` 属取值类 ⇒ **CLI 直读**（pip `importlib.metadata` / npm `package.json`）✓；仅在 op 响应中**附带** `sdk_version` 以便版本握手 ✓（不作为独立 op）。

**4.2 改名的归属：两条路径分属两个 op** ✓
- 依据 1（代码）：`address.rs:609` 注释"改名 = SDK 的 CRUD（校验/派生/冲突预检/云端 rename/白名单清理）"⇒ 改名**必须经 SDK** ✓。
- 依据 2（规范语义）：`assemble` = 装配期**幂等**动作（注册 + 必要时收敛到契约名）✓；`update` = 对**已注册体**做受控变更（用户命令触发）✓。
- 判定：**装配期改名 → `assemble` 内部** ✓（CLI 不再事后调）；**用户主动改名（`aimail address` 改名路径）→ `update`** ✓。

## 5 契约优先（规范一旦生效即具约束力，对 CLI/SDK/执行者一律适用）

1. **规范已规定者 ⇒ 一律按规范执行** ✓：不重复询问 owner、不另行判定 ✗、不"重新讨论已定结论" ✗。
2. **规范未覆盖、代码可取证的 ⇒ 取证后按规范原则执行**，并在文档中**写明依据**（可追溯 ✓）。
3. **仅当规范与代码均不覆盖，且属"价值/优先级取舍"的决策点 ⇒ 提交 owner 裁决** ✓。
4. 执行者的义务是**遵守契约**，而非在契约之外另作决定；发现契约本身有缺口/矛盾 ⇒ **先改契约（走审批）** ✓，再照新契约执行 ✓。

## 6 下一步（P1 动刀）
SDK 侧（`pysdk/`）+ 门禁（`tests/`）：① 分发表新增 `assemble`/`update`/`teardown`（内部转调既有 `aimail_base.*` 等 ✓）② 3 op 最小用例 ③ 边界棘轮 1 项 ⇒ 跑 SDK 门禁（既有用例应全绿 ✓）⇒ 发版。
