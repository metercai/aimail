# 地址绑定的信任链与"手写 agentmail.json"防线（2026-11-25）

问题："如何确保绑定都通过 `aimail_address` → `activateAddressCode` → `activateAddressCodePersist`，
而不是手写 `agentmail.json` 就绑上了？"

答案分两半：**文件不是权威（这一点已经由网关保证），我们补的是"未证明来路的绑定必须被看见/被拒绝"。**

## 1. 信任链：文件是缓存，权威在网关

```
激活码 ──POST /api/v1/activate-address-code──▶ 网关签发 per-address agent-scope key
                                             │
                    activateAddressCodePersist│ 把 key 落到 agentmail.json（0600，tmp+rename）
                                             ▼
宿主插件 ── 每封邮件的拉取/ack 都用这把 key 签名 ──▶ 网关校验：key 有效？属于该 agent/system？过期？
```

因此手写一份 `agentmail.json` 能拿到什么：

| 手写内容 | 结果 |
|---|---|
| 自己编的 `api_key` | 网关 401，永远拉不到信，也 ack 不了 |
| 别人的地址 + 自己的真 key | 网关按 **key** 决定地址，`email` 只是本地标签 ⇒ 拿不到别人的信 |
| 自己的地址 + 自己的真 key（从别处复制） | 等价于那次激活——因为权限本来就是这把 key，复制缓存不增加任何能力 |
| 缺 `agent_system_id` / 过期时间 | 本地会误判"这是 V2 有效绑定"，导致奇怪现象（见 §2） |

结论：**"手写文件就能绑"在能力层面不成立**，新地址的 key 只能由激活接口签发。

## 2. 缺的那一半：来路无法证明，且失败是静默的

原来 `isV2AgentScopeBinding` 只看 `agent_system_id` 非空，于是：

- 一份缺字段的半成品文件会被当作有效绑定；
- 没有任何地方记录"这条绑定来自激活"；
- 出了问题（拉不到信、拒收、会话乱挂）从日志看不出是"文件是手写的"。

## 2.5 三层校验：谁、何时、怎么

| 层 | 谁校验 | 何时 | 怎么 |
|---|---|---|---|
| **① 网关（权威）** | 网关 | **每次拉取 / 每次 ack** | 用绑定里的 `api_key` 做 HMAC/身份认证：key 有效？属于这个 agent-system？过期了吗？地址由 key 决定，不信文件里的 `email` |
| **② SDK 本地（自洽）** | SDK 运行时（TS `poll-entry` / Py `v2/binding`） | **布防时**（启动、re-arm、热加）+ **每封投递重读时** | 纯本地 JSON 判定：§7.1 七字段齐备 + `binding_source == "activate-address-code"` + `activated_at` 可解析 |
| **③ 网关裁定回灌（新增）** | 网关说、SDK 执行 | 拉取出现 **auth-fatal（401/403 连续 3 次）** 时 | 该组所有地址记入本进程"网关已拒绝"集合 ⇒ **不再布防**、该地址的投递记 `skip: unproven`，日志要求重新激活 |

要点：**章是"自洽标记"，不是签名**。凡是有文件写权限的人都能补上这两行；真正无法伪造的是 ①——
而 ③ 让 ① 的裁定立刻改变 ② 的结论（网关拒绝过 ⇒ 本地不再相信这份文件）。

因此"谁来校验"的准确答案是：**网关是唯一权威（①），SDK 只做自洽性（②）并把网关的裁定当成最终结论（③）**。

## 3. 已加的机制（本轮实现）

**写入侧只有一条路**：`activateAddressCodePersist`（TS）与 `activate_address_code_persist`
（Python）在写 `agentmail.json` 时盖两个章：

```json
{ "binding_source": "activate-address-code", "activated_at": "2026-11-25T10:00:00.000Z", ... }
```

**判定侧只有一个布防点**（"这条文件变成我们会替它收信"的那一刻）：

| 语言 | 位置 | 行为 |
|---|---|---|
| TS | `core/poll-entry.ts::v2Pool`（`startAgentPullEntries` 选 V2 绑定的唯一入口） | 校验 `core/binding-provenance.ts`：①§7.1 七个字段齐备（email/gateway_url/domain/system_id/agent_system_id/api_key/agent_id）②`binding_source == activate-address-code` ③`activated_at` 是可解析时间戳 |
| Python | `pysdk/v2/binding.py::is_armable` | 同一套 `binding_provenance()`；同一份字段表 |

失败时：**一律不布防**（**没有开关、没有兼容模式**——尚未发版，不存在"老版本写的合法绑定"；
缺章只可能是手写文件或写入方 bug，两种情况都必须拒），并打一条**带原因的日志**（每个地址一次）：

```
[aimail-pull] a@e2e.local: binding REFUSED — provenance not proven (no activation stamp …);
              re-bind with an activation code (aimail_address)
```

per-delivery 也会再判一次（`freshDeliveryTarget`）：即使流已布防，文件随后被换掉也不再投递，
该投递记为 `skip: unproven`（不 ack，邮件留待处理后重新拉取）。

**测试**：`test/core/binding-provenance.test.ts`（7 项）+ `tests/sdk/test_binding_provenance.py`
（15 项）覆盖：正常章、无章、伪造章、章无时间戳、逐字段缺失、strict 开关、默认只报不拒、
他自己的 AgentSystemId 才算自己的。

## 4. 仍然做不到的（说清楚，别给人虚假安全感）

1. **本机同用户自己改文件**：那是他的机器、他的 key，无法也不该阻止；能阻止的是"别的用户改"
   （写侧 0600/tmp+rename；读侧目前**不**复验权限——可作为下一步）。
2. **真 key 泄漏**：拿到有效 key 的人就等于拿到了那次激活的能力，这与"手写文件"是同一个问题，
   只能靠网关侧吊销（`expires_at` / 重新激活）解决。
3. **PI-Desktop 面**：它的 key 来自插件设置（用户手填），不经 `agentmail.json`，也不走这条激活链；
   它的校验在插件设置的 schema 里，不在这道闸里。
4. **网关侧不校验绑定文件名/路径**：网关只认 key。所以"文件从哪来"永远是本地约定，我们能做的是
   让违反约定的绑定**失效并说明原因**（默认即硬拒）。

## 5. 运维

- 激活链一定会盖章；**手写/半成品文件一律不布防**，无需任何开关。
- grep `binding REFUSED` 即可定位：列出的地址要么重新用激活码绑（`aimail_address`），要么删除该文件。
- 看到 `unknown binding_source 'register-address'` 这类日志：那是 v1 register 链写的残留文件，同样重新激活。
- 注意：§7.1 七字段规则只针对 V2 绑定（`agent_system_id` 存在）。V1 形态的绑定不参与聚合流，
  不在本判定内——但它们本身也不该再出现在"只保留 SSE-V2"的部署里。
