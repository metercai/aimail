# 交接给质检：C16 之后的全部改动 → 复检 → L2 门禁 → 发版

日期：2026-11-25（夜） ｜ 契约：**v2.4** ｜ 两个仓都有改动，需一起验

本文汇总"C16 处置决定 + 命名契约裁剪"之后的全部改动（含随之展开的清理与跨仓联动）。请按 §4 复检、
§5 跑 L2、§6 发版；§7 是诚实的未验项与风险。

---

## 1. 这一轮到底改了什么（按主题，不按时间）

| # | 主题 | 一句话 |
|---|---|---|
| 1 | **C16 判定** | 不签 N/A：E1（直接回复）原来完全不校验锚点 ⇒ 回信会被投进别的 workspace 的会话。已修（E1/E2 都比对锚点，弃边记 `anchor-moved`），并给了 dsh 的 `s8` 夹具 |
| 2 | **命名契约裁剪** | `contract/aimail-contract.json` 从 push 时代清单裁到只剩**命名**值（version 4，4 个键）；端点键、hermes webhook 键全删 |
| 3 | **v1 hermes 退役** | `pysdk/hermes/` 删除，hermes 只剩 `hermes_plugin`（`hermes_agent.plugins` 入口点） |
| 4 | **本地端点退场** | `core/listen.ts`、pi 的监听、dsh 的 `createInboundHandler`、openclaw 的网关内路由、`inbound-notify` 全删；`INBOUND_PATH`/`INBOUND_PORT` 退出契约 |
| 5 | **dsh 单一投递路径** | `mail-hub` 改调 `deliverInbound`（E1/E2/E3 + C16 + C17 + dispatch 行），`routeThread` 删除；SOUL/skills provider 从没被挂载的 `apply()` 移进 hub |
| 6 | **push/CLI 残留清空** | 删 `openclaw/commands.ts`、`core/sdk-ops.ts`、`core/ensure-system.ts`、`core/auto-bind.ts`、pysdk 的 `install.py`/`sdk_ops.py`/`deer-flow/`/`hosts/`；`PyYAML`/`fastapi` 依赖随之消失 |
| 7 | **绑定来路（激活章）** | 绑定由激活写入 `binding_source`+`activated_at`；布防时校验七字段+章，**不通过即不布防**（无开关） |
| 8 | **网关否决回灌** | 拉取 auth-fatal（401/403）后，该地址在本进程内**不再布防** |
| 9 | **锚点跨仓见证**（核心） | 激活登记 `anchor_id = sha256(lower(email) + "\0" + 锚点)`；每次拉取带 `X-Aimail-Anchor: email;hash,…`，网关比对不符 ⇒ 403 `anchor_mismatch` 且不投递 |
| 10 | **本地锚点账本取消** | `anchor.json` 那套（含 `judgeAnchor`）删除——有网关见证后它是多余的；`move` 改为 `re-activation-required` |
| 11 | **deerflow 投递回读** | deerflow 拿到 run_id 后回读 run 记录，`error/timeout/interrupted` ⇒ 不 ack；401/403/404 回读不可用 ⇒ 记 `landing-unverified` 放行 |

## 2. 两个仓的改动清单

### A. `aimail`（SDK / 契约 / 夹具）

**契约**
- `docs/aimail-host-sse-contract-v2.md` → **v2.4**：§9.2 I2 记账义务、§13.3 改为"换锚点必须重新激活 +
  锚点标识协议"、§16 C16 判定范围、§18 相邻资产三步裁剪记录。

**TypeScript（`tssdk/packages/agent-aimail`）**
- 新增：`core/anchor-id.ts`（`anchorId`/`anchorHeader`/`ANCHOR_HEADER`）、`core/binding-provenance.ts`
  （激活章 + 网关否决集合）。
- 路由：`runtime/routing.ts` 的 E1 现在查锚点，带出 `anchorMoved{edge,space}`；`session-router.routeThread` 删除。
- dsh：`dsh/inbound.ts` 变纯投递核心（`deliverInbound`/`deliverPulled`/`installDshInboundProviders`）；
  `dsh/mail-hub.ts` 的 `deliver()` 调它，`start()` 装 provider。
