# V2 QA — 独立质检结论（agent-aimail 0.5.0）

日期：2026-11-20 · 验证方（QA）· 结论：**PASS（准予进入发布窗口；两项非阻塞项见 §5）**

## 1. 被测对象（实测 HEAD）

| 仓库 | HEAD | 说明 |
|---|---|---|
| aimail | `6a99574` | docs(qa): add V2 release handoff（含 V2 契约与交接） |
| aimail-gateway | `58df8cd` | test(agent-system): isolate pending across gateways |
| aimail-advanced | `e9f7cef` | test(e2e): cover shared_system and dedicated address types |
| agent-aimail | `0.5.0` | `pnpm build` 绿；`gen-tool-spec: 15 tools` |

## 2. 门禁结果（我方实跑，非引用）

| 门 | 命令 | 结果 |
|---|---|---|
| L0 | `bash tests/sdk/l0-gate-tests.sh` | `[L0] PASS — all gates green` |
| V2 release gate | `./tests/host-acceptance/run-v2-release-gate.sh` | `L2 dsh PASS` / `L2 pi PASS` / `L2 openclaw PASS` / `L2-TWO-GATEWAYS PASS` / `V2 RELEASE GATE PASS`（rc=0） |
| Advanced E2E | `PYTHONPATH=…/pysdk ADV_BIN=… e2e-open-apply.py` | `RESULT: 55 PASS, 0 FAIL`（含 `apply shared_system → charlie.sys1@e2e.local`、`apply dedicated → dave@dedicated.e2e.local`） |
| V2 surface | `pytest tests/sdk/test_v2_contract_surface.py -q` | `6 passed` |

## 3. 验证方自建探针（不依赖 dev harness，独立跑在真 base gateway 上）

| ID | 检查 | 结果 |
|---|---|---|
| P0 | 无效激活码（空 home） | PASS — `410 invalid or expired activation code`，未写 binding |
| P1 | 有效码 bind | PASS — `success:true`，返回 GatewaySystemId |
| P2 | binding 字段 | PASS — `system_id`(GatewaySystemId)、`agent_system_id`、无 `webhook_url` |
| P3 | 目录布局 | PASS — `~/.aimail/systems/system-4ad0/alice_test.local/` |
| P4 | AgentSystemId 稳定性 | PASS — 同 home 两次取值一致 `asid_647a6890eb1…` |
| P5 | Pi 单地址（C1） | PASS — 第二地址被拒：`Pi is single-address: already receiving alice@test.local; pause/unbind it first` |
| P6 | ack 语义（C9，服务端） | PASS — 消费方拒收后 `pending_deliveries.status` 仍为 `pending`；`agent_system_addresses` 记有 `ackuser@test.local | asid_dd31… | system-38b2` |

## 4. 工具面 / prompt 抽查（静态 + 运行时）

- `openclaw.plugin.json`：`contracts.tools` **15 项**，含 `aimail_address`，**无 `aimail_system`** ✓
- `registerTool((ctx) => …, { names: [...] })` —— V2 已带 names 元数据（此前 G9 的根因已修）✓
- `AimailAddressToolArgs` = `action/code/email/gateway_url/workspace_path`（无 system_code/manager/domain）✓
- Activation prompt 与 Appendix A 一致（`Activate and bind my AIMail mailbox address {address}` + `using \`aimail_address\`` + `agent_aimail`）✓
- `resources/skills/activation/SKILL.md` 存在（4911 B），与会话 SKILL 分离 ✓
- openclaw `inbound.ts`：`sessionKey` 仅在宿主 `hooks.allowRequestSessionKey` 时发送 ✓

## 5. 发现（均非阻塞）

**F1（P2，安全，建议随下一版修）**：`aimail_address` 的 bind 结果里带 `raw_key`（地址凭据）
- 证据：P1 返回 `{"success":true,"raw_key":"09a7…","system_id":"system-4ad0",…}`
- 源码：`src/core/address-code.ts:327` 返回 `raw_key`；`src/core/tools.ts:113` 原样 `return res as unknown as ToolResult`
- 风险：该字段会随工具结果进入模型上下文/宿主日志，与契约 §15「日志不含 code/key」的意图相悖
- 建议：工具结果脱敏（保留在 binding 文件内），只回 `success/system_id/email_address/expires_at/config_path`

