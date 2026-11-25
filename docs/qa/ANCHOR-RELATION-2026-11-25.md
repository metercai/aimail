> **状态更新（2026-11-25 深夜）**：本文描述的 `anchor.json` 本地账本**已取消**。owner 定案："有了 gw 的
> 见证就不存在意外改动问题了"。现在锚点关系由**网关**见证（激活登记指纹 + 每次拉取校验），
> 详见 `ANCHOR-CROSS-REPO-2026-11-25.md`。本文保留作为历史记录：它记录了问题形态（为什么"改一行就能改绑"
> 是核心缺陷）以及本地账本方案的边界（两个文件同在一个 home，改得一致就绕过）。

# 地址 ↔ 锚点关系：不可再"改一行字段就改绑"（2026-11-25）

owner 指出：**激活绑定的核心语义是地址与锚点（dsh 里是 workspace）的一对一关系**；而当时这个关系
只存在于绑定文件里，任何人（包括 agent 自己）改一个字段就把邮箱搬到了另一个 workspace，
邮件从此进入另一个项目的会话，且没有任何地方发现。

本文记录：这个关系现在由谁保管、哪几条合法路径能改它、以及还差什么（需要网关配合）。

## 1. 问题的形态

契约 §9/§11 里，锚点是**硬性条件**：E1/E2/E3 全部在锚点内判定，C16（禁止跨锚点）、C17（session-gone
重挂）都以锚点为界。而锚点的值就是绑定文件里的 `workspace_id` / `workspace_path`。

改这一个字段的后果不是"配置错了"，而是**投递语义整体换到另一个 workspace**：
那个 workspace 的会话会收到这个地址的信，agent 的回信也落在那里。

改它的路径（改动前）有 4 条：

| 路径 | 现在的处置 |
|---|---|
| 手改绑定文件 | **拒绝布防**（本条新增，见 §2） |
| 工具 `aimail_address(action:'move')` | 保留，但**必须同时改写锚点记录**、留 `source: move` 审计（内核与 dsh 两处实现都改了） |
| 宿主适配层 `pinWorkspaceToBinding()`（pi/openclaw 绑定后回填当前 workspace） | 改为 **fill-only**：只在绑定还没有锚点时填，已锚定则拒绝并打印 `refusing to re-anchor …` |
| 激活 `activateAddressCodePersist` | 唯一的"铸造"路径，写入绑定 + 锚点记录（`source: activation`） |

## 2. 现在的机制：锚点记录（binding 之外的第二个副本）

`core/anchor-record.ts`（TS）/ `pysdk/v2/anchor.py`（Py）在地址目录下写一个独立文件
`systems/<sid>/<cleanAddr>/anchor.json`（0600、tmp+rename）：

```json
{ "email": "a@example.com", "workspace_id": "...", "workspace_path": "...",
  "pinned_at": "2026-11-25T10:00:00Z", "source": "activation" }
```

**布防时比对**（`judgeAnchor` / `judge_anchor`）：

- 绑定没有 `workspace_path` ⇒ 拒（`binding carries no anchor`）；
- 没有锚点记录 ⇒ 拒（`no anchor record … re-bind with an activation code`）；
- 绑定的锚点 ≠ 记录里的锚点 ⇒ 拒，且原因同时给出两边
  （`anchor mismatch: the binding says /work/B but the activation recorded /work/A (source: activation) — an address cannot be moved by editing the binding file`）；
- 只 id 不一致也拒。

判定点在原来的两处（`v2Pool` 布防、`freshDeliveryTarget` 每封重读），Python 侧在 `is_armable`。
行为是**硬拒 + 说明原因**，没有开关。

## 3. 边界（不吹牛）

- 两个文件都在用户 home 下：能同时一致地改两个文件的人，仍能改绑。区别是这不再是"改一行"，
  而且是**两份互相印证的书**，任何只改一份的行为都会被拒并留下日志。
- **真正不可伪造的做法**要求网关参与：
  1. 激活请求带上锚点（`{code, email_address, anchor}`），网关把 `地址 → 锚点` 存进库；
  2. 每次拉取的响应头（或 `whoami` 类端点）回带该锚点；
  3. SDK 比对本地记录与网关值，不一致即拒（并可让网关直接拒绝"锚点与库不符"的 key 使用）。
  这是跨仓改动（网关 + 契约 §7.1），需要 owner 与网关侧排期；本仓已把"本地一律先验"做完，
  将来接上网关时只需把 `judgeAnchor` 换成"网关值 vs 记录值"。

## 4. 测试

| 语言 | 文件 | 覆盖 |
|---|---|---|
| TS | `test/core/anchor-record.test.ts`（8 项） | 独立文件名/0600/无 tmp 残留；无记录拒；记录通过；手改拒绝且指出两边；缺锚点拒；id 不一致拒；`move` 改记录后新锚点通过、旧锚点拒绝 |
| Py | `tests/sdk/test_anchor_relation.py`（11 项） | 同上 + 端到端：同一份绑定只改 `workspace_path` ⇒ `is_armable` False；激活侧确实调用 `write_anchor_record` |
| 两语言 | `test/helpers/anchor.ts`、`tests/sdk/_anchor.py` | 夹具按真实写入方的样子造绑定（章 + 锚点记录），避免"靠宽松判定混过去" |

回归：`l0-gate-tests.sh` PASS · `tsc` clean · `vitest` 515 · `pytest tests/sdk` 441。

## 5. 与其它两件事的关系

- **provenance 章**（`binding-provenance.ts`）：管"这份文件是不是激活产的"；
- **锚点记录**（本文）：管"这份文件说的锚点是不是激活时的那个"；
- **网关裁定回灌**：管"网关是否已经否决了这把钥匙"。

三者都挂在同一个布防点，任一不通过 → 不布防 + 说明原因。
