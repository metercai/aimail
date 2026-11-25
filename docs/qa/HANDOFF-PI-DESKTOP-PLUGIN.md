# 交接（PI-Desktop 插件面 → 待打包发版）：请质检复检后打包

日期：2026-11-24
交接方：开发（PI-Desktop 轨道）
接收方：质检 / verifier
范围：`/home/ubuntu/aimail` 的 tssdk 轨道（`tssdk/packages/agent-aimail` 的 `./desktop` 面 + `tssdk/packages/aimail-desktop-plugin`）；pysdk 未动

> **本文用途**：让质检在**不看开发对话**的前提下独立复检这一轮桌面插件面，并据此**打包、发版**。
> owner 已明确：**打包（`.piplug` 与流水线）由质检负责**，开发只交付源码、构建产出与已验证的打包约束（§7）。
> 本文只写可被验证的事实；未执行/不可验证的一律在 §8 标出，不美化。

---

## 0. 一句话结论

```text
开发侧自评：源码 + 构建 + 冒烟全部通过，具备送质检复检与打包的条件
VERDICT(开发自评，非质检结论): PASS
基线 commit 1cd84dc（本轮改动全部在工作树，未提交）
单测    tssdk 全量：568 passed / 65 files（其中本轮新增 41：desktop 37 + 注入传输 4）
静态    agent-aimail `tsc -b` clean；插件包 `tsc -p --noEmit` clean
冒烟    scripts/smoke.mjs：2/2 ok（真 bundle：拉取→import→投递→ack；拒绝⇒不 ack）
门禁    tests/sdk/l0-gate-tests.sh → [L0] PASS
产物    dist/main.js 122,475 B + manifest.json 2,609 B + package.json 25 B
范围外  真机安装、权限授予、服务守护（需装进宿主，开发不做自助安装）
```

**⚠ 首要事实**：本轮全部改动**都在工作树、未提交**。质检复检对象是"当前工作树"，不是任何 tag。
打包前的第一个动作应是**提交这批文件并打 tag**（§7.4 给了建议顺序）。

---

## 1. 背景与判定：为什么桌面是"多地址（M）"宿主

判据见 `docs/aimail-host-sse-contract-v2.md` §11.1–§11.4（三层判定：**锚定点 / 锚点内隔离 / 会话调用能力**；判定必须来自探测，不得来自引擎名）：

| 判据 | 裸 pi（S） | PI-Desktop（M） |
|---|---|---|
| J1 有 workspace 类容器 | cwd 目录 | `project` 实体（`project/*`、`session.create{projectId}`） |
| J2① 能稳定唤起目标 session | `SessionManager.open()+appendMessage()` 无文件锁 ⇒ `unstable` | `turn/start` 只要求会话在持久层存在（`agent-host.ts:1483 requireSession`），宿主自行加载 + 租约 + `admission:"queue"` + `idempotencyKey` ⇒ `stable` |
| J2② 能创建新 / 唤起当前 | `new_session` / `--session-id` | `session.create`（RACP）/ `session.import`（插件面） |

结论：PI-Desktop 满足 M 的四项前置；裸 pi 因"唤起不稳定"落 S。**同一引擎不同承载形态能力不同**，所以这是探测结论，不是按名字分配的。

---

## 2. 交付物地图（本轮新增/改动）

### 2.1 产品代码（新增）

| `tssdk/packages/aimail-desktop-plugin/manifest.json` | 88 | 插件清单：权限 6 项、`contributes.services`、7 个设置项 |
|---|---|---|
| `tssdk/packages/agent-aimail/src/desktop/delivery.ts` | 356 | 投递端口：能力探测 + 确定性消息 id + `import` 带信建会话 + 空间失效重投 + **一切拒绝皆 skip** |
| `tssdk/packages/agent-aimail/src/desktop/inbox.ts` | 233 | 收件箱：设置校验、thread→session 选择、投递失败不得当成功 |
| `tssdk/packages/agent-aimail/src/desktop/index.ts` | 48 | `agent-aimail/desktop` 出口（两个面一个入口） |
| `tssdk/packages/aimail-desktop-plugin/manifest.json` | 100 | 插件清单：权限 6 项、`contributes.services`、7 个设置项 |
| `tssdk/packages/aimail-desktop-plugin/src/main.ts` | 184 | 插件入口（CJS）：`onLoad` 注册服务 → `startSystemPull`（`mode:'poll'`）→ 投递 / 不 ack 即抛 |
| `tssdk/packages/aimail-desktop-plugin/scripts/build.mjs` | 50 | esbuild 打单文件 CJS；校验 manifest/package 版本一致；写 `dist/package.json{type:commonjs}` |
| `tssdk/packages/aimail-desktop-plugin/scripts/smoke.mjs` | 181 | 用假 `pi` 跑**构建后的 bundle**：正例 + 拒绝例 |

