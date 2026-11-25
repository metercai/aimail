# AIMail V2 发版前 QA 交接文档

版本：`agent-aimail 0.5.1`  
交接日期：2026-11-20  
用途：提交给 QA 做发版前最后一次独立验证  
状态：开发侧本地验收已通过；**尚未执行 npm publish**

---

## 1. 结论与范围

本次交付的是 AIMail Host SSE V2 主路径：

- 地址激活只有 `aimail_address`；
- activation prompt 内完成安装引导 + 工具触发；
- Gateway 返回 `GatewaySystemId`，本地按它分组；
- 本地 `AgentSystemId`，按 `(AgentSystemId, Gateway)` 聚合拉取；
- base 申请页只申请地址；
- advanced 支持三种地址形式；
- standalone 只申请独享域；
- DSH/Pi/OpenClaw 均接入 V2 拉取路径；
- CLI/pysdk 从 V2 主路径断开，仅保留 legacy。

**不在本次 QA 范围**：

- npm registry 实际发布；
- CLI/pysdk 旧代码物理删除；
- 真实 OpenClaw 宿主的完整 LLM 对话验收（仅本地 L2 与单测）；
- Gateway 生产环境的域名开通/审计运营流程。

---

## 2. 代码变更清单

### 2.1 Gateway（`aimail-gateway`）

| 文件 | 变更 |
|---|---|
| `src/core/api/activation.rs` | 允许无 webhook 地址申请激活码；删除 base `apply-system` 公网入口；地址邮件改为 V2 prompt；兼容激活 handler 采集 `X-Agent-System-Id` 并写归属 |
| `src/core/storage.rs` | 新增 `agent_system_addresses` 表；新增 `bind_agent_system_address` / `list_agent_system_addresses` / 聚合 pending、ack 方法；新增 A/B 与跨 Gateway 隔离测试 |
| `src/core/api/http.rs` | 新增 AgentSystemId 聚合 pending/ack/SSE 路由 |
| `src/core/api/activation.rs` | 删除不可达的 apply-system handler 与 dead code |

### 2.2 Advanced（`aimail-advanced`）

| 文件 | 变更 |
|---|---|
| `src/advanced/api/open_apply.rs` | `address_type` 支持 `shared` / `shared_system` / `dedicated`；standalone 强制 dedicated-only |
| `src/advanced/api/address.rs` | 新增 `/api/v1/public/address-domains`；激活写入 AgentSystemId 归属 |
| `src/advanced/api/email_notify.rs` | 地址激活/续期邮件使用 V2 prompt |
| `src/advanced/config.rs` | 新增 dedicated code TTL 配置 |
| `frontend/dist/assets/js/pages/public/apply-address.js` | 三类地址表单、域联动、系统标识名输入 |
| `tests/advanced/e2e-open-apply.py` | 补 `X-Api-Identity`；覆盖 shared / shared_system / dedicated；V2 prompt 断言 |

### 2.3 tssdk（`tssdk/packages/agent-aimail`）

| 文件/区域 | 变更 |
|---|---|
| `src/core/agent-system-id.ts` | 新增 AgentSystemId 派生（agent home + 硬件指纹） |
| `src/core/address-code.ts` | binding 写入 `agent_system_id`；激活请求携带 `X-Agent-System-Id`；新增 `move` |
| `src/core/gateway.ts` | 支持自定义 stream/pending/ack path 与额外签名头 |
| `src/core/system-pull.ts` | `agentSystemId` 聚合拉取 |
| `src/dsh/mail-hub.ts` | 每个 Gateway 一条 AgentSystemId 聚合流 |
| `src/dsh/address.ts` | 去掉 shared-only 限制；DSH 激活传 AgentSystemId；支持 unbind/move |
| `src/pi/index.ts` | B2 单地址走聚合流 |
| `src/openclaw/inbound.ts` | B2 走聚合流；sessionKey 仍按 host policy |
| `src/openclaw/tools.ts` | 15 工具面；移除 `aimail_system` |
| `openclaw.plugin.json` | contracts.tools = 实际 15 工具 |
| `src/core/agent-level.ts` | activation SKILL 独立安装 |
| `resources/skills/activation/SKILL.md` | 新增地址激活/生命周期 SKILL |

### 2.4 测试与文档

- `tests/host-acceptance/run-l2-local-gateway.sh`
- `tests/host-acceptance/harness.mjs`
- `tests/host-acceptance/run-l2-two-gateways.sh`
- `tests/host-acceptance/two-gateway-harness.mjs`
- `tests/host-acceptance/run-v2-release-gate.sh`
- `tests/sdk/test_v2_contract_surface.py`
- `docs/aimail-host-sse-contract-v2.md`
- `docs/v2-gap-and-implementation-plan.md`
- `docs/v2-release-notes.md`

---

## 3. 关键行为变化（QA 需知道）

1. **唯一激活工具**：`aimail_address`；不再有 `aimail_system`。
2. **激活 prompt**：

