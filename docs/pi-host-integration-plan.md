# pi 宿主接入方案：能力判定、双面 SDK 与投递契约

状态：**待审批**（尚未修改任何实现代码）
起草：2026-11-24 · 适用：`tssdk/packages/agent-aimail`（TS/npm）、`docs/aimail-host-sse-contract-v2.md`
本文取代此前 v1/v2/v3 迭代稿（已删除，避免多版本并存与"打补丁式"阅读）。

---

## 1. 目标与判定框架

pi 生态有两种承载形态，能力不同，因此**不能一刀切**地判定"pi 是多地址还是单地址"。本方案先定判定框架，再用证据判两端，最后给出各自的对接形态与投递契约。

**判定框架（owner 定，本方案采用）**

- **J1 空间**：是否存在类似 workspace 的**中间层会话容器**（地址绑在该容器上）。
- **J2 会话**：
  - ①**能唤起目标 session** 处理入站回复 —— **硬约束**；
  - ②**能创建新 session 或唤起当前 session** 处理新邮件。
- 两条缺一 ⇒ 单地址（S）。且 ①**必须稳定**：在"目标会话正被他人使用"时也要可确定成功或可确定推迟，且**绝不双写**；不能稳定实现 ⇒ 判为 S。

---

## 2. 事实基础（全部经代码/实测确认）

### 2.1 pi 引擎（`@earendil-works/pi-coding-agent` 1.5.2）

| 事实 | 证据 |
|---|---|
| 入站路径拿不到会话控制：`newSession`/`fork`/`switchSession`/`navigateTree`/`reload` 属 `ExtensionCommandContext`（*"only safe in user-initiated commands"*），入站只拿到只读 `sessionManager`（`getCwd`/`getSessionId`/`getSessionFile`）与 `sendUserMessage` | `dist/core/extensions/types.d.ts` |
| 注入语义：`deliverAs: "steer"(interrupt) / "followUp"(wait)`，**流式中必须显式传** | `dist/core/agent-session.d.ts:184` |
| 会话按 cwd 分目录存：`~/.pi/agent/sessions/<encoded-cwd>/`；`findMostRecentSession(sessionDir, cwd)`、`getDefaultSessionDir(cwd)` | `dist/core/session-manager.d.ts:210,214,376`；实测 cwd `/tmp/probe-ws` ⇒ 目录 `--tmp-probe-ws--` |
| 会话文件是 append-only JSONL，**无文件锁**（`lock`/`flock`/`.lock` 全无） | `session-manager`（检索无命中） |
| 外部控制面存在：`--mode rpc`（JSONL）支持 `new_session`/`switch_session{sessionPath}`/`prompt`/`steer`/`follow_up`；CLI `-p --session <path|id>`、`--session-id <id>`（"creating it otherwise"） | `dist/modes/rpc/rpc-types.d.ts`；`pi --help`；实测 `get_state` 返回 `sessionId`/`sessionFile` |
| 无进程间注入通道（无 `createServer`/`ipcMain`/`process.send`） | `dist/{core,modes,cli}` 检索命中 0 |

### 2.2 PI-Desktop（`vastsa/PI-Desktop` @ `8e258d0`）

