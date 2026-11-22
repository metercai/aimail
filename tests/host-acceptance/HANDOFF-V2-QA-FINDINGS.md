# AIMail V2 独立质检结论（验证方 → 开发）

- 日期：2026-11-20
- 结论：**HOLD（不予发布）**
- 范围：`agent-aimail 0.5.0` + gateway `58df8cd` + advanced `e9f7cef` + 契约 `docs/aimail-host-sse-contract-v2.md`
- 口径：L0/L2-gate/E2E/surface 由验证方实跑；真实宿主为 `pins.env` 的干净镜像 + 小米 `mimo-v2.6-flash`（`XIAOMI_API_KEY` 运行期注入）

---

## 1. 一句话结论

SDK/gateway/advanced 的**自测口径全绿**，但把同一份 0.5.0 放进真实宿主后，
**"入站邮件 → agent turn → ack"这条主链在 Pi / OpenClaw / DSH 三边都没跑通**；
其中两条是产品代码的确定性缺陷（各一行级），另外两条需要开发补日志/口径后复验；此外 V2 自助激活缺少 manager/白名单写入（B5，对 V1 的功能回退）。
因此本轮不能签字发版。

---

## 2. 已验证通过（验证方实跑，非引用）

| 门 | 命令 | 结果 |
|---|---|---|
| L0 | `bash tests/sdk/l0-gate-tests.sh` | `[L0] PASS — all gates green` |
| V2 release gate | `./tests/host-acceptance/run-v2-release-gate.sh` | `L2 dsh/pi/openclaw PASS` + `L2-TWO-GATEWAYS PASS` → `V2 RELEASE GATE PASS`（rc=0） |
| Advanced E2E | `PYTHONPATH=…/pysdk ADV_BIN=… e2e-open-apply.py` | `RESULT: 55 PASS, 0 FAIL` |
| V2 surface | `pytest tests/sdk/test_v2_contract_surface.py -q` | `6 passed` |
| 自建探针（真 base gateway） | 独立实现，不依赖 dev harness | 6/6：无效码 410 不写 binding；bind 返回 GatewaySystemId；binding 含 agent_system_id 且无 webhook；目录 `systems/<GatewaySystemId>/<addr>`；AgentSystemId 同 home 稳定；Pi 第二地址被拒 |

> 说明：`run-v2-release-gate.sh` 的 L2 是 **fake host context**（直接调 SDK 工具），
> 不含真实宿主进程；真实宿主结论见 §3、§4。

---

## 3. 真实宿主结果（干净镜像 + 小米 LLM）

镜像版本（实测）：`pi 1.5.2` / `OpenClaw 2026.11.5 (e13cf15)` / `dsh 0.2.2-rc.1`（与 `pins.env` 一致）。

| 宿主 | 工具面 | 激活（bind） | 聚合拉取 | 入站 → turn → ack |
|---|---|---|---|---|
| Pi | 14 mail tools 注册 | ✅ binding 正确（GatewaySystemId + agent_system_id + workspace，无 webhook） | 受 B1 阻断 | 未证 |
| OpenClaw | 插件加载（2 plugins），hooks 路径可跑真实模型 | ✅ binding 正确 | ⚠ 修 B1+B2 后聚合流 200 建立、邮件被读到 | ❌ 未进 session（B3） |
| DSH | 未出现任何 tool 帧 | ❌ 未落 binding | — | 未证 |

Pi 激活原始输出（示例）：
```
[agent-aimail] registered 14 mail tools + local inbound receiver
Mailbox activated and bound successfully: pireal@test.local / System ID system-b237
binding: {'system_id':'system-b237','agent_system_id':'asid_5850…','workspace_path':'/root/ws','webhook_url':None}
```

---

## 4. 阻断问题（按修复顺序）

### B1（P1，一行级）`isAgentScopeBinding` 仍用 V1 前缀，V2 binding 永不识别

`tssdk/packages/agent-aimail/src/core/poll-entry.ts:36,53-57`
```ts
export const AGENT_SCOPE_SYSTEM_PREFIX = 'shared_addr_'      // V1 遗留
export function isAgentScopeBinding(cfg) {
  if (!cfg?.system_id || !cfg.api_key) return false
  return cfg.system_id.startsWith(AGENT_SCOPE_SYSTEM_PREFIX) // 判错字段
}
```
V2 实际 binding：`system_id = <GatewaySystemId>（system-b237）`、`agent_system_id = asid_…`，
`system_id` 永不以 `shared_addr_` 开头 ⇒ 谓词恒 false ⇒ 日志
`[aimail-pull] no agent-scope binding — polling not started` ⇒ 拉取永不启动。