### 2.2 产品代码（改动）

| 文件 | 改动 | 为什么 |
|---|---|---|
| `src/core/gateway.ts` | +29/−4：`GatewayClientOptions.fetchImpl`、`readonly fetchImpl`、`readonly streamCapable`；`request()` 走该缝；`streamProbe()` 在注入传输时直接返回 false | 插件进程没有可用的全局 `fetch`，出口必须走宿主 broker（`pi.net.fetch`），而 broker 只能整包响应 ⇒ 不能假装能开流 |
| `src/core/index.ts` | +1 行：导出 `GatewayClientOptions` / `GatewayFetchImpl` | 插件需要这个类型 |

### 2.3 测试（新增）

| 文件 | 项 | 覆盖 |
|---|---|---|
| `test/desktop/delivery.test.ts` | 23 | 载荷逐字段对齐宿主校验；确定性 id 与幂等；import 的 `imported/skipped` 两种回答；空间 `NOT_FOUND` 重投；缺能力/越界/拒绝信封/抛错 ⇒ skip |
| `test/desktop/inbox.test.ts` | 14 | 设置缺失逐项报错；Message-ID / thread root 提取；thread→session（标题、`externalId`）；查询失败**不得** fork；拒绝不得报 ok |
| `test/core/gateway-injected-fetch.test.ts` | 4 | 注入传输仍是字节载荷、签名不改、状态不覆盖；抛错→status 0；注入时 `streamCapable=false` 且不探流 |

### 2.4 文档

| 文件 | 改动 |
|---|---|
| `docs/aimail-host-sse-contract-v2.md` | **§9 重写为三条映射边**（E1 直属 / E2 thread / E3 分配，E1>E2>E3）；**§11 重写为三层判定**（L1 锚点 / L2 隔离 / L3 调用）；**§11.5 = 创建能力的等价形式：导入**（7 条：一次性完成 / 来源标注 / 幂等锚点 / 写入幂等 / ack 不变 / 容器失效重投 / 首信单飞）；正文不再点名具体宿主（实例在附 E） |
| `docs/pi-host-integration-plan.md` | §11.4 载荷实测表；**新增 §11.6**"插件源码落地；打包由质检驱动"（含 §7 那张约束表） |

### 2.5 依赖与锁文件

`tssdk/pnpm-lock.yaml` **+13 行**：新增 workspace 包 `@aimail/desktop-plugin`（依赖 `agent-aimail`、devDeps `esbuild`、`typescript`）。无新增第三方运行时依赖。

### 2.6 形态：代码统一管理，产物按宿主分开（owner 2026-11-24 决定）

这是**有意的分层**，不是误放——质检复核时请按此判断，不要要求把 `desktop/` 搬进插件包：

| 层 | 归属 | 说明 |
|---|---|---|
| 契约级逻辑 | `agent-aimail`（npm 包） | 载荷形状、确定性 id 幂等、ack 语义、thread→session 规则。与宿主无关，和 `./pi`、`./dsh`、`./openclaw` 共用同一套类型与门禁测试 |
| 宿主接线 | `@aimail/desktop-plugin`（独立产物） | 只做 `pi` API → SDK 的转接，构建时把 `agent-aimail/{core,desktop}` 打进 `.piplug`，**不走 npm 分发** |

