# 评审意见 — PI-Desktop 插件 0.1.0（.piplug 打包与门禁）

- 日期：2026-11-24
- 对象：`docs/qa/HANDOFF-PI-DESKTOP-PLUGIN.md` + 工作树批次
- 当前版本：**0.1.2** · tag `plugin-v0.1.2` @ `1ddfa0d` · CI `plugin-gate` **45502986902 (success)**
- 历史：0.1.0（首个可发产物）→ 0.1.1（加版本戳，但戳里含墙钟时间，产物不可复现，已标记 superseded）
  → **0.1.2（戳改用 git 提交日期，本地与 CI 逐字节一致）**
- 结论：**GO — 0.1.2 已发布；封装/发版由验证方负责，SDK 版本戳已内置在产物里**

```
VERDICT: PASS (artifact released)
TARGET : aimail.desktop 0.1.0 @ 0d2b635 · tag plugin-v0.1.0 · CI 45422540935
ARTIFACT: aimail.desktop-0.1.2.piplug  125,7xx B
          sha256 cf1d5388abb3d7e1f3eff7c8c30af2364835aa987a14e0faa5bcd97e06184d58
          release asset == 本机构建 == CI 构建（同一 hash；同 commit 连构两次亦同）
          build.json ⊃ {"sdk":"agent-aimail","sdkVersion":"0.5.2","gitSha","sourceDate"}
UNVERIFIED: 真机安装（权限授予 + service 启动）—— 见 F-3
```

**发布回读**：从 release 下载 `aimail.desktop-0.1.0.piplug`，`sha256sum` = `ab5de314…6637e9`，与我在本机签署的值、
以及 CI artifact 完全一致。附件另有 `.piplug.sha256`（94 B）。

## 1. 我自己跑出来的数（不是复述交接）

| 检查 | 我的结果 |
|---|---|
| `vitest run`（tssdk 全量） | **65 files / 568 tests passed**（与交接一致） |
| 本面单测（desktop 37 + 注入传输 4） | **41 passed** |
| `tsc -b packages/agent-aimail` | clean |
| 插件包 `tsc -p --noEmit` | clean |
| `scripts/build.mjs` | `build: aimail.desktop@0.1.0 → main.js` |
| `scripts/smoke.mjs` | 两个场景都成立：投递→ack；**拒绝→不 ack**（脚本内断言 `if (calls.ack.length) failure(...)`，还断言了会话复用与确定性 message id） |
| 宿主源码对照 | 6 项权限全部存在于 `PLUGIN_PERMISSIONS`；`session import accepts one item`（plugin-runtime.ts:1061）、`message role is invalid`（:1095）印证交接 §3.1 的两个形状 |
| 产物 | 3 个条目位于归档根：`main.js` 122,475 B、`manifest.json` 2,609 B、`package.json` 25 B |
| 压缩方法 | 全部 `method=0`（store-only），宿主可解 |
| `dist/package.json` | `{"type":"commonjs"}` —— 我另做了 `require('./main.js')` 实测，导出 `onLoad,onUnload` |
| sha256 | `ab5de314…6637e9`，归档字节计算，并写入 `.piplug.sha256` |

## 2. 我新增的"判产物"门禁（本轮交给我做的那一半）

- `tests/release/pack-piplug.py`：**不能用 `zip -r`**（默认 deflate，宿主直接拒）。脚本以 store-only 写包，随后**重新打开归档自证**：method 全 0、`manifest.json` 在根、≤2000 条目、≤50 MiB、无符号链接、禁入项（`.git/`、`node_modules/`、`.env*`、`.npmrc`、`*.pem`、`*.key`）、`manifest.version == package.json version`、sha256 取归档**字节**、命名 `<id>-<version>.piplug`。
- `tests/release/gate-pi-desktop-plugin.sh`：install → 类型 → 本面单测 → build → smoke → pack，全程 fail-closed。**smoke 那步要求"拒绝场景"存在**：输出里没有 refusal/no-ack 证据就判失败——看不见"拒绝被当成功"的门禁，对这个插件等于没有门禁。
- `.github/workflows/plugin-gate.yml`：插件相关路径变更即跑；打 `plugin-v*` 时把 `dist-piplug/*` 附到 release。

## 3. 评审发现（不影响本次产物可发，但必须记录）

**F-1 交接 §2 的改动清单不完整。** 工作树里还有交接未列出的改动：`src/core/address-code.ts`、`src/pi/index.ts`、`src/runtime/index.ts`、`src/runtime/session-router.ts`，以及新文件 `src/core/host-capabilities.ts`、`src/runtime/routing.ts` 和 3 个新增测试（`address-single-host`、`runtime-direct-reply`、`runtime-routing`）。这些已随本批一起复检（全量 568 绿），但**清单与事实不一致**会让人误判批次边界，建议下批补齐。

**F-2 交接 §9 的 npm 说法与事实不符（需 owner 决定）。** §9 写"npm 上仍只有 `agent-aimail`（本插件需要的 `./desktop` 入口已在其 `exports` 里）"——实际上 `./desktop` 是**本批新增**的 exports，已发布的 `agent-aimail@0.5.2` **没有**它。因为插件把 `agent-aimail` 打进单文件 bundle，这不影响 `.piplug` 可用；但若有源码消费者 `import 'agent-aimail/desktop'`，必须等一次 npm 发版（0.5.3）。**我不擅自升版**：是否发 0.5.3 属版本策略，请你定。