建议：`return !!cfg.agent_system_id`（或 `startsWith('asid_')`）；
同步检查 `src/openclaw/inbound.ts:232` 同一前缀用法与相关单测。

验证方实验（/tmp 改 dist，未进仓库）：改完后立即出现
`[aimail-pull] … polling enabled every 30000ms (limit 20, agent-scope key)`。

### B2（P1，一行级）适配器构造的 pull client 缺聚合路径与身份头

`src/core/poll-entry.ts:202-204`
```ts
const client = opts.clientFor ? opts.clientFor(cfg)
  : new GatewayClient(cfg.gateway_url, cfg.api_key, PULL_HTTP_TIMEOUT_MS, cfg.email)  // 缺 3 个路径 + 头
```
对照 `src/core/system-pull.ts`（`startSystemPull`）：
```ts
new GatewayClient(url, key, PULL_HTTP_TIMEOUT_MS, systemId, {
  streamPath: '/api/v1/agent-system/pending/stream',
  pendingPath: '/api/v1/agent-system/pending',
  ackPath: '/api/v1/agent-system/pending/ack',
  extraHeaders: { 'X-Agent-System-Id': agentSystemId },
})
```
验证方对照实验（同 gateway、同 binding、同封 welcome）：
`startSystemPull` → `onEmail fired`，pending 行随后消失；适配器路径 → 行一直 pending。

### B3（P1，需开发定位）OpenClaw：邮件被读到，但没有交给 agent session

修完 B1+B2 后（420s 窗口）：
```
[qa-poll] loop entering / round 0 inPoll=false hasStream=function hasProbe=function aborted=false
[qa-gw] STREAM /api/v1/agent-system/pending/stream status=200 ctype=text/event-stream
[qa-gw] GET /api/v1/contacts?addresses=ext-sender@example.com,ocfin@test.local   ← 邮件已读到
（无 turn、无 POST /api/v1/agent-system/pending/ack；pending_deliveries 仍 pending）
```
结论：拉取成立、消息已读；**inbound → session 交付这一跳没发生**。
此时不 ack 是**符合契约 §8.3** 的（未成功进 session 不得 ack），所以这不是 ack 缺陷。
请在该路径加一条结果日志（成功/失败原因），我方按日志复跑。

### B4（P1，需开发给口径）DSH：会话工具面为空

同一镜像 `session/modelCatalog` 已证明真实模型可路由：
```
{"default":{"provider":"xiaomi","model":"mimo-v2.6-flash"},"routableProviders":["xiaomi"]}
selectModel → {"selected":{"provider":"xiaomi","model":"mimo-v2.6-flash"}}
session/prompt → accepted:true
```
但 300s 窗口内只有 `turn/start`、`step/start`，**无任何 tool 帧**，未落 binding。
请确认：HTTP `session/create` + mux `session/follow(activate:true)` + `session/prompt` 建立的会话，
是否需要额外步骤（agentPreset / session 级工具开关）才能看到 mail 15 件工具，并给最小复现命令。
（参考：`dsh headless` 走 `llm-deepseek` 默认路由，与 web profile 的 xiaomi 默认模型不同源。）

### B5（P1，验证方复查意见）V2 自助激活只建 system_domains，不写 manager、不建白名单

代码事实（gateway `58df8cd`）：
| 路径 | 位置 | 写 system_domains | 写 domain_addr_meta(manager) | 建 whitelists |
|---|---|---|---|---|
| V1 管理端 `register_address` | `src/core/api/http.rs:690-712` | ✅ | ✅ `upsert_domain_addr_meta(…, Some(effective_manager), …)` | ✅ `create_whitelist_entry(email,'all',manager,…)` |
| V2 自助激活 `activate_address_code` → `ensure_agent_address_domain` | `src/core/api/activation.rs:117-119` → `src/core/storage.rs:1670-1689` | ✅（`INSERT OR IGNORE`，webhook NULL） | ❌ | ❌ |

配套事实：
- `activation_codes` 表无 manager 列；公开 `apply-address` 只收 `{email,name,agentname,domain}` ⇒ 自助流程里没有 manager 输入。
- 激活响应无 `manager_address` 字段（`activation.rs:750-764`），而契约 §7.1 要求 binding 可带 manager。
- 入站 SMTP 对未白名单发件人直接拒：`src/core/smtp/receiver.rs:555` → `550 Sender not whitelisted`
  （验证方实测：自助激活的地址收到外部邮件即 550，手工加 from 白名单后才 250）。

影响（按契约条款）：
1. §10「manager cc/回复是普通 inbound；welcome 的 persona/signature 与 manager 指令保持语义」→ 无 manager 写入 ⇒ persona 审批（`[CMD] approve persona`）无审批人；
2. §8/§9 入站投递 → 外部邮件 550，自助地址事实上收不到任何外部邮件；
3. §7.1 binding 的 `manager_address` → 响应不返回，客户端无从写入。