同一个 npm 包里放多个面是既有形态：`package.json` 用 `pi.extensions`（裸 pi）、`openclaw.extensions`（openclaw）、`dsh.bundle.patch`（dsh）分别声明，各宿主各取所需；PI-Desktop 的加载机制是 `manifest.json` + `main.js`，因此产出物必然是另一个文件，但**代码仍只有一份**。

已核对：`agent-aimail/desktop` 目前**只被** `@aimail/desktop-plugin` 引用，其它面不依赖它。

---

## 3. 方案要点（质检只需复核这 5 条）

### 3.1 载荷形状：不是自由格式，是宿主校验过的形状

初版按"想当然"写成扁平 `{sessionId, role, content}` 和 `{sessions:[…]}`，**两处都会被宿主拒**。实测形状（`apps/desktop/electron/main/plugin-runtime.ts` 校验、`crates/host-core/src/plugin_sessions.rs` 落库）：

| 调用 | 真实形状 |
|---|---|
| `pi.session.appendMessage` | `{ sessionId, message: { id?, role, content, createdAt: RFC3339 } }` —— 消息是**包裹对象**，`createdAt` 必填，`content` ≤512 KiB |
| `pi.session.import` | **单条**（不包 `sessions[]`）：`{ source, sourceLabel?, externalId, title, createdAt, updatedAt, messages[], projectId? }` |
| `import` 返回 | `{ sessionId, imported, skipped }`，按 `(pluginId, source, externalId)` 去重 |
| `appendMessage` 返回 | `{ messageId }`；宿主把 message id 视为**全局唯一**，重复 id = 已落库 |

### 3.2 幂等：崩溃重试不双写

- 投递的消息 id 由信件派生：`aimail-<Message-ID 归一化>`，无 Message-ID 用正文 FNV-1a 指纹，≤128 字符。
- `import` 的 `externalId` **取自这封信**（不是 thread root），重投同一封得 `skipped: true` + 同一 sessionId ⇒ **视为投递成功**（该会话已含此信）。
- 契约依据：宿主 `sessions::append_message_with_receipt` 的注释明确"Electron 可能重放 outbox，message id 全局唯一，已存在即已落库"。

### 3.3 新 thread 的会话分配：import 就带着这封信

一次 `import` 完成"建会话 + 投递首信"，**不建空会话、没有先建后投的空窗**。代价已入契约 §11.5：该会话 `originKind` 是 `imported`，标题带 thread root（`AIMail <root>`，≤200 字符）。

若宿主将来开放 `session.create`，`create()` 走 `session.create` → `appendMessage`，其余不变（能力探测，不是配置开关）。

### 3.4 ack 语义：只有宿主接受才 ack

`startSystemPull` 在回调 resolve 后 ack。所以 `onEmail` 里**任何不成功都抛错**：缺能力、载荷越界、宿主拒绝信封、RPC 抛错、会话查询失败、投递被拒 —— 一律不 ack，留 pending 下一轮重试。冒烟第二个场景专门验证这条（宿主拒绝写入 ⇒ 断言 `ack` 调用数为 0）。

### 3.5 传输缝：为什么要给 `GatewayClient` 加 `fetchImpl`

插件进程里 `pi.net.fetch` 是唯一被权限模型管辖的出口（`net.fetch` + `net.anyHost`，因为网关地址是用户填的、清单无法预知）。它**只能整包响应**，所以：

- `fetchImpl` 注入后 `streamCapable=false`，`streamProbe()` 直接返回 false（不白花一次请求也不会误判）；
- 拉取固定 `mode: 'poll'`；
- **默认路径不变**：未注入时仍是全局 `fetch` + `AbortSignal.timeout`，且 body 仍是 `Uint8Array`（二进制附件安全）。这条有回归护栏：本轮一次改错（把 body 改成字符串）被 `test/core/sendmail.test.ts` 立即打红，已修回字节。

---

## 4. 请质检复检的清单（逐条可执行）

> 全部在 `/home/ubuntu/aimail` 下执行。每条给出**期望**与**不通过时的判读**。

### 4.1 单测与静态