- 端点：删 `core/listen.ts`、`core/inbound-notify.ts`、`core/sdk-ops.ts`、`core/ensure-system.ts`、
  `core/auto-bind.ts`（→ `system-config.ts`）、`core/ensure-binding.ts`、`openclaw/commands.ts`；
  pi/openclaw/dsh 的监听与 HMAC 路径删除。
- 激活：`activateAddressCode` / `activateAddressCodePersist` / `provision.activateAddressCode` 带 `anchor_id`；
  pi 用会话 cwd、openclaw 用 agent workspace、dsh 用 `workspace.path` 在**激活之前**传入。
- 拉取：`buildAgentSystemClient(..., members)` 把 `X-Aimail-Anchor` 放进 `extra_headers`（三条腿共用）。
- `move`：`aimailAddress` 与 dsh 工具都返回 `re-activation-required`，不再本地改写锚点。

**Python（`pysdk`）**
- 删：`hermes/`、`deer-flow/`、`hosts/`、`install.py`、`sdk_ops.py`、`aimail_base.ensure_system`。
- 新增：`v2/anchor.py`（`anchor_id`/`anchor_header`）。
- 激活：`activate_address_code(... anchor_id=)` 发送；`activate_address_code_persist` 计算并带上。
- 拉取：`v2/pull_client.build_agent_system_client(..., members)` 带 `X-Aimail-Anchor`。
- 布防：`v2/binding.is_armable` 判激活章 + 网关否决集合（不再有本地锚点账本）。
- deerflow：`runtime.py` 的落地回读（见主题 11）。
- `pyproject.toml`：force-include 与 extras 同步（无 `install.py`/`deer-flow`/`hosts`；依赖只剩可选 docs）。

**夹具 / 门禁**
- 新增 `tests/host-acceptance/v2/cases/s8_anchor_isolation.sh` + `lib/s8_drive.py` + `hosts/dsh.sh` 的 `S8` 钩子；
  `run-matrix.sh` 注册 `s8`；`GATE.md` 增 `s8`/`DSH-ONE-PATH`/`S8-HOOK-1` 行。
- `tests/contract/check-legacy-push-scope.py` → **no-local-endpoint 门禁**：端点键/常量、监听符号、v2 面端点字面值、
  v1 路径（`pysdk/{hermes,deer-flow,hosts}`、`install.py`、`sdk_ops.py`）任一复现即红。
- `check-contract-single-source.py`：4 个命名键；`check-docs-consistency.py`：不再比对端口。

### B. `aimail-gateway`（Rust，3 文件 +294 行）

- `src/core/storage.rs`：新表 `address_anchor(email_address PK, anchor_id, updated_at)` +
  `set_address_anchor` / `get_address_anchor` / `list_agent_system_anchors`（**不改** `domain_addr_meta`）。
- `src/core/api/activation.rs`：`activate-address-code` 接受 `anchor_id`（64 hex 校验）、入库、**响应回显**。
- `src/core/api/http.rs`：`parse_anchor_claims` + `enforce_anchor_binding`，挂在
  `agent_system_stream` / `list_agent_system_pending` / `ack_agent_system_pending`；不符 ⇒ 403 `anchor_mismatch`。
- 兼容策略：**未登记锚点的地址放行**（老 key 轮换），因此可分批上线；全量登记后可一行收紧为"未登记即拒"。

## 3. 关键不变量（复检时按这几条判）

1. **只有"投递进会话"才 ack**；拒绝一律不 ack（`skip`/raise）。
2. **没有任何本地监听端点**：不绑端口、不开路由、不校验 HMAC push body、不上报 `--inbound-live`。
3. **绑定只由激活产生**：七字段 + 激活章；缺一不布防。
4. **锚点关系由网关见证**：`sha256(lower(email)+"\0"+锚点)` 在激活登记、每次拉取校验；不符 403 且不投递。
5. **dsh 只有一条投递路径**：`mail-hub` → `deliverInbound`（E1/E2/E3 + C16/C17 + dispatch 行）。
6. **换锚点必须重新激活**：`move` 返回 `re-activation-required`。

## 4. 复检点（不需要 L2 的部分）