对"这是标准动作、与 SSE-V2 改造无关"的评审意见：
- **同意**的一半：manager/白名单与 SSE/聚合拉取无关，不影响 B1/B2 的传输修复；
- **不同意**的一半：这不是"与 V2 无关"，而是 **V2 自助激活对 V1 的功能回退**——它直接违背 §7.1/§8/§10 的三条承诺，
  且验证方已在真实链路观测到 550。属于 P1，应随本次发布修，不能留给"以后统一"。

建议修法（V2 激活路径内，保持幂等、不覆盖既有显式 manager）：
```rust
// src/core/api/activation.rs，ensure_agent_address_domain 之后
let existing = db.get_domain_addr_meta(&email).await.ok().flatten();
let manager = existing.filter(|m| !m.manager_address.is_empty())
    .map(|m| m.manager_address)
    .or_else(|| effective_manager(&state));            // 复用 V1 的 effective_manager（显式 → 系统默认 → 配置默认）
if let Some(m) = manager.as_deref() {
    let _ = db.upsert_domain_addr_meta(&email, &sid, Some(m), None, None).await;
    if let Err(e) = factory.create_whitelist_entry(&email, "all", m, Some("Agent <-> Manager (auto-created)")).await {
        warn!(operation = "auto_whitelist_failed", email = %email, error = %e);
    }
}
// 响应补 "manager_address": <m>
```

判据（复验）：
1. 自助激活后 `domain_addr_meta.manager_address = <默认管理员>`，`whitelists` 有 `(email,'all',manager)`；
2. 该 manager 发外部邮件 → SMTP 250（不再 550）；welcome cc manager；`[CMD] approve persona` 能生效；
3. 重复激活/换码激活幂等：不产生重复行、不覆盖管理员显式设置的 manager；
4. 激活响应含 `manager_address`，binding 落盘后 §7.1 字段齐全。

---

## 5. 非阻断项

| ID | 级别 | 内容 | 状态 |
|---|---|---|---|
| F1 | P2（安全） | `aimail_address` 的 bind 结果含 `raw_key`（地址凭据），会随工具结果进入模型上下文/宿主日志；源 `core/address-code.ts:327` + `core/tools.ts:113` | 建议脱敏（键只落 binding 文件），下一版修 |
| F2 | P3（夹具） | `tests/sdk/test_v2_contract_surface.py` 假设兄弟仓在 `<repo>/../` | **验证方已修**：env 覆盖 → 相对 → `~` 三级回落，`6 passed`（`03bc4ed`） |
| F3 | P3（镜像） | `aimail-host-dsh` 的 `cordis.patch.yml` 中 `agent-default-model` 块重复两次（同值无害） | 建议去重 |

---

## 6. 复验判据（修完请按此复跑，我这边一次跑完）

1. 三宿主激活后重启 → 日志出现 `polling enabled` + `pull scope … email=<addr>`（B1/B2 生效）
2. 注入入站邮件 → 30–60s 内 `[qa-gw] POST /api/v1/agent-system/pending`（或 STREAM 事件）→ 宿主产生真实 turn
3. turn 完成后 `pending_deliveries` 行消失（acked）；未进 session 时保持 pending（C9）
4. Pi 单地址、OpenClaw 多地址、DSH 多地址各一遍；DSH 需先给会话工具面口径（B4）

---

## 7. 环境与证据位置

- 密钥：`XIAOMI_API_KEY` 在验证方环境变量文件，运行期注入为 `LLM_API_KEY`；镜像不烧 secret
- 本地网关夹具：`/tmp/v2-e2e`（gateway `58df8cd`，`127.0.0.1:18080`，SMTP sink `12525`）
- 真实宿主日志：容器内 `/tmp/gw.log`（OpenClaw）、`/tmp/web.log` + mux 帧（DSH）、`pi -p` 输出（Pi）
- 验证方实验包（含 B1/B2 补丁与埋点）仅存在于 `/tmp`，未进入仓库
- 本轮夹具/交接提交：`03bc4ed` `ade4a01` `4f1aee3` `cbb24b2` `5ea7019` `8253c3d` `015031f` `36d3867`

---

## 8. 验证方自我更正（留存）

- 曾报"240s 内零 HTTP 请求"——实为验证方日志过滤器漏掉 `[qa-poll]`/`[qa-gw]` 行；补全后确认轮询循环在跑、STREAM 返回 200。
- 曾把宿主 CLI 版本 `dsh 0.1.7-rc.1` 误写为镜像版本；镜像实为 `0.2.2-rc.1`（与 `pins.env` 一致）。