| 事实 | 证据 |
|---|---|
| 常驻 headless host：RACP server（loopback）+ Agent Host + Node agent runtime + Rust host-core | `apps/pi-host/README.md` |
| pi 会话**跑在宿主进程内**；扩展由宿主进程内加载 | `packages/agent-runtime/src/native-pi-session.ts`、`src/extensions/{discovery,loader,runner}.ts` |
| 扩展的命令上下文里会话控制**全部 unsupported** → 抛 `NATIVE_PI_UNSUPPORTED` | `native-pi-session.ts` `commandContextActions` |
| 会话有**租约**（协式锁 + 文件快照校验） | `native-pi-session.ts` / `native-pi-session-lease.ts` |
| 会话生命周期是宿主能力，经 RACP 暴露：`session/{create,get,list,attach,history,configure,rename,archive,revoke,fork,compact,deriveTitle,delete}`、`turn/{start,get,stop,interrupt,cancel,prioritize}`、`events/*`、`project/{list,register,browse}`、`workspace/*`、`tools/*` | `packages/racp/src/operations.ts` |
| `turn/start` 参数：`sessionId`、`idempotencyKey`、`admission:"queue"\|"reject_if_busy"`、`input.text` / `input.sessionMessageId` | 同上 `TurnStartParams` |
| `turn/start` **只要求会话在持久层存在**（`sessions.get`），不要求驻留内存 ⇒ 宿主自行加载 + 取租约 | `packages/agent-host/src/agent-host.ts:1483 requireSession`、`:522 effectiveRemotePermissionMode` |
| 幂等：`idempotencyKey` 记输入哈希，不符抛 `IDEMPOTENCY_CONFLICT` | `agent-host.ts:505-520` |
| `session/create` 绑定 **`projectId`**（不是 cwd） | `operations.ts` `SessionCreateParams` |
| RACP 鉴权：配对设备令牌（`connection/pair`）+ 角色（viewer/controller/approver/owner） | `packages/racp/src/auth.ts` |
| 远程权限收敛：`applyCeilingToPairedDevices` 默认 **false**、`remoteMaxPermissionMode` 默认 **"ask"** ⇒ 配对设备默认不降档；宿主开启该策略后远程投递每步需人工批准 | `packages/shared/src/racp.ts:515-528,832-840` |

### 2.3 PI-Desktop 插件系统

| 事实 | 证据 |
|---|---|
| 插件是**宿主级 JS 模块**，清单 `{id,name,version,main,permissions,contributes,renderer?…}`，`main` 为 ESM 入口（`export function onLoad()`） | `crates/host-core/src/plugins/manifest.rs:4-44` |
| 插件**能拥有自己的会话**；持 `*.all` 授权时可触达**所有活会话** | `crates/host-core/src/plugin_sessions.rs:1-6,42-66`（`SessionScope::Own` ADR 0200 / `::All` ADR 0339） |
| 权限：清单声明 + **安装时用户授予**（装上 ≠ 获权） | `manifest.rs:34,42-44` |
| `.all` 家族为**高危**：`session.read.all` / `session.update.all` / `session.delete.all` | `apps/desktop/src/features/plugins/model.ts:60-66` |
| 执行点在 Electron 主进程，逐方法映射权限：`appendMessage`/`replaceMessages` ⇒ `session.update.all` | `apps/desktop/electron/main/plugin-session-access.ts:40-41` |
| 已有官方插件先例：`pi.official-enhancements` 声明并使用 `session.read.all` | `apps/desktop/resources/plugins/pi.official-enhancements/README.md` |
| 插件可申请常驻后台服务权限 `background.service` | `model.ts` 权限表 |
| 分发走 PI-Desktop 自己的插件中心，**不是 npm**：目录 `https://plugins.aiuo.net/catalog.json` + `/api/v1/download/resolve` | `crates/host-core/src/plugins/resolve.rs:222-223` |
| 包体限制 `MAX_PACKAGE_BYTES=50MB`、`MAX_PACKAGE_FILES=2000` | `plugins.rs:17-18` |

### 2.4 参照：dsh（为什么它有资格做多地址）

| 事实 | 证据 |
|---|---|
| 地址绑**workspace 实体**（`workspace_id`/`workspace_path` 写入 binding） | `tssdk/.../dsh/address.ts:33,96,128,180` |
| 宿主提供会话工厂：`agents.get` / `agents.resume` / `agents.create({meta:{cwd}})` | `tssdk/.../dsh/mail-hub.ts:405-430` |
| workspace 注册表是**扁平的项目目录列表**，无父子/嵌套概念 | `@deepseek-ai/dsh-workspace` README + `lib/index.js`（`parent*/children/nested` 命中 0） |

---

## 3. 判定结果