以契约 `docs/aimail-host-sse-contract-v2.md` **Appendix A** 为准（Gateway v1.0.16 实文）：
"Use `aimail_address` to activate and bind the dedicated AIMail mailbox {address} for this agent
(gateway: {gateway_url}; activation code: {code}). … install the `agent-aimail` plugin using your
platform's plugin install command … no reinstallation is needed."

> 本文不再复制完整 prompt：上一版复制后随网关改动变成了陈旧副本。引用段落号即可。

3. **GatewaySystemId**：激活返回，作为 `.aimail/systems/<GatewaySystemId>/<address>` 分组。
4. **AgentSystemId**：本地派生；SSE/聚合身份；不是 Gateway system_id。
5. **SSE 单位**：`(AgentSystemId, Gateway)` 一条；多 Gateway 多条。
6. **advanced 三类地址**：
   - `shared`：`agent@shared`
   - `shared_system`：`agent.system@shared`
   - `dedicated`：`agent@dedicated`
7. **standalone**：只允许 dedicated。
8. **Pi**：单地址，不允许第二个地址。
9. **CLI/pysdk**：V2 主路径不使用；旧文档已标记 Legacy。

---

## 4. 环境要求

- macOS 或 Linux；
- Rust toolchain + Cargo；
- Node.js + pnpm；
- Python 3.10+；
- 可执行 `openssl`（advanced E2E 使用）；
- 端口空闲：
  - L2 单 Gateway：18080 / 12525 / 12526
  - L2 双 Gateway：18090 / 18091 / 12525 / 12536 / 12537
  - advanced E2E：38080 / 25025 / 25126

> 这些端口组不能并发运行，必须串行执行。

---

## 5. QA 执行顺序

### 5.1 准备

```bash
cd /Users/metercai/Desktop/chains/aimail
git status
git log --oneline -5
cd tssdk && pnpm install && pnpm build
cd ../aimail-gateway && cargo build
cd ../aimail-advanced && cargo build
```

确认版本：

```bash
node -e "console.log(require('/Users/metercai/Desktop/chains/aimail/tssdk/packages/agent-aimail/package.json').version)"
# 期望 0.5.1
```

### 5.2 L0

```bash
cd /Users/metercai/Desktop/chains/aimail
bash tests/sdk/l0-gate-tests.sh
```

期望：

```text
[L0] PASS — all gates green
```

### 5.3 三宿主 + 双 Gateway 一键门禁

```bash
./tests/host-acceptance/run-v2-release-gate.sh
```

期望：

```text
L2 dsh: PASS
L2 pi: PASS
L2 openclaw: PASS
PI A2 ARMING PASS
OPENCLAW A2 ARMING (multi-address) PASS
L2-TWO-GATEWAYS PASS
V2 RELEASE GATE PASS
```

该门禁覆盖：

- DSH 单/双地址聚合；
- Pi 单地址聚合；
- OpenClaw 单/双地址聚合；
- **真实宿主 A2 武装**(pi 单地址 + openclaw 多地址,no-pointer 条件 ⇒ 各恰好 1 条 aggregate 流;`run-real-host-arming.sh`)；
- 真实两个 base gateway + 同一 AgentSystemId；
- 每 Gateway 一条聚合 SSE。

> **真实宿主 A2 步的环境要求**：需要本地 docker 镜像 `aimail-host-pi:latest` /
> `aimail-host-openclaw:latest`（`host-images.yml` 构建）。缺镜像时该步 exit 2
> （ENV GAP，非产品红）。§6.1 复验判据注记：字面 `polling enabled` 为 V1
> per-binding 行；V2 binding 必走 AgentSystemId 聚合，武装审计行为
> `no pointer — aggregate agent:<asid> … members=[<addr>…]` +
> `pull scope … fanout=true`（门禁即按此断言）。

### 5.4 Advanced 三类地址 E2E

```bash
cd /Users/metercai/Desktop/chains/aimail-advanced
cargo build
PYTHONPATH=../aimail/pysdk \
ADV_BIN="$PWD/target/debug/aimail-advanced" \
python3 tests/advanced/e2e-open-apply.py
```

期望：

```text
apply shared_system → charlie.sys1@e2e.local
apply dedicated → dave@dedicated.e2e.local
RESULT: 55 PASS, 0 FAIL — open-application e2e pre-flight
```

覆盖：

- 原有 apply/activate/SDK pull/SSE/ack/quota/license/lifecycle；
- shared / shared_system / dedicated 三种地址；
- V2 prompt；
- AgentSystemId 归属；
- `already_bound`、pause/resume/unbind/re-bind。

### 5.5 独立负向/边界检查

```bash
python3 -m pytest tests/sdk/test_v2_contract_surface.py -q
```

期望：

```text
6 passed
```

检查：

- V2 prompt 不含旧 self-setup；
- base `/apply` 无系统码入口；
- advanced 三种地址类型存在；
- standalone dedicated-only；
- manifest 15 工具，无 `aimail_system`；
- `aimail_address` 不含 `system_code/manager/system_name/domain`。

---

## 6. QA 验收矩阵

