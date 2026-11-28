# L3（五宿主 · 真实用户旅程）开发侧接手：代码进度查验 + 计划

- 日期：2026-11-26 · 依据：质检 `docs/qa/L3-2026-11-26.md` + 仓库现状（gateway `v1.0.20 @ 599b840`）
- 目标（owner 口径）：L3 = **不主动装 SDK**，模拟真实用户：申请激活码 → 收激活邮件 → 把激活 prompt 贴进对话框 → agent 发现没有 `aimail_address` → **自助安装 SDK** → 重启 → 再执行 prompt → `aimail_address` 完成激活绑定 → welcome 校准身份 → 安全员批复 → 拿到身份卡 + 签名。覆盖 dsh / pi / openclaw / hermes / deerflow 五类。

---

## 1. 代码进度查验（事实，不是推测）

### 1.1 夹具现状

| 文件 | 覆盖 | 状态 |
|---|---|---|
| `tests/l3/run-l3.sh` | 入口（单 cell 执行器） | **硬拒非 dsh**（`L3_AGENT` 白名单只有 dsh），五宿主跑不了 |
| `tests/l3/run-v2.sh` | 本地/CI 包装器（`--cell platform/agent`） | 有 cell 机制，但矩阵只有 dsh；`SKIP_LEGACY` 分支把 hermes/pi/openclaw 归为退役 |
| `tests/l3/dsh-v2.sh` | dsh 完整旅程 | 存在；质检本地 PASS |
| `tests/l3/journey-pi.sh` | pi 旅程 | 存在（另有 `.wip` 旧稿）；质检 linux-amd64 本地 PASS，macos CI FAIL（"never installed"） |
| `tests/l3/journey-openclaw.sh` | openclaw 旅程 | 存在；nudge 已改（装/绑分两回合），**尚未跑完整旅程**，上轮停在 `[tools] dispatch_action failed` |
| `tests/l3/journey-hermes.sh` | hermes 旅程 | 存在；工具面 + `pip install agent-aimail` 均已验证可用，**卡在 turn 2（带 code 绑定）** |
| `tests/l3/journey-deerflow.sh` | deerflow 旅程 | **不存在**（质检：`prepare.sh` 的 deerflow `--home=/app` 需要容器基座） |
| `tests/l3/GATE.md` | L3 判定口径 | **写的是"dsh only"**（owner 2026-11-23 裁定），与本次五宿主口径冲突，需要更新 |

### 1.2 质检已定位的三个产品级阻塞

| # | 阻塞 | 证据 | 归属 |
|---|---|---|---|
| P0-1 | dsh 干净 CI 上 `status.json` rename ENOENT（本地不复现） | `[.../status.json.tmp → status.json] ENOENT` | **tssdk（已修，见 §2）** |
| P0-2 | **激活邮件只给方向、不给可执行命令** ⇒ hermes 去读文档、openclaw 工具面失败、pi CI 不装 | 三个宿主的 turn 日志 | 契约 + **网关**（文案在 `activation.rs:28`） |
| P0-3 | deerflow 旅程缺失 | — | 夹具（开发补） |

### 1.3 时序阻塞（必须先讲清楚，否则 L3 的"绿"没有意义）

L3 的判定前提是**已发布 SDK**（`run-v2.sh` 里 `npm view agent-aimail version`）。当前已发布的是旧线（npm `0.6.0` / pypi `agent-aimail 0.4.0`），它们早于本轮的 SSE-V2、**强制锚点**、以及 **v1.0.20 的按地址拉取退役**。所以：

- 用旧发布版跑五宿主 L3，会得到"旧 SDK + 新网关"的混搭结论，其中任何 410 都不是 L3 的真实失败；
- 用新网关 `1.0.20` 配旧 SDK 必然 410（`pull_scope_retired`）。

⇒ 正确顺序：**SDK 收尾（2 个用例）→ L2 全绿 → 发正式版（npm/pypi）→ 五宿主 L3**。L3 的"本地夹具验证"可以提前做，但只能用**开发版 SDK**（打本地 wheel/npm pack 注入），结论要标注为"夹具层验证"，不能当成发版门禁。

---

## 2. 本轮已修（P0-1：dsh `status.json` 竞态）

