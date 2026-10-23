# 改进要求（非退回件）：TS 侧 binding 的 gateway_url 应与 Python 侧一致，永远有

> 出具方：验证方　接收方：实现方　日期：2026-10-23
> 当前判定：**PASS 保持**（五家 docker 发版门禁 + 场景 1 本地回归全绿）。
> 本文件不是 HOLD，是 owner 2026-10-23 裁决的**下一版改进要求**：
> 「让 ts 侧与 py 侧保持一致，从 client 配置派生，永远有（gateway_url）」。

## 1. 现状（代码位置）

```
tssdk/packages/agent-aimail/src/core/address-code.ts:122  activateAddressCodePersist(client, code, emailAddress, opts)
tssdk/packages/agent-aimail/src/core/address-code.ts:140  gateway_url: opts.gatewayUrl      ← 绑定 agentmail.json
tssdk/packages/agent-aimail/src/core/address-code.ts:173  gateway_url: opts.gatewayUrl      ← 系统连接文件
tssdk/packages/agent-aimail/src/core/address-code.ts:31   interface RequestClient { request(...) }   ← 无 baseUrl
tssdk/packages/agent-aimail/src/core/gateway.ts:12        class GatewayClient { readonly baseUrl }   ← 其实有
```

调用方不传 `opts.gatewayUrl` ⇒ `undefined` ⇒ `JSON.stringify` 直接丢字段 ⇒ 落出**六字段** binding，
全过程无任何报错。Python 侧（`pysdk/aimail_tools.py` 的激活落盘）从 client 配置派生，永远七字段。

## 2. 为什么算问题（不是洁癖）

1. 七字段是文档契约（`docs/agent-self-setup.md` §1 与 A1-04/`S1-09` 断言的字段集），少一个字段的 binding 在 pull 循环、`repair`、后续 `renew` 上都是隐性坏数据。
2. **跨语言 parity 断裂**：同一个产品动作，py 宿主永远有 `gateway_url`，ts 宿主取决于作者是否记得传 `opts`。
3. 静默：没有报错、没有 warn，验收时只能靠"字段数"这一层兜住（我第一版场景 1 夹具就是这么拿到 6 字段的）。

## 3. 改进要求（owner 已裁决方向）

1. `activateAddressCodePersist` 写绑定与连接文件时，`gateway_url` 取 `opts.gatewayUrl ?? client.baseUrl`（或等价：把 `baseUrl` 加进 `RequestClient` 并从此派生）；
2. **两者都拿不到时显式失败**（抛错或返回 `{success:false, error:...}`），不允许静默写出少字段的绑定；
3. 补一条单测：**不传** `opts.gatewayUrl` 调 persist ⇒ 断言 `binding.gateway_url === client.baseUrl`，且字段集合与 Python 侧逐字一致；
4. 若同一份逻辑还有别的调用点（`fireBindingCreated` / 连接文件 / `auto-bind`），一并按同一派生规则处理，避免只修一处。

## 4. 验证方复验方式（改完我这边这么判）

- 场景 1 本地门 `tests/l3/run-s1-local.sh`：`S1-09`（绑定七字段）与 `S1-05b`（宿主内包解析）必须仍绿；
- 新增变体：故意**不传** `opts.gatewayUrl` 的 ts 调用 ⇒ 要么七字段、要么显式报错（两种都算达标，静默六字段算红）；
- A1 `L2/ts` 14 例与 `dsh` 插件路径门禁不回退。

## 5. 范围声明

不影响已发布的 `agent-aimail 0.2.2` / CLI `0.1.57` 的可用性；这是下一版的健壮性改进，随下一次 ts 侧发版一起验。
