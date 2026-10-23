# 复验交接：agent-aimail npm 包在干净项目无法安装（L2/ts 红）

> 出具方：验证方　接收方：实现方（worker）　日期：2026-10-23
> 判定：**HOLD（仅 L2/ts 与 TS 宿主安装面）**。单包化本身通过，PyPI 侧与 dsh 宿主侧均绿。

## 1. 阻塞事实（一条）

在**干净 npm 项目**里安装已发布的 `agent-aimail@0.2.1` 失败，退出码 1：

```
mkdir -p /tmp/x && cd /tmp/x && npm init -y >/dev/null
npm install --no-audit --no-fund agent-aimail@0.2.1     # rc=1
```

```
npm error code ERESOLVE
npm error ERESOLVE unable to resolve dependency tree
npm error   peerOptional @deepseek-ai/dsh-agent@"^0.1.0-rc.6 || ^0.2.0-rc.2 || ^0.2.1-rc.1" from agent-aimail@0.2.1
npm error Could not resolve dependency:
npm error   peerOptional @deepseek-ai/dsh-llm@"^0.0.1-rc.1 || ^0.2.0-rc.2 || ^0.2.1-rc.1" from agent-aimail@0.2.1
```

同一命令加 `--legacy-peer-deps` 可以装上，且 `agentTools()` 可用——但那是绕过解析，不是可交付的安装形态。

## 2. 影响面

| 路径 | 现状 |
|---|---|
| PyPI `agent-aimail` 0.2.0（hermes/deerflow 等 python 宿主） | **绿**：L2/py 14/14；场景 1 本地回归 hermes 18/0 |
| npm 经 dsh 自身插件管理器（`dsh plugin add agent-aimail`） | **绿**：场景 1 本地回归 dsh 19/0 |
| 直接 `npm install agent-aimail`（任何自己接 SDK 的 TS 宿主、L2/ts 门） | **红**：ERESOLVE |

含义：凡是"用户在自己 Node 工程里接 SDK"的方式目前装不上；只有宿主带依赖树的路径可用。

## 3. 根因（验证方的定位，不是修法指定）

单个包同时声明三家宿主（dsh / openclaw / pi）的 `peerDependencies`，其中 dsh 三条 range 解析出的版本互斥：`@deepseek-ai/dsh-session@0.1.0-rc.8` 要求 `dsh-llm ^0.1.0-rc.8`，而包的声明是 `^0.0.1-rc.1 || ^0.2.0-rc.2 || ^0.2.1-rc.1`，npm 无法同时满足 ⇒ ERESOLVE。`peerDependenciesMeta.optional` 只表示"缺了不报错"，不阻止 npm 去解析冲突。

## 4. 清关条件（三条都要）

1. 上述 `npm install agent-aimail@<新版本>` 在干净项目里 **rc=0，无需 `--legacy-peer-deps`**；
2. `agentTools()` / `activateAddressCodePersist()` 在装好的包里可用；
3. L2/ts 在我这里重跑转绿（14 cases / 0 fail），且 L2/py、L1/py、L1/ts 不回退。

## 5. 验证方这边的门禁口径（已落地）

- `host/l2-ts.sh`：`npm install` 失败且日志含 `ERESOLVE` ⇒ 判 `产品缺陷`（exit 1），**不再**判环境缺口（exit 2）；加 `--legacy-peer-deps` 不被接受为通过。
- 夹具已随单包化修正并推送：`aimail f27985f`、`aimail-advanced 0399fb2`。
- 当前 A1 门禁结果：L1/py 29/29 · L1/ts 13/13 · L2/py 14/14 · **L2/ts FAIL（本节缺陷）**。

## 6. 另需配套的一条（不属本节缺陷，但影响上线）

已发布的 CLI `0.1.56` 的安装步骤仍指向旧包名（实测发布资产输出 3 处 `aimailsdk`，仓库 HEAD 构建输出 3 处 `agent-aimail`）。因此新双包目前到不了真实用户安装路径；功能本身在 HEAD CLI 下已验通。**需要发一版装 `agent-aimail` 的 CLI**，发版后我用发布资产把场景 1 本地回归（hermes + dsh）再跑一遍，才算"上线后回归"成立。

## 7. 复现与证据

- 证据目录：`/tmp/verify-r3/a1-final/`（`l2-ts-install.log`、`l2-ts.log`）
- 用户形态复现脚本：`/tmp/verify-r3/npm-user-check.sh`
- 场景 1 本地回归证据：`/tmp/verify-r3/s1-newcli.log`（hermes 18/0、dsh 19/0）
