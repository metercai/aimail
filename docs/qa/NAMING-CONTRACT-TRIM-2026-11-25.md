# 命名契约裁剪（`contract/aimail-contract.json`）— 影响面核查与落地

日期：2026-11-25 ｜ 触发：契约 v2.3 §18 第 21 条后记录的"未同步的相邻资产"

## 1. 原问题

`contract/aimail-contract.json` 是 **v1 时代**的 agent 侧命名契约，把三件事混在一起：

1. **命名边界**（skill/toolset 注册名、binding/pointer 文件名）——仍在役，且是真契约；
2. **本地接收端点**（`inbound_path` = `/aimail/inbound`、`inbound_port` = 9099）；
3. **pull/push 三态叙述**（"空 `webhook_url` = pull，非空 = push"）—— push 时代的投递模型。

V2 契约（`docs/aimail-host-sse-contract-v2.md`）的投递模型是 SSE + 长轮询，**没有 push/webhook 面**。
清单把第 3 条当通用规则，会让读清单的人（和夹具）以为"webhook 非空 = 宿主会推"，与 V2 判定冲突。

## 2. 只读影响面核查（结论 = 不能整段删）

| key | 是否仍有活引用 | 谁在引用 | 判定 |
|---|---|---|---|
| `agent_skill_name` / `agent_toolset_name` | 是 | `SKILL.md` frontmatter、hermes toolset ensure、门禁 | **命名契约，在役** |
| `binding_file` / `pointer_file` | 是 | 绑定文件写入/读取、生命周期测试、L3 夹具 | **命名契约，在役** |
| `inbound_path` | 是 | `tssdk/src/pi/{identity,index}.ts`（回环监听 + 激活回执 URL）、`tssdk/src/openclaw/{inbound,identity,index}.ts`（注册入站路由）、`tssdk/src/dsh/inbound.ts`（`createInboundHandler`） | **端点，在役，但不是投递契约** |
| `inbound_port` | 是 | 同上（默认 9099，冲突时 bind 探测重选） | **端点，在役** |
| `hermes_inbound_path` | 是 | 仅 `pysdk/hermes/aimail_hermes.py`、`pysdk/hermes/ensure_config.py`（**v1 适配器**） | **v1 专有** |
| `hermes_route_name` | 是 | 同上（`webhook_subscriptions.json` 的 route key） | **v1 专有** |

关键事实：`pysdk/hermes_plugin/__init__.py` 明写 "SSE-V2, no CLI, no webhook, no port"，V2 hermes 插件
根本不读 `HERMES_*`；而 pi/dsh/openclaw 的 V2 适配层**仍然**绑回环监听（pi `listenWithRetry`、dsh
`createInboundHandler`、openclaw 入站路由），所以 `inbound_path`/`inbound_port` **不能删**，只能降级定位。

（我第一版判断 `inbound_path`/`inbound_port` 也是 legacy，被这次核查推翻：删掉会打断三个在役适配层。）

## 3. 裁剪落地

### 3.1 清单结构（值一个没改，只改"它是什么"）

新增两节，把端点键按事实分组：

- `local_endpoint.keys = [inbound_path, inbound_port]`：**在役但不是投递契约**。文字明确
  "宿主投递能力由 `docs/aimail-host-sse-contract-v2.md` 判定，绝不看这个端点是否应答"。
- `legacy_push.keys = [hermes_inbound_path, hermes_route_name]`：**v1 专有**，列了 `consumers`
  与 `retire_when`（v1 hermes 适配器退出 wheel 时删键 + 删两侧镜像常量 + 删三态措辞）。
  三态说明移入 `legacy_push.v1_semantics_only`，并注明"不得用于判定 V2 宿主"。
- `notes` 重写：只留命名边界 + 端口归属裁决（owner 2026-10-25），去掉 push 作为通用投递模式的表述。
- `version` 1 → 2（结构变化；无门禁读取该值，已核）。

