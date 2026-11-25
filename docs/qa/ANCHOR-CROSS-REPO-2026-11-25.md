# 跨仓联动：锚点标识随拉取由网关校验（2026-11-25）

owner 定案：本地账本"防君子不防小人"，改用**跨仓联动**——每次拉取请求带上
`hash(email_address + workspace_path)` 作为绑定的锚点标识，由网关校验。
`workspace_path` 是 **agent 侧的锚点**，不同 agent 类型不一定相同。

## 1. 设计（一句话）

**锚点永不出宿主，出宿主的是它的指纹。** 网关不理解"workspace"，只比对不透明标识。

```
激活：  POST /api/v1/activate-address-code  {code, email_address, anchor_id}
        anchor_id = sha256(lowercase(email) + NUL + 锚点)        ← 网关入库（address_anchor 表）
        ← 响应回显 anchor_id（客户端可核对，不靠假设）

拉取：  POST/GET /api/v1/agent-system/pending[/stream|/ack]
        X-Aimail-Anchor: <email>;<hash>[,<email>;<hash>…]         ← 一次请求覆盖该 ASID 的所有地址
        ← 网关逐地址比对：不一致或缺失（该地址已登记）⇒ 403 anchor_mismatch，一封不发
```

NUL 分隔是刻意的：否则 `sha256("ab"+"c") == sha256("a"+"bc")`。

## 2. 网关侧（`aimail-gateway`）

| 改动 | 位置 |
|---|---|
| 新表 `address_anchor(email_address PK, anchor_id, updated_at)` | `src/core/storage.rs`（建表 + 3 个方法：`set_address_anchor` / `get_address_anchor` / `list_agent_system_anchors`） |
| 激活接收 `anchor_id`（64 位 hex 校验）、入库、响应回显 | `src/core/api/activation.rs`（`activate_address_code_compat_handler`） |
| 三个聚合端点校验 | `src/core/api/http.rs`：`parse_anchor_claims` + `enforce_anchor_binding`，挂在 `agent_system_stream` / `list_agent_system_pending` / `ack_agent_system_pending` |
| 测试 | `storage.rs::anchor_tests`（3 项）+ `http.rs::anchor_header_tests`（3 项） |

刻意不做的：

- **不改** `domain_addr_meta` 表结构（避免动 5 处既有 SELECT）；锚点独立成表。
- **不解释锚点**、不存锚点原文：网关只见 64 位 hex，新增宿主类型（锚点语义不同）无需改网关。
- 未登记锚点的地址**不阻断**（老 key 轮换场景），因此可以按宿主分批上线、无需 flag day。

## 3. SDK 侧（两个面都改了）

| 位置 | 改动 |
|---|---|
| `core/anchor-record.ts` / `pysdk/v2/anchor.py` | `anchorId(email, anchor)` = sha256(lower+NUL+anchor)；`anchorHeader(bindings)` 生成 `email;hash,…` |
| 激活 | TS `activateAddressCodePersist` → `activateAddressCode` body 带 `anchor_id`；Python `activate_address_code_persist` → `activate_address_code(anchor_id=…)`；`provision.activateAddressCode`（dsh 链路）同样带上 |
| 宿主传递锚点 | dsh `address.ts` 用 `workspace.path`；pi 用会话 cwd；openclaw 用 agent workspace —— 都在**激活之前**传进去（原来只是事后回填） |
| 每次拉取 | `buildAgentSystemClient(..., members)` / `build_agent_system_client(..., members)` 把 `X-Aimail-Anchor` 放进 `extra_headers`，SSE/长轮询/ack 三条腿共用同一个 client |
| 拒绝处理 | 网关 403 `anchor_mismatch` 走既有 auth 三连 → `PullAuthError` → 该组成员记入"网关已拒绝"、不再布防 |

**本地账本已删除**（2026-11-25，同日）：`anchor.json` / `writeAnchorRecord` / `judgeAnchor`（及其 Python 版）
全部移除，SDK 只保留指纹计算（`core/anchor-id.ts` / `pysdk/v2/anchor.py` 的 `anchorId` / `anchor_id`）。
理由：权威判定已经在网关，本地再存一份只是把"两个文件能被一起改"的问题搬家；剩下的本地检查只有
激活章（`binding_source` / `activated_at`），它管的是"这份文件是不是激活产的"，与锚点无关。

连带效应：`aimail_address(action:'move')` 不再改写本地锚点，而是返回 `re-activation-required` ——
锚点变更必须走重新激活（新 code），否则本地改了而网关没改，该地址会在 `anchor_mismatch` 上永远拉不到信。

## 4. 攻击面（改绑这件事现在的成本）

| 攻击 | 结果 |
|---|---|
| 手改绑定文件把地址指向另一个 workspace | 拉取时指纹不同 ⇒ 网关 403，一封邮件都拿不到 |
| 手改 + 改本地 `anchor.json` | 同上——本地两文件自洽也没用，网关记的是激活时的指纹 |
| 真想把地址搬走 | 只能重新激活（新 code），网关记录随之更新，这是**显式可审计**的动作 |

仍存在的边界：拿到**有效 key** 的人可以冒充这个 agent 拉信（这是 key 的语义，不是锚点问题）；
锚点只在"这个地址属于哪个锚点"这一维上提供不可伪造的约束。

## 5. 验证

```
aimail-gateway   cargo test --offline --lib     433 passed（含 anchor 表 3 项 + header 解析 3 项）
aimail           l0-gate-tests.sh               PASS
                 pytest tests/sdk -q            449 passed（anchor relation 19 项）
                 cd tssdk && npx vitest run     522 passed（anchor-record 12 项）
```

跨仓一致性由契约 §13.3 锁定（中性措辞 + 头名 + 哈希定义），两侧实现按它各自被判。

## 6. 上线注意

1. **网关先上**（向后兼容：没有 `anchor_id` 的激活、没有头的拉取都放行）。
2. SDK 随后：新激活自动登记；老绑定在下一次重新激活时补登记。
3. 打开观测：`grep anchor_mismatch`（网关日志）+ `binding REFUSED`（SDK 日志）。
4. 全量登记后，可把网关侧"未登记放行"收紧为"未登记即拒"——那是一行判断，留待确认后再做。
