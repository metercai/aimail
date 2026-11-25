# 缺陷：dsh 在锚点绑定下零投递（A/B 已证实是代码）

- 日期：2026-11-25 · 发现者：verifier · 严重度：**P0（接收中断）**
- 对象：`tssdk/packages/agent-aimail`（dsh 拉取/投递路径）
- 坐标系：`aaa1a3d`（SDK）+ gateway `v1.0.17 @ ac30461`（本地构建，`GW_BIN`）

## 1. 现象
`dsh` 完成 v2.4 激活（`/apply` → 锚点已写入 `address_anchor`）后，邮件到达网关但**宿主一封不取**：

```
PASS  sender whitelisted before inbound
PASS  inbound stored (emails=2)
RED   observed max=2 final=2          ← pending 一直为 2
```
连带 `s6`（无会话证据）、`s7`（无 s7_gone.json）、`s8`（无新会话）全部 GAP。

## 2. A/B 证据（唯一变量 = 锚点行）
```
基线   锚点存在           → emails=2, final=2   → 一封没 ack（RED）
A/B    DELETE FROM address_anchor  → "queued then acked" → VERDICT PASS
```
同一套 SDK、同一个网关二进制、同一条投递流程。

## 3. 交叉对照（同一网关，别的宿主没事）
| 宿主 | `address_anchor` | 存信 | 投递 |
|---|---|---|---|
| hermes（pysdk） | 有 | 5 | ✅ |
| deerflow（pysdk） | 有 | 3 | ✅ |
| **dsh（tssdk）** | 有 | 6 | ❌ 0 |
| pi（tssdk） | **无** | 3 | ✅（强制未触发） |

## 4. 定位线索
- 网关侧强制点：`src/core/api/http.rs:3447` `parse_anchor_claims()` + `:3491` `enforce_anchor_binding`；
  头部格式 **`X-Aimail-Anchors: <email>;<sha256hex>[,<email2>;<hex2>]`**，缺项或值不符 ⇒ **403 + `anchor_mismatch`**（403 不打日志，所以网关日志里看不到，别被"无 403"误导）。
- SDK 侧：`ANCHOR_HEADER` 目前只出现在 `core/poll-entry.ts` 与 `core/anchor-id.ts`；
  **dsh 自己的拉取/投递路径（`dsh/mail-hub.ts`、`dsh/mail-service.ts`）没有引用它**。

## 5. 期望修法
1. dsh 的每一次已认证请求（尤其 SSE 拉取与 ack）都带上 `X-Aimail-Anchors`，值为该 agent 名下**全部**地址的 `email;sha256(workspace_path)`；地址集合来自绑定时下发的 `members`。
2. 同一条路径上的 403 必须**可见**（至少写一行 `dispatch`/`pull` 日志并带状态码），否则运维只能看到"邮件不动"。

## 6. 验收判据（我会重跑）
```
dsh  s1 s2 s3 → 全 PASS（不再出现 final>0）
dsh  s8       → 改绑定 workspace_path 后同一拉取 403 anchor_mismatch（负例必须红）
dsh  s6 s7    → 恢复可判（不再是被连带 GAP）
```

## 7. 附带问题（同一批，需要一并回答）
**pi 的绑定没有 `address_anchor` 行**。`/apply` 应当给每个激活地址打锚点；pi 没有 ⇒ 它在 s8 里永远不会被强制判，等于"没验"。
请回答：pi 的激活路径为何没写入锚点？是宿主差异、还是 `/apply` 的分支漏了？