| 判据 | **裸 pi**（TUI/CLI） | **PI-Desktop** |
|---|---|---|
| **J1 空间容器** | ✅ 有：**cwd 目录**即容器（会话按 cwd 分目录）；路径即身份，无独立 id | ✅ 有：**project 实体**（`project/*` + `session/create{projectId}`），带 id |
| **J2① 唤起目标 session（硬约束）** | ❌ **不稳定**：唤醒 = `SessionManager.open()` + `appendMessage()`，**无锁**且**探测不到第三方写者**（用户手开的 TUI、`pi --resume`）；自建登记只能覆盖"也装了本扩展"的进程 ⇒ 双写/上下文分叉有真实窗口 | ✅ **稳定**：`turn/start` 只要求会话在持久层存在，宿主自行加载并取租约；忙时 `admission:"queue"` 排队；幂等键防重；宿主是唯一写者 |
| **J2② 创建新 / 唤起当前** | ✅ `new_session` / `--session-id`；当前 = 进程内 `sendUserMessage` | ✅ `session/create{projectId}`；当前 = 该会话内注入 |
| **判定** | **单地址（S）** | **多地址（M）** |

裸 pi 的不稳定源于宿主缺能力（无排他会话打开、无 resume-by-id 独占语义），不是适配层能补的——因此**不为它设计外部进程/spool/心跳那套"凑稳定"的机关**。

---

## 4. 对接架构：一份 SDK，两个面

```text
agent-aimail (npm)
├── ./            共享核心
│                 bindings · 地址↔空间绑定 · thread/session 路由 · outbound 索引
│                 · SSE 客户端（每 (AgentSystemId,Gateway) 一条）· tool-spec
├── ./pi          pi 扩展面（引擎通用）→ 裸 pi（S）
│                 15 个邮件工具 + 本会话 sendUserMessage；不碰会话生命周期
└── ./desktop     PI-Desktop 插件面（宿主级 JS）→ PI-Desktop（M）
                  onLoad 起常驻 SSE；按 project 绑定地址；投递到目标 session
```

两端用户安装都走**宿主自己的命令**，不要求用户敲 npm：

- 裸 pi：`pi install <source>`；
- PI-Desktop：插件中心（`plugins.aiuo.net`）。npm 只是源码/依赖渠道，可作为插件打包依赖被 vendor。

**为什么桌面面是插件而不是 RACP 客户端**：插件在宿主进程内 ⇒ 无需配对令牌、不受远程权限收敛（`remoteMaxPermissionMode`）影响、SSE 与投递同域；权限在安装时一次授予。RACP 仅留作"拿不到插件权限时的调试退路"。

---

## 5. 投递契约

### 5.1 路由（thread 与 session 是两个维度）

```text
入站：
1. gateway_url + recipient → binding；无 binding / paused ⇒ 不 ack
2. 空间 = binding 绑定的 project（桌面）/ cwd 目录（裸 pi）
3. parent = In-Reply-To（立即父消息）；last = References 的最后一个元素；
   root0  = References[0]（thread 根，不能用来判"直接回复"）
4. 直接回复 := parent ∈ outbound 索引（同 address+gateway）
               无 In-Reply-To 时用 last；仅 references 链中更深处命中 ⇒ 不算法直接回复
   ⇒ 硬约束：投递到 outbound[parent].sessionId，不做降级
5. 非直接回复：
   a. thread 有历史 ⇒ 回到"曾处理该 thread 的 session"（thread 可跨 session）
   b. 无历史 ⇒ 新建 session（新 thread 优先新 session）
      失败 ⇒ 退到当前活跃会话（仅记日志，不改正文、不加标注）
      再失败 ⇒ 不 ack，留 pending 重试
6. 投递被宿主接受 ⇒ 写 thread 记录（root → 最近 session）+ ack
```

出站侧（使上述判断可执行）：`send_mail` 时写 `outbound[messageId] = {address, gateway, sessionId, space, root}`，并写 `thread[root]`（复用 tssdk `core/meta.ts` 的 `stampSessionMeta` / `readLocalMeta`，dsh 已在用）。

### 5.2 桌面端投递通道

| 用途 | 通道 | 权限 |
|---|---|---|
| 投递到目标 / 新会话 | 插件会话 API：`session.appendMessage`（入 `session.update.all`）；需要宿主排队时用 RACP `turn/start{admission:"queue"}` | `session.update.all` |
| 新建会话 | `session.create{projectId}`（插件会话 API 或 RACP） | `session.create` |
| 常驻 SSE | `onLoad` 内起循环 | `background.service` |

