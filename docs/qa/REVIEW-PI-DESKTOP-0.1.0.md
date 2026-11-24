# 评审意见 — PI-Desktop 插件 0.1.0（.piplug 打包与门禁）

- 日期：2026-11-24
- 对象：`docs/qa/HANDOFF-PI-DESKTOP-PLUGIN.md` + 工作树批次
- 坐标：`0d2b635` · tag `plugin-v0.1.0` · CI `plugin-gate` run **45422540935 (success)**
- 结论：**GO — 已发布到 release（产物与本地签署值逐字节一致）**

```
VERDICT: PASS (artifact released)
TARGET : aimail.desktop 0.1.0 @ 0d2b635 · tag plugin-v0.1.0 · CI 45422540935
ARTIFACT: aimail.desktop-0.1.0.piplug  125,423 B
          sha256 ab5de3145239b51b3edc83215d4fa8c31807b8a77173768bd37dacc5ee6637e9
          release asset == 本机构建 == CI 构建（同一 hash，跨 node 22/24 可复现）
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
