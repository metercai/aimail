# 场景口径（1–6）

面向用户的旧口径来自 `aimail/README.md` 的 **Four install scenarios**；DSH 新增两个 agent 对话场景。两者不是替代关系，而是入口不同。

| 场景 | 入口 | 域 | 激活凭据 | DSH 工具/路径 | 关键判定 |
|---|---|---|---|---|---|
| **1** | Agent 对话，地址级 | 共享域 | 地址激活码 `shared_a-…` | `aimail_address bind{email,code,gateway_url}` | **没有 system**；地址形如 `name@shared`；preflight `code_type=address, scope=shared` |
| **2** | 命令行 | 共享域 | 系统激活码 | `aimail install` / bootstrap（**不是 DSH 工具**） | 系统级，CLI 维护；DSH 场景 5 是它的 agent 对话等价入口 |
| **3** | 命令行 | 独享域 | 系统激活码 | `aimail install` / bootstrap（**不是 DSH 工具**） | 系统级，CLI 维护 |
| **4** | 命令行 | 独立网关/独享域 | 已有 system key | `AIMAIL_ADMIN_KEY=<system key>` + `aimail install`（**不是 DSH 工具**） | 自建网关；不需要产品激活码 |
| **5** | Agent 对话，系统级 | 共享域 / 独享域 / 独立网关 | 共享/独享/**独立网关**都由网关 `/apply` 页面申领**系统激活码** | `aimail_system bind{...}` | 共享：`code+system_name`；独享/独立：`code+domain`；不接受 email / admin-key / system-key |
| **6** | Agent 对话，地址级，**系统对接后** | 共享域 / 独享域 / 独立网关 | 共享域：用 system 的 domain key（**不要用场景 1 的 shared code**）；独享/独立：地址码可选 | `aimail_address bind{email,code?}` | **必须有 system config**；共享域地址形如 `name.system_name@shared`；独享/独立形如 `name@domain` |

## 场景 1 与场景 6 的区别（重点）

| | 场景 1 | 场景 6 |
|---|---|---|
| 前置 | 无 system | 已有 system（场景 5/2/3/4 之后） |
| 域 | 共享域 | 共享/独享/独立 |
| 地址 | `name@shared` | 共享：`name.system_name@shared`；独享/独立：`name@domain` |
| 凭据 | 地址激活码（必须） | 共享域：system domain key；独享/独立：地址码可选或 domain key |
| 工具判定 | preflight `scope=shared` + 无 system | system config 存在 + preflight `system_id` 匹配 |
| 禁止 | 已有 system 再绑共享地址码（`system_mismatch`） | 无 system 直接绑本域地址（`needs_system`） |

## 场景 5 的三种子形态

| 子形态 | `aimail_system` 参数 | 说明 |
|---|---|---|
| 共享域系统 | `code`, `system_name`, `gateway_url` | 系统码绑定 `system_name`；默认工作员 `agent.<system_name>@shared` |
| 独享域系统 | `code`, `domain`, `gateway_url` | 系统码绑定 domain；默认工作员 `agent@domain` |
| 独立网关 | `code`, `domain`, `gateway_url` | 在自建 base 的 `/apply` 申领系统码；码兑换 system key；默认工作员 `agent@domain` |

- 所有场景 5 都用 `code`；**admin-key / system-key 永远不会进入 agent 对话**。
- 独立网关的码同样来自该 base 的 `/apply` 页面（`apply-system`）。
- `manager` 可选，只影响系统默认 manager；不是收码邮箱。

## 校验顺序（避免“先消费码再报错”）

`aimail_system`：
1. 有 code：preflight；`code_type` 必须是 system/product；绑定必须与 `system_name`/`domain` 一致；否则在激活前拒绝。
4. 已有 config：同 gateway 可重绑；跨 gateway `gateway_mismatch`；已有地址绑定但无 system `binding_conflict`。

`aimail_address`：
1. `email` 必须与 code 绑定一致。
2. 有 system：B1；无 system：B2（仅 shared code）。
3. 有 system + shared code → `system_mismatch`。
4. 无 system + 非 shared code → `needs_system`。
5. 无 code + 有 system → domain key 注册（场景 6）。
6. 无 code + 无 system → `needs_system`（场景 1 必须用地址码）。

## 与 CLI 场景 2/3/4 的边界

- 场景 2/3/4 仍由 CLI/bootstrap 负责；DSH **不调用 CLI**。
- DSH 场景 5 是“系统级 + agent 对话”的等价能力；不要把它描述成 CLI 场景。
- 场景 6 的“系统对接后地址激活”与场景 1 的“共享域地址级激活”是不同前置；测试/文档不能混用。