冲突处理**由宿主实现**：插件在宿主进程内为唯一写者，另有会话租约；RACP 需排队时用 `admission:"queue"`，重复投递用 `idempotencyKey = aimail:<message-id>`。**不再自造 spool / 归属心跳。**

### 5.3 裸 pi（S）的行为

一实例一 active 地址；入站一律进当前会话；第二地址拒绝（`SINGLE_ADDRESS_HOST`，不消耗激活码）。不做外部唤醒、不做多会话路由。

---

## 6. 契约改动（`docs/aimail-host-sse-contract-v2.md`）

1. **§11 能力前置**（替换现有"多地址/单地址"的二元表述为可执行清单）：
   - 稳定空间 + 地址绑该空间；
   - **能稳定唤起目标会话**（可确定成功或可确定推迟，绝不双写）；
   - 能创建新会话或唤起当前会话；
   - 上述能力可由入站路径触发。
   任一条不满足 ⇒ S。
2. **§9 映射**改为三条边（E1 直属 > E2 thread > E3 分配；E1 锚点内硬性、E2 可跨 Session 重挂、E3 优先新会话）。
3. **§11 判定**改为三层（L1 锚点 / L2 隔离 / L3 调用），宿主归类修正为 **裸 pi = S、PI-Desktop = M**。
4. 新增注记：多地址宿主的"唤起"能力由**宿主**提供（桌面 = 插件会话 API / RACP），扩展层不承担会话生命周期。

---

## 7. 安装与安全

| 项 | 结论 |
|---|---|
| 能否在对话框里 prompt 触发安装 | **裸 pi**：技术可行（agent 有 Bash，`pi install` 无"会话内拒绝"硬闸），但那属**自我提权**（agent 改自己的扩展集）⇒ 产品上不采用。**桌面**：不能静默完成，安装是插件中心流程且**权限必须用户授予**，跨不过 `session.update.all` 这一关 |
| 激活路径 | 保持现状：激活邮件引导用户用**宿主自己的插件机制**安装，agent 只负责引导与绑定，不自行 install |
| 高危权限 | `.all` 家族 = high，安装时显式批准；`scope` 参数只接受 `own`/`all`，写错直接报错不放宽 |
| 远程（RACP）退路 | 配对设备 + 角色；默认不降档，但若宿主开启 `applyCeilingToPairedDevices`，每步需人工批准 ⇒ 仅作调试 |

---

## 8. 测试与门禁

| 层 | 内容 |
|---|---|
| 单测（tssdk） | ① 直接回复判定（`In-Reply-To` 命中 / 缺失时用 `References[last]` / 仅 `References[0]` 命中不算法直接回复）；② thread 跨 session 复用；③ 新 thread 新会话与失败回退；④ outbound 索引写入与读取；⑤ 地址↔project 绑定；⑥ `idempotencyKey` 冲突路径 |
| L2（现有夹具） | 裸 pi 维持 S：`s4` 走"拒绝第二地址"分支；`s1 s2 s3` 不变红 |
| 桌面 E2E（需环境） | ① 冷会话 `turn/start` 能拉起并完成；② `admission:"queue"` 忙时排队而非拒绝；③ 同 `idempotencyKey` 重投只生效一次；④ 直接回复落在**同一** session 文件；⑤ 新 thread 产生新 session 且 `projectId` 正确 |
| 门禁一致性 | `addressModel` 与其能力必须自洽：声明 `multi` 的宿主必须能通过上述 ①②④⑤；声明 `single` 的必须走拒绝分支 |

---

## 9. 实施顺序