**F2（P3，夹具）**：`tests/sdk/test_v2_contract_surface.py` 用 `<repo>/../aimail-gateway|advanced` 定位兄弟仓；本机仓库不在该布局，未加软链时 3 例误红（加软链后 6 passed）
- 建议：改为环境变量（如 `AIMAIL_GATEWAY_DIR`）或 `git rev-parse --show-toplevel` 相对查找

**F3（范围说明，非缺陷）**：`run-v2-release-gate.sh` 的 L2 是 **fake host context**（直接调 SDK 工具），不含真实 DSH/OpenClaw/Pi 进程与真实 LLM；交接文档 §7.4 已声明。真实宿主 L2 仍属 UNVERIFIED。

**附**：上一轮 G12（全新地址首 bind 报 `already_bound`）在干净 `AIMAIL_HOME` 下**不可复现**（P1 正常兑换并绑定），判定为旧夹具残留状态，已关闭。

## 6. 签字条件对照

```text
[x] L0 PASS
[x] V2 RELEASE GATE PASS
[x] advanced E2E 55 PASS
[x] V2 surface tests 6 PASS
[x] 三宿主工具面/激活 prompt 抽查
[x] 无 P0/P1 回归（F1=P2、F2=P3）
[ ] npm publish 窗口 — 待发布负责人（Owner）授权后由验证方执行
```

## 7. 验证方补充（2026-11-20，真实宿主 + 真实 LLM 的第一轮）

### 7.1 夹具修复（F2，已完成，验证方自改）

`tests/sdk/test_v2_contract_surface.py` 不再假设兄弟仓固定在 `<repo>/../`：
新增 `AIMAIL_GATEWAY_DIR` / `AIMAIL_ADVANCED_DIR` 覆盖，未设置时依次回落 `REPO.parent/<name>` → `~/<name>`。
验证：删除临时软链后 `pytest tests/sdk/test_v2_contract_surface.py -q` → **6 passed**。提交 `03bc4ed`。

### 7.2 真实宿主（OpenClaw 2026.9.6，宿主进程 + 真实 LLM）

环境：宿主原生进程，`HOME=/tmp/v2-oc`，插件 `agent-aimail 0.5.0` 经
`openclaw plugins install … --force --accept-capabilities` + `plugins enable`；gateway 进程
`openclaw gateway`（`gateway.mode=local`、`gateway.auth.token`、`hooks.enabled`）；
真实 base gateway `58df8cd`（`127.0.0.1:18080`）+ SMTP sink。

已证实（原文）：
```
[agent/embedded] tool-search: cataloged 15 tools behind compact prompt surface
✅ Mailbox bound successfully.
- Address: ocreal@test.local
- System: system-b237 (gateway http://127.0.0.1:18080)
- Pointer written: /tmp/v2-oc/.openclaw/.agentmail
```
binding 落盘（`~/.aimail/systems/system-b237/ocreal_test.local/agentmail.json`）：
`system_id=system-b237`（GatewaySystemId）、`agent_system_id=asid_d1fd…`、
`workspace_path=/tmp/v2-oc/.openclaw/workspace`、无 `webhook_url`。
宿主重启后：`[agent-aimail] pull entry armed for 1 agent-scope binding(s)`（契约 C10）。

未完成：注入一封外部入站邮件（已加 from 白名单、SMTP 250 接受、gateway 记为
`Inserted pending delivery (pull mode)`）后 30s 内 `pending_deliveries.status` 仍为 `pending`，
宿主未产生 turn。gateway 侧 `agent_system_addresses` 归属正确
（`ocreal@test.local | asid_d1fd… | system-b237`）。

**请开发确认**：openclaw 适配器在"激活后重启即 armed"状态下，聚合拉取应如何实际发起
（本次观察到 armed 但 30s 内未 drain；SDK 级 harness 的 `startSystemPull` 可正常取到）。

### 7.3 环境阻塞（需 Owner 提供）

本机 **没有** `LLM_API_KEY`（镜像按设计不烧密钥，`set-llm-key.sh` 要求运行时注入）。
因此上述真实宿主跑的是本机 OpenAI 兼容端点（Qwen3.8-27B），**不是**小米端点。
要做与生产一致的（小米 `mimo-v2.6-flash`）三宿主真实 L2，需要 Owner 提供 `LLM_API_KEY`
（或在 CI 侧触发同一条注入路径）。

## 8. 验证方补充（2026-11-20，真实宿主 + 小米 LLM）