```bash
cd tssdk
pnpm install --offline        # 或按 CI 正常 install；新增的包必须被 link
npx vitest run                # 期望：Test Files 65 passed / Tests 568 passed
npx tsc -b packages/agent-aimail   # 期望：无输出（clean）
cd packages/aimail-desktop-plugin && node ../../node_modules/.bin/tsc -p tsconfig.json --noEmit   # 期望：无输出
```

本轮新增的 41 项单独跑：

```bash
cd tssdk && npx vitest run packages/agent-aimail/test/desktop \
  packages/agent-aimail/test/core/gateway-injected-fetch.test.ts   # 期望：3 files / 41 passed
```

不通过判读：`sendmail.test.ts` 变红 = 传输缝动了字节载荷语义（§3.5 的回归护栏）；`desktop/*` 变红 = 载荷形状或 ack 规则回退。

### 4.2 构建 + 冒烟（真 bundle）

```bash
cd tssdk/packages/aimail-desktop-plugin
node scripts/build.mjs      # 期望：build: aimail.pi-desktop@<当前版本> → main.js
ls -l dist                 # 期望：main.js（~122 KB）、manifest.json、package.json{type:commonjs}
node scripts/smoke.mjs     # 期望两行 ok，退出码 0
```

冒烟两行分别证明：

```text
smoke: ok — pulled, imported one session for the thread, acked after delivery
smoke: ok — a refused delivery stays pending (no ack)
```

不通过判读：第一行失败 = 拉取/import/ack 链路或载荷形状错；第二行失败 = **有拒绝被当成成功 ack 了**（这是最严重的一类，属数据安全问题，必须打回）。

### 4.3 门禁

```bash
cd /home/ubuntu/aimail && bash tests/sdk/l0-gate-tests.sh
# 期望结尾：[L0] PASS — all gates green
```

### 4.4 与宿主源码逐条对照（可选，验证 §3.1 的形状没抄错）

```bash
cd /tmp/pi-desktop-src     # 宿主源码（若不存在，跳过本节，不影响结论）
grep -n 'case "session.import"'  apps/desktop/electron/main/plugin-runtime.ts
grep -n 'accepts one item'       apps/desktop/electron/main/plugin-runtime.ts
grep -n 'fn prepare_import'      crates/host-core/src/plugin_sessions.rs
grep -n 'fn append_message'      crates/host-core/src/plugin_sessions.rs
```

期望：`import` 是单条对象（`sessions[]` 只属于 `importBatch`）；`prepare_import` 要求 `externalId/title/createdAt/updatedAt/messages`；`append_message` 读 `params.message` 并返回 `{messageId}`。

### 4.5 权限串是否真的存在

清单里 6 项必须都在宿主权限表内（拼错会被安装器拒）：

```bash
cd /tmp/pi-desktop-src && awk '/export const PLUGIN_PERMISSIONS/,/\] as const/' \
  packages/plugin-sdk/src/index.ts | grep -o '"[a-z][a-zA-Z.]*"' | tr -d '"' | sort
```

核对这 6 个：`net.fetch`、`net.anyHost`、`session.read.own`、`session.update.all`、`session.import`、`background.service`。
（`net.anyHost` 是宿主为"用户自填端点"留的安装期逃生口，见 plugin-sdk 该数组上方注释；不是我们自创的。）

---

## 5. 契约对齐（复核点）

| 契约条目 | 本轮如何满足 | 证据 |
|---|---|---|
| §9.2 I1 直属回复必须回原 Session（锚点内） | 桌面面**没有**出站索引（它归 agent 侧所有），因此不做"直属判到别的会话"；凡未能识别的父消息一律按 E2 处理，**绝不跨锚点改投** | `inbox.ts` `selectTargetSession`；§8 已列为已知边界 |
| §9 E3 新 thread 优先新 Session，失败退当前 | 宿主无 `session.create` ⇒ 走 `import`（§11.5 第 1 条），一次调用完成建会话+首信 | `delivery.ts` `importSession()` |
| §9.5 未被识别的投递不得 ack（Target 且投递成功才 ack） | 全部拒绝路径 `skip`/抛错 | `delivery.ts` `refusalOf`/`thrownRefusal`；`main.ts` `onEmail` |
| §11 判定来自能力探测 | `desktopCapabilities()` 只看方法是否存在；`create` 缺失自动落到 `import` | `delivery.ts` 顶部注释 + 测试 "skips when neither create nor import exists" |
| §11.5 幂等锚点 / ack 语义 / 容器失效 | 逐条实现见 §3.2、§3.4；空间 `NOT_FOUND` 去绑定重投并记 `projectDropped` | `delivery.ts` `importSession()`；测试 "drops a stale space binding rather than losing the mail" |