质检分析正确：`restart` 让**旧 hub 的收尾写**与**新 hub 的首次写**重叠，而两者共用同一个 `${statusPath}.tmp`，先到者 rename 后后者 `rename` ⇒ ENOENT。本地机器快、窗口小 ⇒ 不复现；CI 慢 ⇒ 稳定复现。

修法（`tssdk/packages/agent-aimail/src/dsh/mail-hub.ts::writeStatus`）：

```ts
writeStatusSeq += 1
const tmp = `${p}.${process.pid}.${writeStatusSeq}.tmp`   // 每个写入者独占临时名
try { await fs.writeFile(tmp, …); await fs.rename(tmp, p) }
catch (e) { await fs.unlink(tmp).catch(() => {}); throw e }
```

- 唯一性由 `pid + 进程内自增` 保证（写入者只有进程内 timer 与一个短命前任，不需要随机数）；
- 失败路径清理临时文件，避免残留；
- `tsc --noEmit` clean。

**验收（待做）**：dsh 的 CI 四个平台连跑两次均 PASS；并补一条并发写回归测试（两个 hub 同 HOME 同时 `start()`，断言 `status.json` 可解析且无 ENOENT）。

---

## 3. P0-2：激活邮件/prompt 必须给**可复制的逐宿主命令**

现状（契约 §6 与 `activation.rs:28` 同文）：

> If `aimail_address` is unavailable or unregistered, install the `agent-aimail` plugin using your platform's plugin install command …

"用你平台的安装命令"对 agent 不是一个可执行动作。仓库里**已有**的逐宿主命令（README/playbook）：

| 宿主 | 命令 |
|---|---|
| dsh | `dsh plugin --profile web add agent-aimail` |
| pi | `pi install npm:agent-aimail` |
| openclaw | `openclaw plugins install agent-aimail --force --accept-capabilities` |
| hermes | `pip install agent-aimail`（entry point 自动发现） |
| deerflow | `pip install agent-aimail` + 宿主 extension 注册（待 deerflow 旅程确认写法） |

建议改成"一行一个宿主"的清单 + 一句明确指令（"现在执行与你宿主匹配的那一行"），例如：

```text
If `aimail_address` is missing, install the plugin NOW with the command for your host:
  dsh:      dsh plugin --profile web add agent-aimail
  pi:       pi install npm:agent-aimail
  openclaw: openclaw plugins install agent-aimail --force --accept-capabilities
  hermes:   pip install agent-aimail
  deerflow: pip install agent-aimail
Then restart the agent runtime if the tool is still missing, and run the activation prompt again.
```

代价与影响面：契约 §6 文案 + `activation.rs` 的 prompt 常量 + `tests/sdk/test_v2_contract_surface.py` 的文案断言（三处必须同改，否则 L0 的文案一致性门禁会红）。**这是文案层面的产品决策，需要 owner 点头再动。**

---

## 4. 计划（按依赖顺序）

1. **SDK 收尾**：`agent-level-agentturn.test.ts` 剩 2 个用例（mock 时序，与拉取传输无关）→ 全绿；否则 L2/L3 都不该开始。
2. **P0-2 文案落地**（契约 + 网关 + 断言同改），并验证：任一宿主在**不给 nudge** 的情况下单轮内自助安装 + 绑定 + 回信。
3. **夹具改造**：`run-l3.sh` 的 `L3_AGENT` 白名单扩到五宿主；`run-v2.sh` 矩阵加 cell；`GATE.md` 从"dsh only"改为五宿主口径（含每宿主的失败归因规则）。
4. **补 `journey-deerflow.sh`**（容器基座 `/app` + pysdk wheel）。
5. **先本地跑通五宿主**（开发版 SDK 注入），把每个宿主的卡点收完：openclaw `dispatch_action failed`、hermes turn 2、deerflow 全流程。
6. **L2 全绿 → 发正式版 → GitHub runner 上跑五宿主 × 四平台**（硬件 + OS 覆盖）。
7. 每步产出证据文件（宿主版本、SDK 版本、网关版本、turn 日志、断言结果），按质检的既有格式落到 `docs/qa/L3-<date>.md`。