| 步 | 内容 | 退出判据 |
|---|---|---|
| 0 | 桌面 E2E 探针（上表 ①②③），拿到 `addressModel` 的实证依据 | 三探针各有脚本与日志 |
| 1 | 契约改 §9/§11 + 宿主归类修正 | 文档 review；`l0-gate-tests.sh` 绿 |
| 2 | 共享核心：outbound 索引、路由五步、地址↔project 绑定 + 单测 | 单测全绿（不依赖宿主） |
| 3 | `./pi` 面收敛为 S 语义（去单地址钉之外的多地址假设），L2 回归 | 裸 pi `s1 s2 s3 s4 neg` 全绿 |
| 4 | `./desktop` 插件面：`onLoad` 起 SSE、`session.appendMessage`/`session.create`、权限声明 | 桌面 E2E 全绿 |
| 5 | 全量回归（pi/dsh/hermes/deerflow + L0） | 全绿 |

---

## 10. 未决与风险

| # | 项 | 现状 |
|---|---|---|
| R1 | 桌面宿主环境 | 我这边没有可连的 PI-Desktop 宿主 ⇒ 步 0 需 owner 提供（配对一次） |
| R2 | 插件市场准入是否对 `.all` 一票否决 | 未找到明确限制，也未找到"允许"条款；自用/本地安装不受限 |
| R3 | `background.service` 的具体语义（能否长期驻留、能否无会话运行） | 权限存在、行为未验证 ⇒ 步 4 首件事 |
| R4 | 裸 pi 自我提权面 | `pi install` 未见硬闸；若将来要开放"agent 自助安装"，需我们在扩展内自行拒绝 |
| R5 | 桌面端 `session.appendMessage` 与 `turn/start` 的取舍 | 前者更轻（宿主排队），后者带 `admission` 显式语义；步 0 实测后定 |

---

## 11. 执行状态（2026-11-24）

| 步 | 状态 | 产出 / 证据 |
|---|---|---|
| 0 桌面 E2E 探针 | ✅ **已实测通过** | 运行环境**本身即 PI-Desktop**：`pi-host 0.23.0` @ `ws://127.0.0.1:45861`（WebSocket + `Authorization: Bearer`）。探针脚本已就绪：`tests/host-acceptance/v2/lib/racp_probe.mjs`（已实测可连：无效令牌止于鉴权）。**唯一缺口 = 有效设备/配对令牌**；重启宿主 `--pair` 会杀掉当前会话，不擅自动手 |
| 1 契约 §9/§11 | ✅ | `docs/aimail-host-sse-contract-v2.md`：§9 改为「直接回复 → thread → 新会话」并新增 §9.1 出站索引；§11 改为三条能力轴（空间 / 会话创建 / **会话唤起稳定性**）+ M 的四项前置 + §11.4 矩阵 |
| 2 共享核心（路由） | ✅ | `tssdk/.../runtime/session-router.ts`（`parentMessageId` / `directReplySession` / `referencesList` / `normalizeMessageId`）、`runtime/routing.ts`（`decideInboundDelivery`：direct → followup → new）；单测 **17 项** |
| 3 `./pi` 收敛为 S | ✅ | 新增 `core/host-capabilities.ts`（`address_model` 声明 + 单地址拒绝 `SINGLE_ADDRESS_HOST`，在**任何网关调用之前**返回，不消耗激活码）；`pi/index.ts` 声明 `single`；单测 **3 项** |
| 门禁加固 | ✅ | `cases/s4_multiaddr.sh`：单地址分支不再只看 `max_bindings ≤ 1`，必须看到**书面拒绝**（否则 RED）；`run-matrix.sh` 对所有宿主都申请第二个 code（原先 pi 被跳过 ⇒ s4 是空过）；`hosts/pi.sh` 增加 per-turn 证据日志 |
| 全量验证 | ✅ | tssdk `vitest` **527 passed**；`l0-gate-tests.sh` **PASS**；pi L2 `s1 s2 s3 s4 neg` **PASS=15 RED=0 GAP=0**（S4 为真断言）；hermes `s1 s4` **PASS=8** 未被新断言打红 |
| 4 `./desktop` 插件面 | 🟢 **源码 + 构建 + 冒烟通过**（打包归质检） | `agent-aimail/desktop`（`delivery.ts` 23 项 + `inbox.ts` 14 项单测）、`aimail-desktop-plugin`（manifest + `main.ts` + CJS bundle）；`scripts/smoke.mjs` 用假 `pi` 跑通真 bundle 的拉取→import→投递→ack 与"拒绝即不 ack"。**打包（`.piplug` 与流水线）由质检负责**，需要的约束见 §11.6 |