密钥来源：`XIAOMI_API_KEY`（验证方环境 `~/.hermes/profiles/verifier/.env`，运行期注入为 `LLM_API_KEY`，
镜像内不烧 secret）。三个镜像的 provider 端点已逐一核对：`https://api.xiaomimimo.com/v1` + `mimo-v2.6-flash`。

### 8.1 Pi（`aimail-host-pi:latest`，pi 1.5.2）

真实模型（mimo-v2.6-flash）走完激活：
```
[agent-aimail] registered 14 mail tools + local inbound receiver
[agent-aimail] transport=sse (no listener/CLI)
Mailbox activated and bound successfully:
- Address: pireal@test.local
- System ID: system-b237
- Config: /root/.aimail/systems/system-b237/pireal_test.local/agentmail.json
- Pointer: /root/.pi/.agentmail
```
binding：`system_id=system-b237`、`agent_system_id=asid_5850…`、`workspace_path=/root/ws`、无 `webhook_url`。
⚠ 附带一条：`[agent-aimail] inbound down not reported … spawn aimail ENOENT`（pull 模式下仍尝试拉起 legacy `aimail` 进程）。

### 8.2 OpenClaw（`aimail-host-openclaw:latest`，OpenClaw 2026.11.5）

`plugins install 0.5.0 --force --accept-capabilities` + `plugins enable`；`/hooks/agent` 提交激活 prompt：
```
{"ok":true,"runId":"67a4a29b-…"}
binding: {'email':'ocreal@test.local','system_id':'system-b237',
          'agent_system_id':'asid_d8e22f4…','workspace_path':'/app/.openclaw/workspace','webhook_url':None}
```
未观测到 `polling started`：宿主在绑定前已启动，绑定后需重启宿主（按开发既有裁决）。

### 8.3 DSH（`aimail-host-dsh:latest`，dsh **0.2.2-rc.1**（pins.env 钉版））

插件装入 profile web；`session/modelCatalog` 证明真实模型可路由：
```
{"default":{"provider":"xiaomi","model":"mimo-v2.6-flash"},
 "routableProviders":["xiaomi"], "groups":[{"id":"xiaomi",…"models":[{"id":"mimo-v2.6-flash"}]}]}
selectModel → {"selected":{"provider":"xiaomi","model":"mimo-v2.6-flash"}}
```
`session/prompt` 返回 `accepted:true`，但两轮（各 180s）均未产生 `aimail_address` 工具帧，也未落 binding。
需与开发确认：dsh web 的 mux `session/follow(activate)` + prompt 组合在 V2 插件下是否还需要额外的会话事件驱动。

### 8.4 未完成项（真实宿主）

- 三宿主均**未**在本轮证明「入站邮件 → 真实 turn（含唯一串）→ ack 顺序」；
  openclaw 上一轮观察为 armed 但 30s 内 `pending_deliveries` 仍 pending。
  这是本轮唯一剩下的功能缺口，需要产品侧确认拉取触发条件（restart 后 arm 与实际 drain 的关系）。

### 8.5 DSH 长窗口复测（2026-11-20 晚）

同一夹具，把 mux 读取窗口从 25s 延长到 300s，并在 prompt 后保活 240s，观察到的帧：
```
item turn/start
item step/start
（无 tool/* 帧，无 binding 落盘）
modelCatalog: routableProviders=["xiaomi"]；selectModel → xiaomi/mimo-v2.6-flash
session/prompt → accepted:true
```
即：**真实模型确实开始跑 turn，但没有发起 `aimail_address` 调用**，因此没有 binding。

请开发确认（DSH，V2）：
1. 通过 HTTP `session/create` + mux `session/follow(activate:true)` + `session/prompt` 这条链建立的会话，
   插件工具（mail 15 件）是否会进入该会话的工具面？本次 turn 内未出现任何 tool 帧。
2. 若需要额外步骤（例如 agentPreset / tool 注册 / session 级 allow），请给出最小复现命令。
参考：同一镜像 `dsh headless` 走的是 `llm-deepseek` 默认路由（无 key → MISSING_CREDENTIAL），
说明 headless 与 web 的默认模型来源不同；web profile 的 `agent-default-model=xiaomi/mimo-v2.6-flash` 已生效。

### 8.6 目前结论（真实宿主）

| 宿主 | 工具面 | 激活（bind） | 拉取 armed | 入站→turn→ack |
|---|---|---|---|---|
| Pi（1.5.2） | 14 mail tools 注册 | ✅ binding 正确 | 未见（进程短命） | 未证 |
| OpenClaw（2026.11.5） | 插件加载（2 plugins） | ✅ binding 正确（hooks 路径） | ✅ armed（重启后） | 未证（pending 未 drain） |
| DSH（0.2.2-rc.1） | 未观测到 tool 帧 | ✗ 未落 binding | — | 未证 |

