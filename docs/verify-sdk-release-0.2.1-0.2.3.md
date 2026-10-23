# SDK 发版验收：agent-aimail 0.2.1（PyPI）/ 0.2.3（npm）

> 出具方：验证方　日期：2026-10-23　判定：**PASS，已发布**
> 批次：`aimail` `4b4f94c`（含 `25ab8d9` gateway_url parity、`f5fb34e` 退役 set_public_whoami）
> 发版流水线：`publish.yml` run **39993692498** → `completed/success`，26/26 job 绿
> 前置 dry-run：run 39992443019 → success（形状核对：cli-release/pypi/l3 按设计 skipped）

## 1. 发布产物

| 产物 | 版本 | 证据 |
|---|---|---|
| PyPI `agent-aimail` | `0.2.0 → 0.2.1` | wheel `agent_aimail-0.2.1-py3-none-any.whl`；provenance: GitHub · metercai/aimail · workflow `publish.yml` · env `pypi-release` |
| npm `agent-aimail` | `0.2.2 → 0.2.3` | `publish-npm.sh: OK — 1 package(s) handled`；registry `npm view` = 0.2.3 |
| CLI `cli-v0.1.57` | 不变 | 本次未重建：tag 已存在 ⇒ Release 上传步跳过（见 §4） |

## 2. 装后产物（我亲跑）

```
PyPI 0.2.1（干净 venv）: dist 0.2.1 · import 落 site-packages
    tools=14 · set_public_whoami 不存在 · activate_address_code 存在
npm 0.2.3（空项目）    : version 0.2.3 · tools=14 · set_public_whoami 不存在
    activate_address_code 存在 · activateAddressCodePersist 已导出
```

工具数 15 → 14 是 `f5fb34e` 退役 `set_public_whoami` 的预期结果，两个 SDK 一致。

## 3. 地址级激活（发布产物上复验）

用**已发布的 npm 0.2.3**（不是工作树）跑我自己的脚本：起真 advanced 网关 → 走真申领链取码 →
`activateAddressCodePersist(client, code, addr, {})` **不传** `opts.gatewayUrl`：

```
6 pass / 0 fail
  ✓ 激活成功 · 绑定落盘 · 七字段齐全
  ✓ gateway_url == client.baseUrl
  ✓ 空 baseUrl + 不传 opts ⇒ 显式失败（"cannot derive gateway_url ... contract: 7 fields"）
```

## 4. CLI 资产未被本次覆写（发布前修的护栏）

SDK-only 发版会重跑 `publish.yml`，而 `cli-v0.1.57` 已存在；原来的 `action-gh-release` 步骤会把
四个资产重传一遍，用户按 sha256 钉住的哈希就会变。本次在 `cli-release` 里加了 `id: tag` +
`created` 输出，Release 上传步 `if: steps.tag.outputs.created == 'true'`。

实跑证据：`cli-v0.1.57 already on origin — nothing to create`，job success，且
`aimail-0.1.57-linux-amd64` 的 sha256 仍是 `05663735f4c8337c…`（与发版前一致）。

## 5. 多平台（发版流水线自带）

L3 **12 格全绿**（linux-amd64 / linux-arm64 / macos-arm64 / windows-amd64 × hermes / dsh-aimail / dsh-plugin）。
这 12 格在容器里装的是**刚从 registry 拉到的** 0.2.1 / 0.2.3 ⇒ 等于新双包的跨平台安装面覆盖。

## 6. 发布前本地门（同一棵树，`4b4f94c`）

```
SDK L0            PASS
tssdk tsc -b      ok；vitest 331 passed / 1 skipped
A1 门禁           PASS（L1/py 29 · L1/ts 13 · L2/py 14 · L2/ts 14）
per-agent docker  RELEASABLE ×5（hermes · dsh · openclaw · pi · deerflow）
check-versions    PASS（py 0.2.1 / ts 0.2.3 配对一致）
```

其中 deerflow 的 E4（产物新鲜度）本轮改了口径：该格跑的是**门禁构建的 wheel**，发布前必然
"容器内=0.2.1 > 已发布=0.2.0"。现在 `installed > published` 打印
`pre-release artifact under test` 并通过，`installed < published` 仍判红（`E4_ALLOW_AHEAD=0` 可回严格等值）。

## 7. 回滚

- npm：`npm dist-tag add agent-aimail@0.2.2 latest`（0.2.2 仍在 registry）；
- PyPI：0.2.0 仍可安装，需要时 `pip install agent-aimail==0.2.0`，必要时 yank 0.2.1；
- 无 CLI/网关/数据面变更，无 tag 变更 ⇒ 回滚只涉及包版本指向。