**实施期发现并修掉的三个真问题**

1. **单地址宿主此前是"结构性单地址"而非"拒绝"**：pi 的第二次绑定会覆盖同一 agent 槽位（门禁只看 `max_bindings ≤ 1`，所以一直绿灯）。现改为在任何网关调用前显式拒绝。
2. **门禁对 pi 的 s4 是空过**：`run-matrix.sh` 对 pi 不申请第二个 code，s4 从未真正发起第二次绑定。现对所有宿主都申请。
3. **能力声明在打包后可能失效**：pi 的 bundle 会出现两份 `host-capabilities` 模块实例，内存态分裂导致守卫静默失效（第二封绑定直接打到网关，日志 `NETWORK`）。已加 `AIMAIL_HOST_ADDRESS_MODEL` 环境变量通道并让环境优先。

### 11.1 本机宿主实证（2026-11-24）

| 事实 | 证据 |
|---|---|
| 运行环境就是 PI-Desktop 宿主，版本 **0.23.0** | 进程 `pi-host.js --port 0 --pair --pairing-lifetime-ms 600000`；日志 `pi-host ready {version:"0.23.0", port:45861}` |
| RACP = WebSocket，鉴权 `Authorization: Bearer <token>` | `racp listening 127.0.0.1:45861`；`auth.ts` 只接受 Bearer；探针用无效令牌实测：**连得上，止于鉴权** |
| 空间是 `projects` 实体（J1 成立） | `~/.pi-desktop/pi.sqlite`：`projects(id, path, name, pinned, created_at, last_opened_at, …)` |
| 会话持久化、且有**队列表**（`admission:"queue"` 有实体支撑） | `sessions(id, title, project_id, provider_id, model_id, mode, permission_mode, …)`、`turns`、`turn_queue`、`messages` |
| 关键操作在 0.23.0 里都存在 | `pi-host.js` 命中 `turn/start`、`session/create`、`admission`（`session.update.all`/`background.service` 属桌面侧权限串，不在 host 包内——与"host 只按传入 scope 执行"一致） |
| 当前 0 个项目 / 0 个会话 | 同一 sqlite 查询 |

**为什么还不能跑完探针**：RACP 需要配对设备令牌；bootstrap 日志里的配对令牌（`PI_HOST_PAIRING`）已于 `2026-11-22T17:03Z` 过期，`credentials.json` 只存哈希。重新配对需要宿主以 `--pair` 重启——那会杀掉当前会话，不能擅自做。**需要 owner 在 PI-Desktop 界面"添加设备"给出新配对令牌**（或明确同意重启宿主），我把令牌接上即可跑完三验。

### 11.2 步 0 实测进展（2026-11-24，独立宿主实例）

用 `--data-dir` 起**独立第二实例**（不动现有宿主），逐层打通到 RACP 鉴权完成：

| 层 | 结果 | 说明 |
|---|---|---|
| 独立宿主可起 | ✅ | `node pi-host.js --data-dir /tmp/piprobeN --port 0 --pair` → `PI_HOST_READY {port, version:"0.23.0"}` |
| WS 升级契约 | ✅（已实测） | 路径 **`/v1/racp/ws`**、子协议 **`pi-racp.v1.jsonrpc`**、头 `Authorization: Bearer <token>`。路径不对 ⇒ `404 Not Found`；子协议/头不对 ⇒ `1006`；令牌不对 ⇒ `401 Unauthorized`（宿主日志逐条可辨） |
| `connection/initialize` | ✅ | 返回 `protocolVersion=1.0`、`server.hostId` |
| `connection/pair` | ✅ | 用 `--pair` 打印的 `PI_HOST_PAIRING_TOKEN` 配对成功，签发设备令牌 |
| 角色生效 | ✅（有坑） | **配对连接仍是无角色的 pairing 身份**：必须用新设备令牌**重新建连 + 再次 initialize**，否则 `events/subscribe` 报 `principal lacks the role` |
| `session/create` | ⛔ | 报 **`No provider configured`**：本机 host-core 的 `providers` 表为空（`~/.pi/agent/models.json` 是 agent 侧配置，不走 host-core 的 provider 注册）。桌面端正常使用时由 UI 注册 provider |