```bash
# SDK / 契约 / 门禁
cd /home/ubuntu/aimail
bash tests/sdk/l0-gate-tests.sh          # 期望 PASS（含 no-local-endpoint、契约单一来源、docs 一致性、zero-bridge）
python -m pytest tests/sdk -q            # 期望 438 passed
cd tssdk && npx vitest run               # 期望 510 passed / 58 files
npx tsc --noEmit -p tsconfig.json        # 期望 clean
python -m pip wheel . -w /tmp/whl        # 期望构建成功；wheel 内无 aimail/{install,sdk_ops,hosts,deer-flow,hermes}

# 网关
cd /home/ubuntu/aimail-gateway
cargo test --offline --lib               # 期望 433 passed（anchor 表 3 + header 解析 3 为本轮新增）
```

**请重点抽查的四件事**

1. **锚点协议端到端**（网关 + SDK 各一半）：本地起网关 → 用激活码绑一个地址（带 `anchor_id`）→
   拉取带 `X-Aimail-Anchor` 成功 → **改绑定的 `workspace_path`** 后同一拉取应 403 `anchor_mismatch`；
   恢复后再次成功。
2. **激活章**：删掉绑定里的 `binding_source` ⇒ 布防日志出现 `binding REFUSED — provenance not proven`，
   且该地址**收不到信**（不是静默降级）。
3. **端点退场**：起 dsh/pi/openclaw 后 `ss -ltnp | grep 9099` 为空；`~/.dsh`/`~/.pi` 下没有监听进程。
4. **dsh 单一路径**：`grep -c "decideInboundDelivery" src/dsh/*.ts` 只在 `inbound.ts`；
   `mail-hub.ts` 里出现 `deliverInbound(` 而不出现 `routeThread`。

## 5. L2 门禁（必须跑，开发未跑）

> 开发侧**没有**重跑 L2；以下是期望值与原因，任何与期望不符都请直接判红并回单。

```bash
cd /home/ubuntu/aimail
bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host dsh --stages "s1 s2 s3 s6 s7 s8"
bash tests/host-acceptance/v2/run-matrix.sh --sdk ts --host pi  --stages "s1 s2 s3 s6 s7"
bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host hermes   --stages "s1 s7"
bash tests/host-acceptance/v2/run-matrix.sh --sdk py --host deerflow --stages "s1 s5 s7"
```

| 阶段 / 宿主 | 期望 | 变化点（与上一轮相比） |
|---|---|---|
| dsh `s1 s2 s3` | PASS | `s2` 断言里"无本地监听"现在是**真的**（监听已删） |
| dsh `s6` | PASS | 现在测的是**出货链路**（`mail-hub` → `deliverInbound`） |
| dsh `s7`（C17） | **应出现 `reason: session-gone`** | 以前在打包 dsh 上不可能出现（旧 hub 不写 dispatch 行） |
| dsh `s8`（C16，新增） | **应出现 `anchor: anchor-moved`**，`s8_anchor.json.leaks=false` | 新夹具；`rewritten=0` 或缺文件一律 GAP，不记绿 |
| pi `s1 s2 s3` | PASS | 无监听、无 `--inbound-live` 回报 |
| pi `s6/s7` | `N/A`（`HOST_CAN_ALLOCATE_SESSION=0`） | 与上轮一致 |
| hermes `s1` | PASS | 只剩 `hermes_plugin`；`pip install` 即有 |
| hermes `s7` | `RISK-ACCEPTED` | 与上轮一致（owner 已签署） |
| deerflow `s1 s5` | PASS | 依赖：`anchor_id` 现在会随激活提交（网关需是本轮版本） |
| deerflow `s7` | 期望 PASS（`probe`） | 落地回读在跑；若回读 401/403/404 会记 `landing-unverified` 放行（日志可见） |

**先起网关**：`deerflow`/`hermes` 的激活现在会带 `anchor_id`，用上一版网关不会被记录（放行但不校验）。
本轮的网关必须在场，否则主题 9 实际上没有被验证。

## 6. 发版清单

版本建议（owner 定；以下是依赖关系，不是决定）：

