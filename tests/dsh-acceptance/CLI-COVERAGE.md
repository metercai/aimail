# CLI 场景 2/3/4 的 DSH 覆盖与退役条件

> 结论先说：**DSH 场景 5/6 已覆盖 CLI 2/3/4 的“系统/地址接入”核心**，所以 **DSH 试点可以不再依赖 CLI 做 onboarding/activation**。但 **CLI 全量退役**还需要补 DSH 之外的主机适配、运维命令和多系统能力；本文件把边界写死，避免把“DSH 试点不依赖 CLI”误写成“CLI 立即全部退役”。

## 1. 覆盖矩阵

| CLI 场景/能力 | CLI 路径 | DSH 等价 | 状态 | 证据 |
|---|---|---|---|---|
| 2 共享域系统接入 | `aimail install` + system code | 场景 5：`aimail_system{code,system_name}` | ✅ 核心覆盖 | 单测 `scene 5 shared`；base/advanced 代码 |
| 3 独享域系统接入 | `aimail install` + system code | 场景 5：`aimail_system{code,domain}` | ✅ 核心覆盖 | `run-base-loop.sh`（base live） |
| 4 独立网关接入 | `aimail install` + system key | 场景 5：**该 base `/apply` 申领 system code** + `aimail_system{code,domain}` | ✅ 核心覆盖（agent 对话口径） | `run-base-independent.sh`（base live） |
| 系统默认工作员 | `install` 注册 default | 场景 5 自动建 `agent@domain` / `agent.system_name@shared`，绑默认工作区 | ✅ | `run-base-loop` |
| 地址级激活（无 system） | 地址码 + SDK | 场景 1：`aimail_address{code,gateway_url}`（B2） | ✅ | `run-advanced-b2.sh` |
| 系统对接后地址 | `aimail address` | 场景 6：`aimail_address{email,code?}`（B1） | ✅ | `run-base-loop`（有码）/`run-base-independent`（无码） |
| 地址 pause/resume | CLI address | 场景 6 工具 pause/resume | ✅ | `run-base-lifecycle.sh` |
| 地址 rotation/renewal | CLI address/renew | 场景 6 工具 + 网关 kind | ✅ | `run-base-rotation.sh`；advanced 单测 |
| 多 domain | CLI/domain | 场景 5 `domains/domain_keys`；场景 6 选 key | ✅ | `run-base-loop` second.test |
| `aimail welcome` | CLI | **未覆盖**（可用 `role_calibrator` 手工触发，但未做成 onboarding 步骤） | ❌ | — |
| `aimail check/stats/repair` | CLI | **未覆盖**（插件只有 `status.json` / hub status） | ❌ | — |
| 多 host/多 system | CLI 多平台 | DSH 单 profile；pysdk/openclaw/pi 仍走 SDK/CLI | ❌（DSH 外） | — |
| bridge/push | CLI/bridge | DSH 只用 SSE；其他 host 仍可能用 push | ⚠️ 不适用 DSH | — |
| base 启动/建域/取码 | CLI/admin | base 容器启动后可 `/apply`；建域目前需 admin API/CLI | ⚠️ 运维侧 | 测试栈 admin harness |

## 2. “DSH 不依赖 CLI”的代码证据

- `cordis.patch.yml` 只挂：`mail-service`、`tools`、`system`、`mail-hub`；**没有** `mail-inbound` / `register-cli`。
- 场景 5：`system-core.ts` 走 HTTP `preflight/activate-system`；不 spawn CLI。
- 场景 6：`address.ts` 走 HTTP `preflight/activate-address-code` 或 `registerPullAddress`；不 spawn CLI。
- hub：只连 `/admin/pending/stream` + ack；不 spawn CLI。
- 旧 `inbound.ts`/`register-cli.ts` 仍在包内但未挂载；它们不是 DSH 路径。
- 插件写 `aimail_gateway.json` 是受审计例外，不依赖 CLI 生成该文件。

## 3. 全量 CLI 退役的前置条件

1. **主机平权**：pysdk（Hermes/deer-flow）、openclaw、pi 也要有等价的“对话内 onboarding/address”入口；本文件只证明 DSH。
2. **运维命令替代**：`welcome`/`check`/`stats`/`repair` 要么做成插件工具/状态页，要么明确这些不再由 CLI 提供。
3. **多系统/多 profile**：DSH 当前一个 profile 一个 system；CLI 的多 system 需要 per-profile 或新的作用域设计。
4. **base 运维入口**：自建 base 仍需要启动、建域、管理员配置；这些可以留在 CLI/管理面，不属于 agent 对话。
5. **push/bridge 主机**：依赖 webhook/bridge 的 host 仍需要 CLI；DSH SSE-only 不代表全部 host。
6. **测试门禁**：CLI 删除必须同步 `tests/cli`、`test-matrix.json`、L3 `dsh-aimail` cell（改为 `dsh-plugin`）。

## 4. 结论

- **DSH 试点**：场景 5/6 可以作为 CLI 2/3/4 的对话内替代；DSH 不再需要 `aimail install`/CLI 做系统或地址接入。
- **全量退役**：在完成第 3 节 1–6 项之前，不宣称 CLI 可删；CLI 仍服务其他 host、运维和多系统。
