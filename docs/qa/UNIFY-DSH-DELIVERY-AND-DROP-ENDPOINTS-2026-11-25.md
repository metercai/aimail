# 统一 dsh 投递链路 + 彻底移除本地端点（2026-11-25，owner 指示）

两件事一起做，因为它们互为因果：本地监听的存在逼着每个适配层维护第二套投递实现，而 dsh 恰恰是
因为这一点分裂成两套路由——出货路径没有 E1、没有 dispatch 行。

## 1. 统一 dsh 的投递链路（原来夹具测的不是出货路径）

事实（核查得出）：

- 出货 bundle `cordis.patch.yml` 只挂 `mail-service` / `tools` / `mail-hub`，**没有** `inbound`；
- 所以 `dsh/inbound.ts` 的 `apply()`（监听、pull 循环、SOUL/skills provider、`deliverInbound`）在打包
  dsh 上从未运行；s6/s7/s8 与单测测的是这条**没被挂载**的链路；
- 真正投递的是 `mail-hub.deliver()`，它用 `routeThread()`：只有 E2（thread 复用），没有 E1（直接回复），
  也不写 dispatch 日志 ⇒ E1 在出货 dsh 上不存在，s7/s8 的证据在出货路径上不可能出现。

改动：

| 位置 | 改动 |
|---|---|
| `src/dsh/inbound.ts` | 重写为**纯投递核心**：`deliverInbound()`（= `decideInboundDelivery` → 投递/resume/新建 → `stampThread` → `logAimailDispatch`）、`deliverPulled()`、`resolveInboundTarget()`、`toInboundResult()`、`installDshInboundProviders()`。删除 HTTP handler、HMAC 校验、`apply()`、`startInboundPull()`（pull 由 hub 负责） |
| `src/dsh/mail-hub.ts` | `deliver()` 不再自己路由：调用 `deliverInbound(ctx, {cfg, agentAddr}, payload, headers, {workspace, agents, agentOptions, sessionExists})`，结果用 `toInboundResult()` 转成 ack 判据；`start()` 里装 SOUL/skills provider（这两个原本也在未挂载的 `apply()` 里，等于出货 dsh 一直没有 SOUL/技能枚举） |
| `src/runtime/session-router.ts` | 删除 `routeThread()`（连同 `runtime/index.ts` 的导出）。路由只剩 `decideInboundDelivery()` 一处 |
| 测试 | `test/dsh/mail-hub.test.ts` 现在走的就是出货链路；`test/core/runtime-router.test.ts` 只留 `threadRoot`/`normalizeMessageId` |

副作用（都是修正，不是放宽）：新建会话现在落在**绑定 workspace 的真实路径**（`meta.cwd = workspace.path`），
而不再是 `process.cwd()`；thread 指针写 root（§9.4 的路由键）+ 每条消息的 provenance 两个位置；
`handle.agent.whenIdle` 变为可选（宿主或测试替身没有它时不能使投递失败）。

## 2. 彻底移除本地端点（回环监听在 SSE-V2 上没有价值）

删除：

- `src/core/listen.ts`（`listenWithRetry`，含端口冲突重选逻辑）；
- pi：整段 `node:http` 监听 + HMAC 校验 + `--inbound-live/--inbound-url` 上报 + `setInboundEndpoint`/
  `inboundWebhookUrl`；`AIMAIL_TRANSPORT` 非 `sse` 现在只记一条 warning（不再复活监听）；
- dsh：`createInboundHandler` + `createServer`（见 §1）；
- openclaw：网关内 `registerHttpRoute` + `createInboundHandler` + `openclawWebhookUrl`；
- `src/core/inbound-notify.ts`（`--inbound-live`/`--inbound-down` 通道，已无调用方）；
- 契约键 `inbound_path` / `inbound_port`（清单 version 3 → 4）与两侧镜像常量、`inboundUrl()`。

保留：v1 deer-flow 适配层自己的路由字符串（`_V1_INBOUND_PATH` / `_INBOUND_PATH`），它是宿主自有 gateway
进程内的 route，仍在出货；该适配层退役时一起删。