**F-4 门禁与 CI 环境不一致（已修，属验证方）。** 首两轮 CI 红在
`tool-registry.ts TS18046 'p' is of type 'unknown'`，而同一 commit 本地干净。逐步排除：
node 22 + pnpm 9.15.9 + `--frozen-lockfile` 容器内 **clean**（→ 不是 node/pnpm/lockfile）；
删 `.tsbuildinfo` + `--force` 仍 clean（→ 不是增量空跑）；跑包自己的 `gen-tool-spec.mjs` 后仍 clean
（→ 不是生成代码过期）。真因在我的门禁第一步：`pnpm install --offline || pnpm install` 在无 store 的
CI 上失败并留下半写 `node_modules`。改为**只装一次 `--frozen-lockfile`**，并按产品自己的构建顺序
`gen-tool-spec.mjs && tsc -b` 执行后，CI 转绿且产物 hash 不变（说明改的是判据，不是产品）。

**F-3 未验证项（交接 §8 已诚实标注，我沿用）**：真机安装（插件中心导入 `.piplug`、6 项权限授予、`contributes.services` 的启动时机）。我的门禁只能证明**归档形状/打包约束/require 可加载/冒烟行为**，不能证明宿主装得上、服务起得来。风险集中在这条，发版说明应保留。

## 4. 发版记录与回滚

- 产物：`plugin-v0.1.0` release 附 `aimail.desktop-0.1.0.piplug` + `.piplug.sha256`（由 `plugin-gate` 上传，工单见 CI）。
- 回滚：删除 release/tag 即可（本插件不进 npm，不存在"已发布不可撤回"）；对已安装用户，宿主侧卸载插件。
- 版本策略：插件 `0.1.0` 与 `agent-aimail 0.5.2` 相互独立；`agent-aimail` 是否升 `0.5.3`（为 `./desktop` exports）留待 owner。

## 5. 交付形态：桌面是独立产物，不是 npm 上的同一个包（验证方实测结论，非 Owner 确认）

**以下是我从代码与夹具实测得出的事实陈述**（不是政策决定；若要改，属架构取舍）：

这是**两条供应链**，不是"同一个包的不同装法"：

| 宿主 | 安装单位 | 版本来源 | 升级方式 |
|---|---|---|---|
| dsh / pi / openclaw | npm 包 `agent-aimail@<sdk版本>` | npm 版本号 | 宿主插件命令装新版本 |
| **PI-Desktop** | **`.piplug` 归档**（`aimail.desktop@<插件版本>`） | 插件版本号（0.1.0） | 从 release 取件，宿主插件中心导入 |

所以：
- `.piplug` **专供 PI-Desktop**，宿主只认这种归档（`manifest.json` 在根 + store-only + `require`-able `main.js`）；
- 其余宿主装的是**同一个 npm 包**，升级粒度是 SDK 版本；
- 插件包 `private: true`，不进 npm；`agent-aimail` 的 `./desktop` 导出是给**源码消费者**的，`.piplug` 不需要它（SDK 已被 esbuild 打进 `main.js`）。

**由此产生的配对义务（本轮暴露的真实缺口，记 REQ-16）**：`.piplug` 里没有 SDK 版本痕迹——
我核过产物：`main.js` 内 `agent-aimail` 出现 40 次，但 `0.5.x` 版本串 **0 次**。于是
"这个 `.piplug` 到底内嵌了哪个 SDK"只能靠 git 反查。本版可反查：tag `plugin-v0.1.0` @ `0d2b635`
的 `agent-aimail` 版本为 **0.5.2**（即 `.piplug 0.1.0` ⊃ `agent-aimail 0.5.2`），已写入 tag 注记。

REQ-16（建议，成本极低）：构建时把 `{sdk:"agent-aimail", sdkVersion, gitSha, builtAt}` 写成
`build.json` 放进 `dist/`（或 `define` 注入 bundle），门禁断言其存在且与 lockfile 一致；
发版注记自动带 `⊃ agent-aimail@<sdkVersion>`。这样"产物 ↔ SDK"的配对不再依赖 git 考古。

## 6. 版本戳与可复现性（由验证方实现，不是开发任务）

Owner 指出封装发版属验证方职责，因此版本戳做在**发布工装**里，未改产品源码：

- `pack-piplug.py --sdk-package <agent-aimail/package.json>` 把
  `{"plugin","pluginVersion","sdk","sdkVersion","gitSha","sourceDate"}` 作为 `build.json`
  写入归档根（store-only，与其他条目同法），并在重新打开时校验：缺戳、戳的 pluginVersion 与
  manifest 不一致、给了 `--sdk-package` 却没有 `sdkVersion`，一律 pack 失败。
- 门禁断言戳里的 `sdkVersion` 与 workspace 实际构建用的一致（输出 `✓ stamped sdkVersion=0.5.2`）。

**我自己犯的错，也记在这里**：0.1.1 的戳用了墙钟 `builtAt`，导致同一 commit 每次都产生不同字节
（实测本地 `4e460898…` vs CI `53d5eb06…`）；0.1.2 改用 `git log -1 --format=%cI` 的提交日期，
同 commit 连构两次 hash 相同，且 CI 与本地一致。0.1.1 未删除，release 注记标为 superseded——
已发布的产物不做静默消失。