**结论**：RACP 通道本身完全可用（鉴权→初始化→配对→以 owner 身份连接全部通过），卡点是**这个安装没有给 host-core 注册 provider**，而注册 provider 只能从桌面 UI 做（RACP 没有 provider 注册操作）。

**下一步（需 owner 在桌面端点一次）**：在 PI-Desktop 里配置一个可用 provider/model（或确认某个已配置主机），之后 `racp_probe.mjs` 一条命令即可跑完三验：冷会话 `turn/start`、`admission:"queue"` 排队、同 `idempotencyKey` 只生效一次。

### 11.3 步 0 实测通过（2026-11-24，独立宿主实例 + 本地模型）

在 `--data-dir` 独立实例上跑通 `tests/host-acceptance/v2/lib/racp_probe.mjs`：

```text
connected: host=host_C7YfeoOItqkZTaGR proto=1.0
PASS  pair                      — device token issued
PASS  cold turn/start accepted  — turn=a0f0fc9a-… (84ms)      ← 冷会话可被唤醒
PASS  cold turn terminal        — states=running→completed     ← 真实 turn 跑完（本地模型）
PASS  admission=queue accepted  — status "running"             ← 忙时不拒绝、排队
PASS  idempotencyKey deduplicates — again == first（同一 turn id）← 重投只生效一次
PASS  session/history readable
```

**结论：PI-Desktop 的 M 判定从"代码层证据"升级为"实测证据"**——J2① 唤起目标会话稳定成立（`turn/start` 只要求持久层存在、宿主加载并取租约）、忙时排队、幂等去重均已实测。

**协议细节（已固化进探针，后续复用）**

| 项 | 值 |
|---|---|
| 路径 | `/v1/racp/ws`（其它路径 ⇒ `404 Not Found`） |
| 子协议 | `pi-racp.v1.jsonrpc`（缺/错 ⇒ `1006`） |
| 鉴权 | `Authorization: Bearer <pdt1.|ppt1. token>`（缺/错 ⇒ `401 Unauthorized`） |
| 客户端 | 必须用 `ws` 包；**Node 内置 WebSocket 会丢掉自定义头** ⇒ 表现为假的 `Unauthorized` |
| 配对 | `--pair` 打印 `PI_HOST_PAIRING_TOKEN`；配对连接**无角色**，须用新设备令牌**重连 + 再 initialize** |
| 变更调用 | `session/create`、`turn/start`、`session/delete` 等都必须带 `context.requestId` |
| provider | host-core 的 `providers` 表需有记录（本机为空）；实测用 `auth_kind='none'` + 本地 `llama-server` 跑通真实 turn |

### 11.4 步 4 端口层完成（2026-11-24）

`tssdk/.../src/desktop/delivery.ts` + 23 项单测，从包导出 `agent-aimail/desktop`。

**实测约束（决定实现形态）**：PI-Desktop 给插件的 API 里**没有 `session.create`**——暴露的是
`session.appendMessage` / `session.list` / `session.get` / `session.import` / `session.importBatch` / `session.rename` / `session.delete` …
（`apps/desktop/electron/main/plugin-runtime.ts` 的方法表）。

**载荷形状也不是自由格式**（`plugin-runtime.ts:1045` 校验、`plugin_sessions.rs` 落库）：

| 调用 | 真实形状（初版猜错过，已按此改） |
|---|---|
| `appendMessage` | `{ sessionId, message: { id?, role, content, createdAt: RFC3339 } }` —— 消息必须是**包裹对象**，`createdAt` **必填** |
| `import` | 单条（不包 `sessions[]`）：`{ source, sourceLabel?, externalId, title, createdAt, updatedAt, messages[], projectId? }` |
| `import` 返回 | `{ sessionId, imported, skipped }`，按 `(pluginId, source, externalId)` 去重 |
| `appendMessage` 返回 | `{ messageId }`；宿主把 message id 视为全局唯一，重复 id = 已落库 |