守卫：`tests/contract/check-legacy-push-scope.py` 重写为 **no-local-endpoint** 门禁（L0）：
清单不得出现四个端点键、两个镜像不得再定义端点常量、源码不得出现 `listenWithRetry`/`createInboundHandler`/
`notifyInboundForSystem`/`setInboundEndpoint`/`inboundWebhookUrl`/`openclawWebhookUrl`，v2 面不得出现
`/aimail/inbound`、`/webhooks/aimail-inbound` 字面值，`pysdk/hermes/` 不得重现。
`tests/contract/check-docs-consistency.py` 的端口比对随之取消（没有端口值可比）。

## 3. 验证（本轮实际执行）

```
bash tests/sdk/l0-gate-tests.sh                 # PASS — all gates green
cd tssdk && npx vitest run                      # 562 passed / 63 files（含 packages/agent-aimail）
python -m pytest tests/sdk -q                   # 516 passed
npx tsc --noEmit -p tsconfig.json               # clean（两个包）
npm run build (agent-aimail)                    # tsc -b 通过
python -m pip wheel . -w /tmp/whl2              # 构建成功
```

删掉的测试：`test/pi/inbound-port-source.test.ts`（端口选择已无意义）、`test/core/listen.test.ts`、
`tssdk/test/inbound-notify.test.ts`、`tssdk/test/startup-hook.test.ts`（都只测被删的 push 面）；
`tssdk/test/pull-startup-hook.test.ts` 重写为"无端点 + 单一路由"的形状断言。

## 4. 未做 / 后续

- **同一轮的后续**：owner 随后要求"清掉所有 push/CLI 残留"，`openclaw aimail` 命令面、`sdk-ops`、
  `install.py`、`pysdk/deer-flow/`、`pysdk/hosts/` 也已删除，见
  `docs/qa/KEEP-ONLY-SSE-V2-2026-11-25.md`。
- **L2 未重跑**：s6/s7/s8 现在测的是出货链路（这是本轮的目的），但需要完整 journey（网关 + dsh 镜像 +
  LLM key，15–25 分钟）。期望值的变化：s7 应能看到 `reason: session-gone`，s8 应能看到
  `anchor: anchor-moved`——**在打包 dsh 上以前不可能出现**。
- v1 deer-flow 适配层仍是唯一的 push 面（宿主进程内 route），其退役是独立任务。
- **自动绑定残留已清空**（owner 澄清 2026-11-25："不存在自动默认绑定，都需要激活码激活绑定"）：
  绑定只能由激活码产生（`aimail_address` → `activateAddressCode` → `activateAddressCodePersist`），
  所以下面这些"启动即绑默认 agent"的接线全部删除，而不是保留成死代码：
  - `pi/identity.tryAutoBindOnce` + 一次性闸门、`openclaw/identity.tryAutoBindOnce` + 闸门；
  - `dsh/mail-service.ensureDefaultAgentBinding`（及 `resetAutoBindOnce`、默认 workspace 补写、指针写）；
  - `core/ensure-binding.ts`（`ensureAgentBinding`，唯一用途就是 auto-bind）与其测试
    `test/core/ensure-binding.test.ts`、`test/dsh/mail-service-ensure.test.ts`；
  - openclaw 的 legacy CLI 命令面（`openclaw aimail register` 等）**不再是默认开启**：现在需要显式
    `AIMAIL_LEGACY_CLI=1`。`AIMAIL_TRANSPORT=push` 只剩一条 warning。
- **仍然存在的一条非激活码绑定路径**：`openclaw aimail register`（legacy CLI，`commands.ts` 里的
  `autoBind`）。它现在默认关闭，但它确实能在没有激活码的情况下按"系统 + 派生的地址"写绑定，与
  "都需要激活码"不一致。是否连这个命令面一起删，需要 owner 一句话（删它要连 `commands.ts` 与其单测）。
