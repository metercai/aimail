# 发版标准（验证方制定，2026-11-25）

本文件是**放行判据**，不是建议清单。任何一条不满足即为 HOLD；豁免必须由 owner **书面**签字并
写进对应 `GATE.md` 的已知项，否则一律按"未满足"处理。

适用：`agent-aimail`（npm）、`agent_aimail`（PyPI）、`aimail.pi-desktop`（.piplug）、gateway 二进制。

---

## R0 通用前提（任何一项不满足直接 HOLD）

| # | 条件 | 判据 | 证据形式 |
|---|---|---|---|
| R0.1 | 干净的发布坐标 | 打包/构建路径无未提交改动（`pack-piplug --dirty-paths` 拒绝、构建前 `git status` 为空） | 提交 SHA + 该 SHA 的重建结果 |
| R0.2 | 产物可复现 | **从发布提交重建**得到与发布件逐字节相同的 hash（本地与 CI 一致） | 两侧 sha256 并列 |
| R0.3 | 工具链固定 | 门禁只使用仓库依赖的编译器/测试运行器（不靠 `npx` 解析），并打印版本 | 门禁输出中的 version 行 |
| R0.4 | 跳过不算成功 | 发布型 job 若因"版本已存在"等跳过，必须**失败**（显式 `allow_already_published=true` 才放行） | job 结论 + 日志行 |
| R0.5 | 按产品自己的构建顺序 | 例如 `codegen && tsc -b`，不得用裸 `tsc -b` 代表产品构建 | 门禁脚本中的命令 |

## R1 静态与单测（L0）

`bash tests/sdk/l0-gate-tests.sh` → `[L0] PASS`，含：tssdk build+vitest、`pytest tests/sdk`、
契约单一来源棘轮、docs 一致性、zero-bridge、file-ownership。

## R2 宿主验收（L2）全绿

对**每一个受支持的宿主 × SDK** 运行完整阶段，要求 `RED=0 GAP=0 SKIP_LEGACY=0`：

```
ts: dsh / pi / openclaw    py: hermes / deerflow
--stages "s1 s2 s3 s4 neg"（s5 生命周期按需；s6/s7 按 R3/R4）
```
每个宿主独立旅程，不合并（`GATE.md` "Journey scoping"）。**GAP 不等于绿**：GAP 必须写明归属
（夹具缺陷 / 宿主能力缺口），属于后者时按 R7 走豁免。

## R3 C18 判定可解释（单地址宿主必做）

`s4` 必须出现**结构化**拒绝证据（`failed=wake|L3`）。仅模型转述（paraphrased）**不足以**满足
R3，只可作为"待收紧"记录。

## R4 C17 降级（至少 1 个宿主证明）

`s7`：删除 thread 命中会话后，对同一 thread 的回信仍被 **ack**，落到**新会话**，且 thread root 未丢。
- `acked=false` 或 `used==deleted` ⇒ **RED（产品缺陷：审批回信会永久挂住）**；
- 无 `s7_gone.json` ⇒ GAP（钩子缺失），不满足 R4；
- `reason` 必须含 `session-gone`。**当前产品无该字符串的生产者**（`runtime/routing.ts` 只返回
  `direct|followup|new`），因此 R4 在补齐 reason 之前**只能满足到"ack + 重挂"**，reason 部分需
  owner 决定：补产品字段，或书面降级该项。

## R5 契约与实现一致

`tests/sdk/test_v2_contract_surface.py` 绿；契约 v2.3 的 §9/§11/§16 断言在实现中可指认（给出文件:函数）。

## R6 无宿主 patch、无 CLI 安装路径

发布物与文档中不得出现 `agent-install.sh`、"Follow the step-by-step" 等旧路径；安装只走宿主插件机制。

## R7 插件产物（.piplug 专属）

| # | 条件 |
|---|---|
| R7.1 | `manifest.id` 通过宿主模式 `^[a-z0-9]+(\.[a-z0-9_-]+)+$`（首段无连字符）；打包含该检查 |
| R7.2 | 归档 **store-only**（method 0）、`manifest.json` 在根、≤2000 条目/≤50 MiB/无符号链接、禁入项为 0 |
| R7.3 | `dist/package.json` = `{"type":"commonjs"}`，且 `require('./main.js')` 可取到导出 |
| R7.4 | 归档内 `build.json` 记录 `{plugin, pluginVersion, sdk, sdkVersion, gitSha, sourceDate}`，且 `sdkVersion` 与 workspace 一致 |
| R7.5 | 冒烟两场景都成立：投递→ack；**拒绝→不 ack** |
| R7.6 | 由 CI 在 tag 上产出并附到 release；**发布后 tag 不再移动** |

## R8 真机安装（每版一次，人工）

按 `docs/qa/RUNBOOK-PI-DESKTOP-INSTALL.md` 8 步执行并留证。宿主禁止自动化安装
（`pluginInstallFromPath` 等在 `MCP_CONTROL_BLOCKED_CHANNEL_KEYS` 中），因此这是**不可自动化**项：
不执行则发版说明必须写明"真机未验证"，且不得宣称"用户可用"。

## R9 L3（agent 宿主）

`tests/l3/` 4 平台 × {dsh, pi} 矩阵由 verifier 执行；**单次绿不作为证据**，要求连续两次全绿或给出
翻转率与归属（见 `tests/l3/GATE.md`）。

---

## 豁免规则

1. 只能由 **owner** 书面签字，写明：豁免项 / 理由 / 有效期 / 补偿措施；
2. 必须落到对应 `GATE.md` 的"已知项"，并带编号（如 `S7-HOOK-2`）；
3. 豁免项在后续版本的 R2 中**仍然会被跑到**，不得从矩阵中删除；
4. 不得以"已被单测覆盖"为由豁免**会导致数据丢失或永久挂住**的项（如 R4 的 ack 语义）。

## 当前状态（2026-11-25，随每次发版更新）

| 判据 | 状态 | 说明 |
|---|---|---|
| R0.1–R0.5 | ✅ 已具备 | 见 `tests/release/*`、`plugin-gate.yml`、`publish.yml` 守卫 |
| R1 | ✅ | `[L0] PASS`（本轮改动后重跑） |
| R2 | ⏳ 待跑 | 需要 5 个宿主的完整回归（等 owner 批准启动） |
| R3 | ⏳ | `s4` 已接受结构化证据；需在单地址宿主（pi/hermes）实跑确认 |
| R4 | ⏳ 部分 | dsh 钩子已实现并首跑；`reason=session-gone` 无产品生产者（见 R4 说明） |
| R5 | ✅ | 契约测试绿 |
| R6 | ✅ | 契约面测试守 |
| R7 | ✅ | `aimail.pi-desktop-0.2.1` 全部满足（hash 本地==CI） |
| R8 | ❌ 未执行 | 需人工在真机按 runbook 走一遍 |
| R9 | ⏳ | 8 格矩阵需连续两次全绿 |