---

## 6. 本轮定位并修掉的真实缺陷（质检重点复核对象）

| # | 缺陷 | 怎么暴露的 | 修法 |
|---|---|---|---|
| 1 | `appendMessage` 传扁平 `role/content/messageId`，宿主会以 `message role is invalid` 拒绝 | 读宿主校验函数 `describeSessionWriteProblem(input, "message")` 才看到要 `message` 包裹 | 改成包裹对象 + 必填 `createdAt`；单测逐字段断言 |
| 2 | `import` 传 `{sessions:[…]}`，宿主只收单条（`session import accepts one item`） | 同上，读到 `pluginSessionPayload` 校验分支 | 改成单条对象；补 `source/externalId/RFC3339` |
| 3 | 传输缝一度把 body 从字节改成字符串，二进制附件会被破坏 | `test/core/sendmail.test.ts` 立刻 13 项变红（stub 用 `TextDecoder().decode(init.body)`） | 改回 `Uint8Array`，broker 侧自行 decode |
| 4 | `dist/main.js` 落在 `type: module` 包里，宿主 `require` 会按 ESM 解析，`module.exports` 拿不到 | 构建告警 + `node -e require(...)` 实测 | 构建时写 `dist/package.json{type:"commonjs"}`；冒烟直接 require 构建产物 |
| 5 | 插件包 `tsc` 报 `GatewayFetchImpl` 未导出 | 插件包类型检查 | `src/core/index.ts` 补导出 |

---

## 7. 打包（质检执行）：已验证的约束与建议

### 7.1 要打的目录

```text
tssdk/packages/aimail-desktop-plugin/dist/
  main.js           # 单文件 CJS bundle（~122 KB）
  manifest.json     # 插件清单
  package.json      # {"type":"commonjs"} —— 不能丢
```

### 7.2 宿主侧的硬约束（开发已验证，质检无需重推）

| 约束 | 值 / 规则 | 出处 |
|---|---|---|
| 归档格式 | zip，**store-only（method 0）**；其它压缩方法宿主直接拒 | `crates/host-core` `extract_zip_bytes`；ADR 0007 |
| 布局 | `manifest.json` 在**归档根**，与 `main.js` 同级 | ADR 0007 |
| 上限 | ≤ 2000 文件、≤ 50 MiB、**无符号链接** | `plugin-devkit/src/walk.ts`（对应 host-core `MAX_PACKAGE_FILES/BYTES`） |
| 不得入包 | `.git/`、`node_modules/`、`.env*`、`.npmrc`、`*.pem`、`*.key` | 同上 |
| 完整性 | 对归档**字节**取 sha256（安装期校验用它比较） | `plugin-devkit/src/pack.ts` |
| 命名 | `<id>-<version>.piplug`，如 `aimail.pi-desktop-0.2.1.piplug`（id 已由质检改为 `aimail.pi-desktop`：`aimail.desktop` 被宿主判为非法 id） | 同上 + commit `ee3446e` |
| 版本单一来源 | `manifest.version` 必须等于 `package.json` version（**构建脚本会因此拒绝**） | `scripts/build.mjs` |

**一处最容易踩的**：包内 `main.js` 必须能被 `require`，而本包 `package.json` 是 `"type": "module"`。`dist/package.json` 里的 `{"type":"commonjs"}` 就是为此存在——打包时不能丢、也不能把它换成 `"module"`。

### 7.3 建议的流水线步骤（供质检落地）

