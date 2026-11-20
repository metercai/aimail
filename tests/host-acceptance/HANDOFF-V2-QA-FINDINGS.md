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