| 包 | 当前 | 建议 | 理由 |
|---|---|---|---|
| `agent-aimail`（PyPI，`pyproject.toml`） | 0.3.0 | **0.4.0** | 破坏性：删 `install.py`/`sdk_ops.py`/`deer-flow`/`hosts`/`hermes` v1；`ensure_system` 删除；`move` 语义变更 |
| `agent-aimail`（npm，tssdk） | 0.5.2 | **0.6.0** | 破坏性：删 `sdk-ops`/`ensure-system`/`auto-bind`/`inbound-notify`/`listen`、openclaw 命令面；`move` 语义变更 |
| `aimail.pi-desktop`（插件） | 0.2.1 | 0.2.2 | 无行为改动，跟随 core |
| gateway | 1.0.16 | **1.0.17** | 新表 + 激活字段 + 拉取校验（向后兼容） |

发版前必做：

1. 网关先进生产（向后兼容：无 `anchor_id` 的激活、无锚点头的拉取都放行）。
2. `scripts/bump.sh`（若在用）统一版本 → `bash tests/sdk/l0-gate-tests.sh` → `python -m pip wheel .`
   → 在**干净 venv** 里 `import` 冒烟（`aimail.v2.binding`、`aimail.hermes_plugin`、`aimail.deerflow_plugin` 可导入；
   `aimail.install` / `aimail.sdk_ops` / `aimail.deer-flow.*` 必须 `ModuleNotFoundError`）。
3. tssdk：`pnpm build` + `pnpm pack` → 在空目录 `npm i <tgz>` 后 `require('agent-aimail/core')` 冒烟。
4. 插件包：`node scripts/build.mjs` → `dist/` 冒烟（`scripts/smoke.mjs`）→ 交给打包流程。
5. 发布后确认：PyPI `Requires-Dist` 无 PyYAML/fastapi；npm `dist/*/commands.js` 不存在。

## 7. 诚实的未验项 / 风险（请据此分配注意力）

| 项 | 状态 | 影响 |
|---|---|---|
| **L2 全量** | **开发未跑**；只有单测与门禁绿 | 本交接的核心目的就是让 QA 跑这一轮 |
| **锚点端到端** | 两侧单元测试各自绿；**未在真网关+真宿主上联调过** | 401/403 语义、header 大小写、逗号分隔的实际行为需一次实测（§4 第 1 条） |
| **PI-Desktop 面** | 未发送 `X-Aimail-Anchor` | 它的 key 来自插件设置（用户手填），不走激活链；本轮不动。若 owner 要求一致，需要给桌面插件加"项目路径 → 锚点"并走同一协议 |
| **网关"未登记放行"** | 有意保留 | 分批上线的过渡态；全量登记后应收紧（一行判断） |
| **`anchor.json` 历史文档** | `docs/qa/ANCHOR-RELATION-2026-11-25.md` 顶部已标注"已取消" | 阅读时注意那是历史方案 |
| **deerflow 回读** | 真实宿主上未验证（容器里 uvicorn 未启动时无法复验） | `s7` 会给出证据；401/403/404 时记 `landing-unverified` 而非红 |

## 8. 相关文档索引

- 契约：`docs/aimail-host-sse-contract-v2.md`（v2.4，§13.3 锚点协议）
- C16 裁定与实现：`docs/qa/DECISION-C16-ANCHOR-ISOLATION.md`
- 命名契约裁剪：`docs/qa/NAMING-CONTRACT-TRIM-2026-11-25.md`
- v1 hermes 退役：`docs/qa/RETIRE-V1-HERMES-2026-11-25.md`
- dsh 单路径 + 端点退场：`docs/qa/UNIFY-DSH-DELIVERY-AND-DROP-ENDPOINTS-2026-11-25.md`
- push/CLI 残留清空：`docs/qa/KEEP-ONLY-SSE-V2-2026-11-25.md`
- 绑定来路（激活章 + 网关否决）：`docs/qa/BINDING-PROVENANCE-2026-11-25.md`
- **锚点跨仓见证（本文的核心）**：`docs/qa/ANCHOR-CROSS-REPO-2026-11-25.md`
- 夹具口径：`tests/host-acceptance/v2/GATE.md`