1. `pnpm install --frozen-lockfile`（tssdk 根）
2. `pnpm --filter @aimail/desktop-plugin build`
3. `node packages/aimail-desktop-plugin/scripts/smoke.mjs`（**必跑**：ack 规则的最后一道闸）
4. 校验 `dist/package.json` 存在且 `type === "commonjs"`
5. 以 store-only 打 zip（`manifest.json` 在根），计算 sha256，产出 `.piplug` + `.piplug.sha256`
6. 上传工件；`plugin-v*` tag 时附加到 release

第 5 步若用脚本实现，注意**不能**用 `zip -r`（默认 deflate）。可直接用 devkit 的 `pack`（若环境能装到 `@pi-desktop/plugin-devkit`）；否则需自己写 store-only 的 zip（本地条目头 + 中央目录 + EOCD，方法位写 0）。

### 7.4 发版前建议先做的一件事

本轮改动**全在工作树未提交**。建议质检复检通过后：先提交这批文件（`git add` 上述 §2 列表，注意**不要**把 `dist/`、`evidence/`、他人在改的 `tests/l3/*`、`docs/TSSDK-L2-FIX-PLAN.md` 一起带进去），再打 tag 触发打包。版本号：插件版本（当前 `0.2.1`）与 `agent-aimail` 相互独立（本插件不走 npm 分发），升版由质检/owner 定。

---

## 8. 未验证 / 已知边界（请据此判断能否发版）

| 项 | 状态 | 说明 |
|---|---|---|
| 真机安装（插件中心导入 `.piplug`） | **未做** | 安装进宿主属自助安装，开发不做。这是唯一没有实测的一环，风险集中在"权限授予 + 服务守护" |
| 宿主沙箱 / 权限实际生效 | **未验证** | 冒烟用假 `pi`，不覆盖 broker 的拒绝行为 |
| `contributes.services` 的启动时机 | 未实测 | 依据 spec 07-plugins §7"broker 在 onLoad 返回后启动声明的 service"；注册即返回，不主动起服务 |
| 用户重命名 import 出来的会话 | 已知限制 | 标题是 thread 的锚点，改名后该 thread 的下一封会新开会话（宿主无 thread 索引 API） |
| 直接回复的严格同会话 | 已知边界 | 缺出站索引（归 agent 侧），见 §5 第一行；不会误投他人会话 |
| `apiKey` 存放 | 已知限制 | 存插件设置文件、宿主明文；清单 schema 现版本拒绝 `secret` 设置项（源码注释"until secure storage exists"） |
| `session.create` | 宿主未开放 | 本面走 `import`，语义已入契约 §11.5 |
| 长连接 SSE | 不可用 | `pi.net.fetch` 只能整包响应 ⇒ 固定 poll（30s 默认，可配） |

---

## 9. 发版决策输入（不预设立场）

- **可以不依赖上游就能发**：`import` 路径是自洽的，契约已把语义打折写成正式注记（§11.5），不阻塞。
- **发之前建议 owner 回答一个问题**：插件是否要进 npm 分发？现在设计是"只出 `.piplug` 工件"，npm 上仍只有 `agent-aimail`（本插件需要的 `./desktop` 入口已在其 `exports` 里）。
- **装了之后最值得先看的一条**：宿主拒绝写入时邮件是否留在 pending（对应冒烟第二场景）。这是唯一会伤数据的路径。

---

## 10. 阅读顺序（给质检的索引）

1. `docs/aimail-host-sse-contract-v2.md` §11（三层判定）、**§11.5**（创建能力的等价形式：导入）、§9（三条映射边与 ack）
2. `docs/pi-host-integration-plan.md` §11.3（步 0 桌面三验实测）、§11.4（载荷实测）、**§11.6**（本轮落地 + 打包约束）
3. `tssdk/packages/agent-aimail/src/desktop/delivery.ts` 顶部注释（为什么这么写）→ `inbox.ts`
4. `tssdk/packages/aimail-desktop-plugin/README.md`（构建 / 打包归属 / 宿主契约表 / 已知限制）
5. `tssdk/packages/aimail-desktop-plugin/scripts/smoke.mjs`（可执行规格：正例与拒绝例各一个场景）