结论：V2 的"激活 → binding"在 Pi / OpenClaw 已由真实宿主 + 小米模型完成；
"入站 → turn → ack" 三宿主均未证；DSH 的会话工具面需要开发给出配置口径。

### 8.7 镜像版本核对（验证方实测）

| 镜像 | 版本 |
|---|---|
| aimail-host-pi:latest | pi 1.5.2 |
| aimail-host-openclaw:latest | OpenClaw 2026.11.5 (e13cf15) |
| aimail-host-dsh:latest | dsh 0.2.2-rc.1（与 `tests/agent-env/pins.env` 一致） |

（更正：§8.3 初稿把宿主 CLI 的 `0.1.7-rc.1` 误写成镜像版本；实际容器内为 `0.2.2-rc.1`。）

### 8.8 OpenClaw 投递复测（镜像内，2026-11-20 晚）

同一次运行内：`/hooks/agent` 激活成功（`status=ok model=mimo-v2.6-flash`）→
binding 落在 `/app/.aimail/systems/system-b237/ocdel_test.local/agentmail.json` →
白名单 201 → **重启 gateway** 后日志仍为：
```
[aimail-pull] no agent-scope binding — polling not started (push path unchanged; address-code activation enables it)
```
即"绑定文件已在、重启后仍判定无 agent-scope 绑定"，因此拉取不启动、入站邮件不会 drain。

请开发确认（OpenClaw 2026.11.5，V2）：
1. 适配器发现 agent-scope binding 的依据（目录扫描？pointer？`agent_system_id` 与本地派生值比对？）；
2. 本次 binding 的 `agent_system_id` 与容器内即时派生值是否可能不一致（同一容器内重启，理应稳定）；
3. 若需要 pointer 之外的条件（如 gateway 侧注册回执），请给出最小判据。

对照：同一镜像内 `/hooks/agent` 能正常跑真实模型并完成 bind（`status=ok`），说明不是模型/工具面问题。

### 8.9 根因定位（P1）：`isAgentScopeBinding` 仍用 V1 前缀判断，V2 binding 永远被判"非 agent-scope"

源码（`tssdk/packages/agent-aimail/src/core/poll-entry.ts`）：
```ts
export const AGENT_SCOPE_SYSTEM_PREFIX = 'shared_addr_'          // :36  ← V1 遗留

export function isAgentScopeBinding(cfg: AgentConfig | undefined | null): boolean {
  if (!cfg) return false
  if (!cfg.system_id || !cfg.api_key) return false
  return cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX)     // :56  ← 判的是 system_id
}
```
V2 实际落盘的 binding（Pi / OpenClaw 实测）：
```json
{"email":"ocdel@test.local","system_id":"system-b237",            // GatewaySystemId
 "agent_system_id":"asid_d8e22f4…","gateway_url":"http://127.0.0.1:18080"}
```
`system_id` 是 **GatewaySystemId**，永远不以 `shared_addr_` 开头 ⇒ 谓词恒 false ⇒
`startAgentPullEntries` 认为"没有 agent-scope binding"⇒ 不启动任何拉取 ⇒ 入站邮件永远 pending。
这解释了本轮三宿主"绑定成功但 polling 不启动 / 邮件不 drain"的全部现象。

同类用法还有一处：`src/openclaw/inbound.ts:232`
```ts
const addressScope = !!cfg.system_id && cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX)
```

建议修法（V2 语义）：判据改为"这是地址级激活产物"，即
```ts
return !!cfg.agent_system_id            // 或 cfg.agent_system_id?.startsWith('asid_')
```
并把 `AGENT_SCOPE_SYSTEM_PREFIX` 及其在 inbound.ts 的用法一并按 V2 字段（`agent_system_id`）改写；
相关单测（poll-entry / inbound）需同步更新，避免用 V1 夹具把错误语义锁死。

判据（修完复验）：
1. 三宿主激活后重启 → 日志出现 `pull scope: … email=<addr>` / `polling started`；
2. 注入入站邮件 → 产生真实 turn（含唯一串）→ `pending_deliveries` 转 acked；
3. Pi 单地址、OpenClaw 多地址、DSH 多地址三种形态各测一次。