| ID | 场景 | 期望 |
|---|---|---|
| Q1 | `aimail_address` 唯一激活 | 无 `aimail_system` |
| Q2 | 激活 prompt | 含 install + `aimail_address`，无旧 guide |
| Q3 | base `/apply` | 仅地址申请；domain 来自 current domains |
| Q4 | advanced shared | 成功出地址码 |
| Q5 | advanced shared_system | 地址 `agent.system@shared` |
| Q6 | advanced dedicated | 地址 `agent@dedicated` |
| Q7 | standalone | shared/system-name 被拒绝 |
| Q8 | GatewaySystemId 目录 | `systems/<GatewaySystemId>/<address>` |
| Q9 | AgentSystemId 聚合 | 一个 Gateway 多地址一条流 |
| Q10 | 多 Gateway | 每 Gateway 一条流，互不串 |
| Q11 | A/B 隔离 | 聚合 pending/ack 只能看到自己的地址 |
| Q12 | DSH 双地址 | 一条流，两个 followup |
| Q13 | Pi 单地址 | 第二地址被拒绝 |
| Q14 | OpenClaw 双地址 | 一条流，两个地址送达 |
| Q15 | sessionKey | 未 opt-in 不发送；opt-in 后原样发送 |
| Q16 | unbind/move | 本地归档/迁移，不换 code |
| Q17 | legacy | V2 文档/工具面不引导旧 CLI/self-setup |

---

## 7. 已知限制 / QA 不要误判

1. **未执行 npm publish**  
   0.5.1 只是准备好；QA 验证的是本地/打包前代码，不是 registry 上的版本。

2. **CLI/pysdk 未物理删除**  
   V2 主路径不依赖；旧文档标记 Legacy。若 QA 发现旧 CLI 仍可运行，不是 V2 回退。

3. **advanced dedicated E2E 的域是测试夹具直接写 DB**  
   验证的是申请/归属/激活逻辑；真实 dedicated 域的生产开通流程需产品另验。

4. **OpenClaw 真实宿主未做完整 LLM L2**  
   本地 L2 使用 fake host context；真实宿主仍需 QA 按宿主版本验证工具面/sessionKey。

5. **`run-v2-release-gate.sh` 不包含 advanced E2E**  
   advanced E2E 需单独执行（见 5.4）。

6. **端口冲突**  
   双 Gateway 与 single L2 使用不同端口；若端口被占，脚本会失败，需先清理。

7. **测试会写 `/tmp`**  
   `aimail-local-gw-*`、`aimail-two-gw-*`、`/tmp/aimail_e2e_open_apply`；失败时保留日志可用于诊断。

---

## 8. 提交与版本

当前主仓库提交（交接快照）：

```text
aimail   9ecc303 docs(plan): record three-address E2E completion
gateway  58df8cd test(agent-system): isolate pending across gateways
advanced e9f7cef test(e2e): cover shared_system and dedicated address types
```

关键实现提交：

```text
gateway  bda201c feat(activation): allow pull addresses and remove system claim UI
gateway  b8b7e64 feat(agent-system): record address ownership by AgentSystemId
gateway  d4eab17 feat(agent-system): add aggregate pending and ack endpoints
gateway  e3589d6 feat(agent-system): add aggregate SSE stream
gateway  94f81ce fix(agent-system): persist ownership on compat activation

aimail   74ae2ee feat(v2): add AgentSystemId and address-only tool surface
aimail   140a5d4 test(l2): real two-gateway AgentSystemId delivery
aimail   14b941d test(l2): two-address aggregate delivery
aimail   f77e76e test(l2): DSH two-address aggregate delivery
aimail   9ecc303 docs(plan): record three-address E2E completion

advanced d9cee59 feat(apply): unify shared, system-name, and dedicated address applications
advanced 16118c5 feat(app): standalone only applies for dedicated addresses
advanced d780769 feat(agent-system): record advanced address ownership
advanced 78f327f test(e2e): align advanced open-apply with V2 prompt and identity
advanced e9f7cef test(e2e): cover shared_system and dedicated address types
```

版本：

```text
agent-aimail 0.5.1
```

> QA 验收以实际 `git rev-parse HEAD` 为准；如 HEAD 与快照不同，以提交说明和 `git diff` 复核。

## 9. 失败时需收集的证据

- 命令、完整 stdout/stderr；
- `git rev-parse HEAD`（三个仓库）；
- 失败用例的临时目录（`/tmp/aimail-*`）；
- Gateway 日志：`<work>/gateway.log`；
- advanced E2E：`/tmp/aimail_e2e_open_apply` 与 `config.toml`；
- 复现步骤是否在同一端口组串行执行。

---

## 10. QA 签字条件

以下全部满足才建议进入发布：

```text
[ ] L0 PASS
[ ] V2 RELEASE GATE PASS
[ ] advanced E2E 55 PASS
[ ] V2 surface tests 6 PASS
[ ] 三宿主工具面/激活 prompt 人工抽查
[ ] 无 P0/P1 回归
[ ] 发布负责人确认 npm publish 窗口
```
