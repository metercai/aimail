# AIMail V2 — SDK L2/L3 质检结论（当前状态）

- 日期：2026-11-23（本轮更新）
- 结论：**HOLD**
- 被测：`agent-aimail 0.5.1`（dev 构建）· gateway **v1.0.11 已发布二进制** · 真实宿主镜像 dsh 0.2.2-rc.1 / pi 1.5.2 / openclaw 2026.11.5 · 小米 `mimo-v2.6-flash`
- 基线 tag：`qa-l2l3-baseline-2026-11-23`

---

## 1. 门禁状态（实跑口径）

| 宿主 | S1 激活 | S2 拉起 | S3 投递 | S4 多地址 | S5 生命周期 | 负例 | 结论 |
|---|---|---|---|---|---|---|---|
| dsh | 8/8 ✅ | 2/2 ✅ | ⛔ ING-1 | 2/2 ✅ | 2/2 ✅ | 1/1 ✅ | **门禁级**（除 S3） |
| pi | 7/7 ✅¹ | 2/2 ✅¹ | — | 单地址拒绝 ✅¹ | — | 2/2 ✅¹ | ¹ 仅当 `typebox` 声明为 peer（REQ-11）；当前发布配置下红 |
| openclaw | 7/7 ✅ | ❌ 未 arm | — | — | — | — | 插件加载/注册正常，拉取未建立 |

`PASS/RED/GAP/SKIP_LEGACY` 口径见 `tests/host-acceptance/v2/GATE.md`；`RED>0 或 GAP>0` ⇒ 退出码非 0。

**L3**：代码就绪（钉版 + sha256、宿主版本断言、dsh×4 判定 / hermes `SKIP_LEGACY`），**单格尚未实跑**；
按 Owner 规则 **L3 仅由验证方在发版后执行，开发不得运行、不得以 L3 作为交付依据**。

---

## 2. 产品阻断项（交开发）

### ING-1（P1，gateway）SMTP 入站被转发到出站 relay，未本地入库
外部发件人 → 本地 pull 地址：SMTP 250 接受，但 `emails` 0 行、`pending_deliveries` 0 行，同一封信出现在 `[relay] smtp_server` 的 spool。
四层排查：① 发件人非内部 host 地址；② 白名单两条在库；③ `emails` 0（入站未完成）；④ `pending_deliveries` 0。
**判据**：`emails` 1 行 → `pending_deliveries` 1 行 → 宿主流取走 → ack 晚于 turn；sink spool 不得出现入站原信。

### REQ-11（P1，tssdk 打包）`typebox` 声明冲突
- `dependencies: typebox` → pi 拒绝加载：`Host-provided extension packages must be declared in peerDependencies with a "*" range, not dependencies: typebox`（同时报无 API key）。
- `peerDependencies: "*"` → pi 绿（11/11），但 **dsh 回归**：`Cannot find package 'typebox'`。
- 建议修法（三选一）：构建期内联；去运行期依赖改产 JSON Schema；双入口。
- **判据**：pi 与 dsh 的 L2 同时绿。

其余条目（REQ-2…REQ-10）见 `docs/qa/PRODUCT-REVISION-REQUEST.md`，均含现象/位置/修法/判据。

---

## 3. 已关闭/已撤回

| 项 | 状态 |
|---|---|
| B1 `isAgentScopeBinding` V1 前缀 | 0.5.1 已修（dsh 实测 `SSE reconcile: 1 … from 1 binding`） |
| B2 pull client 缺 aggregate path/header | 0.5.1 已修（`buildAgentSystemClient`） |
| B4 DSH 会话无工具帧 | **撤回（我方夹具）**：装解包目录缺 `typebox`；未设 `DSH_PERMISSION_MODE=danger-full-access` |
| B5 V2 自助激活不写 manager/白名单 | v1.0.11 已修（`d5afefa`，实测 manager + 两条白名单在库） |
| F1 `aimail_address` 回传 `raw_key` | 未修（P2，见 REQ-5） |

---

## 4. 本轮修掉的夹具缺陷（无产品含义，留痕避免重复踩）

1. 插件安装必须用 **tarball**（装解包目录缺依赖 → 插件不加载）
2. dsh 必须 `DSH_PERMISSION_MODE=danger-full-access`（否则工具调用挂审批）
3. 断言去宿主化：dsh 不持久化 `agent_system_id`；arm 日志为 `SSE reconcile`；openclaw 侧日志靠 grep 而非 tail
4. `docker run … -lc '...'` 内层脚本禁止出现单引号（会提前截断脚本）
5. 多轮 prompt 必须**同一会话**；等待按状态变化而非固定 sleep
6. L2 gateway fixture 与 L3 cell 对齐（hostname=测试域、域 id=域名、注入走网关 SMTP 口、钉版+sha256）

---

## 5. 复验顺序

1. ING-1 修复 → `run-matrix.sh --host all --stages "s1 s2 s3 s4 s5 neg"`
2. REQ-11 修法落地 → pi 段转绿
3. openclaw 拉起（S2）修复 → 其余段补齐
4. 验证方实跑 L3 `run-v2.sh`（dsh×4）→ 出签字结论