于是三条路径：**投递一律是一次确定性 id 的 `appendMessage`；新 thread 的分配用 `import`，且 import 就带着这封信**
（一次调用完成"建会话 + 投递首信"，没有"先创建后投递"的空窗）。重试语义：import 的 `externalId` 取自**这封信**
（`Message-ID`/正文指纹），重投得 `skipped: true` + 同一 sessionId ⇒ 视为成功，不双写。

| 场景 | 调用 | 失败时 |
|---|---|---|
| 直接回复 / thread 命中 | `appendMessage`（`message.id` = `aimail-<mail-id>`） | `skip`（不 ack） |
| 新 thread，宿主有 `create` | `session.create` → `appendMessage` | 同上 |
| 新 thread，宿主只有 `import`（**今天就是这样**） | `session.import`（带信，originKind=imported） | 同上 |

**语义打折已入契约**：`import` 造出来的会话在侧栏里显示为"导入的会话"，与"新建会话"不同。owner 已选择"自我可控"
路线 —— 接受该机制、把它写成正式注记（`docs/aimail-host-sse-contract-v2.md` **§11.5**：分配/标注/幂等锚点/ack
语义/空间失效重投/同 thread 首信单飞），并把代码做成**一等路径**而非兜底分支，而不是等上游开口子。

### 11.6 插件源码落地；打包由质检驱动

`tssdk/packages/aimail-desktop-plugin/`：`manifest.json` + `src/main.ts`（`onLoad` 注册 `contributes.services`
服务，`start` 里起 `startSystemPull`，`mode:'poll'` 因为 `pi.net.fetch` 只能整包响应），路由/投递全部复用
`agent-aimail/desktop`（`delivery.ts` + `inbox.ts`），构建产物是单文件 CJS `dist/main.js`。

**形态（owner 2026-11-24 定）**：代码统一管理、产物按宿主分开 —— 契约级规则留在 `agent-aimail`（与 `./pi`、
`./dsh`、`./openclaw` 共用同一套类型与门禁测试），只有宿主接线在插件包里；PI-Desktop 的加载机制决定产出物是
`.piplug` 而非 npm 包。`agent-aimail/desktop` 目前只被插件包引用，不需要搬动。

**打包不是我做的**（owner 2026-11-24：打包由质检驱动）。我只保证两件事：

1. **输入目录**：`packages/aimail-desktop-plugin/dist/`（`main.js` + `manifest.json` + `package.json{type:commonjs}`）。
2. **已验证的打包约束**（免得质检再翻一遍宿主源码）：

| 约束 | 值 | 出处 |
|---|---|---|
| 归档 | zip **store-only**（method 0），其它压缩方法宿主直接拒 | `crates/host-core` `extract_zip_bytes`；ADR 0007 |
| 布局 | `manifest.json` 在**归档根**，与 `main.js` 同级 | ADR 0007 |
| 上限 | ≤2000 文件、≤50 MiB、无符号链接 | `plugin-devkit/src/walk.ts` |
| 不得入包 | `.git/`、`node_modules/`、`.env*`、`.npmrc`、`*.pem`、`*.key` | 同上 |
| 完整性 | 对归档字节取 sha256 | `plugin-devkit/src/pack.ts` |

**另有一条容易踩的**：包内 `main.js` 必须按 CJS 加载，而本包 `package.json` 是 `type: module`；
`dist/package.json` 里的 `{"type":"commonjs"}` 就是为此存在，打包时不能丢。

**验证**：`node scripts/smoke.mjs` 用假 `pi` 加载**构建后的 bundle**，跑通"拉取 → 为 thread import 会话 → 投递 → ack"，
并跑一遍"宿主拒绝写入 ⇒ **不 ack**"。这覆盖载荷形状与 ack 规则，但不覆盖宿主的权限/沙箱/服务守护（那要真装）。

**交接**：复检与打包的完整说明（目标、方案、自检证据、复检清单、打包约束、已知边界）见
`docs/qa/HANDOFF-PI-DESKTOP-PLUGIN.md`。