### 3.2 两侧镜像（只加作用域注释，值不变）

- `pysdk/aimail_contract.py`：`INBOUND_PATH`/`INBOUND_PORT` 标注"端点、非投递契约"，`HERMES_*` 标注 `v1-only`。
- `tssdk/packages/agent-aimail/src/core/contract.ts`：同上。

### 3.3 新门禁 `tests/contract/check-legacy-push-scope.py`（已接入 `tests/sdk/l0-gate-tests.sh`）

两个方向都判，避免分类腐烂：

1. **正向**：`legacy_push.keys` 仍被声明的 v1 consumers 引用；不再被引用 ⇒ 红（应删键，而非留着）。
2. **反向**：V2 面（`pysdk/hermes_plugin`、`pysdk/v2`、`pysdk/deerflow_plugin`、
   `tssdk/src/{runtime,desktop,pi,openclaw}`）不得出现 legacy 常量或其字面值（注释不算）。
3. 结构：两组 keys 必须存在、互不重叠、能映射到镜像常量；manifest 形状变了 ⇒ rc=2 cannot-judge。

已做负向验证：临时在 `pysdk/hermes_plugin/` 放一行 `X = HERMES_ROUTE_NAME` ⇒ 门禁 FAIL 并点名文件，
删掉 ⇒ clean（34 个文件扫描）。

## 4. 验证

```
python3 tests/contract/check-legacy-push-scope.py        # clean
python3 tests/contract/check-contract-single-source.py   # PASS
python3 tests/contract/check-docs-consistency.py         # PASS（7/7 契约值仍与清单逐字一致）
python3 tests/contract/check-zero-bridge.py .            # clean
```

## 5. 未做（需要 owner 决策，不属本轮）

- **真正删除** `hermes_inbound_path`/`hermes_route_name`：必须先退役 v1 hermes 适配器
  （`pysdk/hermes/aimail_hermes.py` 仍在 wheel 里，被 `tests/sdk/test_legacy_surface_pin.py` 的
  `SHIPPED_LEGACY` 钉住）。
- **`local_endpoint` 的去留**：pi/dsh/openclaw 还在绑监听。若 V2 决定彻底不接收 push 回调，这三个
  适配层的 listener 与 `INBOUND_PATH`/`INBOUND_PORT` 应一起删——那是行为变更，不是命名裁剪。

## 6. 后续（同日，v1 退役后）

owner 在 2026-11-25 批准退役 v1 hermes，于是 `legacy_push` 整组删除，清单 `version` 2→3：

- 删除键 `hermes_inbound_path` / `hermes_route_name`，以及 `pysdk/aimail_contract.py` 的
  `HERMES_INBOUND_PATH` / `HERMES_ROUTE_NAME` / `hermes_inbound_url()`、`contract.ts` 的两个同名常量。
- `local_endpoint`（`inbound_path` / `inbound_port`）**保留**：仍是 pi/dsh/openclaw 的本地接收端点。
- `tests/contract/check-legacy-push-scope.py` 改为"退役不回潮"门禁：两个键/常量/字面值或
  `pysdk/hermes/` 目录重新出现即红；v2 hermes 面注册 webhook 也红。
- 详细退役清单见 `docs/qa/RETIRE-V1-HERMES-2026-11-25.md`。

因此第 5 节里"真正删除需要先退役 v1 适配器"这条已完成；只剩 `local_endpoint` 的去留（行为变更）。

## 7. 最终形态（同日，owner 决定移除本地端点）

`local_endpoint` 那一组也因此删除：`inbound_path` / `inbound_port` 从清单消失（version 4），
`core/listen.ts`、pi 的监听、dsh 的 HTTP handler、openclaw 的网关内路由、`inbound-notify` 通道一并移除。
清单现在只剩**命名**值（skill/toolset/binding/pointer）。详见
`docs/qa/UNIFY-DSH-DELIVERY-AND-DROP-ENDPOINTS-2026-11-25.md`。
