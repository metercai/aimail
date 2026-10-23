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

---

# 实现方复查与修复（2026-10-23）

## 7. 复查结论：阻塞事实复现成立

环境 npm 11.12.1 / node 24.15.0。干净项目 `npm install --no-audit --no-fund agent-aimail@0.2.1` ⇒ **rc=1，ERESOLVE**，报错文本与上文逐字一致。判定为**产品缺陷**，不接受 `--legacy-peer-deps` 作为交付形态。

## 8. 根因更正（比"三家宿主互斥"更窄）

不是三家宿主互相冲突：openclaw / pi 两条 peer 是单包单线，实测各自在位时都能装（见 §10）。真正的断点是 **dsh 家族内部的 range 漂移**：

| 包 | 包内声明 | 上游实际需要 |
|---|---|---|
| `dsh-agent` | `^0.1.0-rc.6 \|\| ^0.2.0-rc.2 \|\| ^0.2.1-rc.1` | `latest` = **0.1.0-rc.6**，其 peer 要求 `dsh-llm ^0.1.0-rc.6`、`dsh-session ^0.1.0-rc.6` |
| `dsh-session` | （传递 peer） | `0.1.0-rc.8` 的 peer 要求 `dsh-llm ^0.1.0-rc.8` |
| `dsh-llm` | `^0.0.1-rc.1 \|\| ^0.2.0-rc.2 \|\| ^0.2.1-rc.1` | **缺 0.1.0 线** ⇒ 与 `^0.1.0-rc.8` 无交集 |
| `dsh-tools` | `^0.0.1-rc.1 \|\| ^0.2.0-rc.2 \|\| ^0.2.1-rc.1` | 同样**缺 0.1.0 线** |

即：`dsh-agent` 声明含 0.1.0 线，`dsh-llm` / `dsh-tools` 却只有 0.0.1 / 0.2.x —— 三条声明不对称，npm 按 `latest` 解析出的组合必然落进空档。

同类问题在 `docs/l3-product-defects.md` S-1 已发生过一次（peerDeps 钉旧系列，dsh 升级后拒装）。这说明**单点改 range 不够，必须加"发版前真装一次"的门**（§9）。

## 9. 修复（版本 0.2.2）

1. **peer 范围对称化**（`tssdk/packages/agent-aimail/package.json`）：
   - `dsh-llm` / `dsh-tools`：`^0.0.1-rc.1 || ^0.2.0-rc.2 || ^0.2.1-rc.1`
     → `^0.1.0-rc.6 || ^0.2.0-rc.2 || ^0.2.1-rc.1`
   - 去掉 `^0.0.1-rc.1`：0.0.1 线与 `dsh-agent` 支持的 0.1.0+ 互斥，属**错误声明**，保留只会诱导 npm 解析出不兼容组合。
2. **防复发门禁**（`tests/sdk/release/l2-check-tarball.sh` 新增第 9 步）：
   发布前拿**真实 tarball** 在干净项目里真装两次，都必须 rc=0，且**禁止 `--legacy-peer-deps`**：
   - G1 裸项目（无宿主）——npm 仍要完成 peer 解析校验；
   - G2 预装 `dsh-agent@latest`——传递 peer 树全部物化，正是互斥触发路径。
   该门已在 `scripts/publish-npm.sh` 的发布前链路（L134），坏包发不出去。

## 10. 验证矩阵（0.2.2 tarball 实测）

| 场景 | 结果 |
|---|---|
| 干净裸项目 | **rc=0** |
| 预装 `dsh-agent@latest`（0.1.0 线） | **rc=0** |
| 预装 dsh 0.2.1-rc.2 线（agent+llm） | **rc=0** |
| 预装 `openclaw` | **rc=0** |
| 预装 `@earendil-works/pi-coding-agent` | **rc=0** |
| 负向：0.2.1 tarball（旧声明） | **rc=1 + ERESOLVE**（门禁能抓） |
| `dsh plugin add agent-aimail` 路径 | 未变（只放宽 range，不动 plugin 装配） |

导出面（清点装好的包）：`dist/hosts/index.js` 共 132 个导出，`agentTools` / `activateAddressCodePersist` 均为 function ⇒ 清关条件 2 满足。
回归：tssdk `vitest` **296 passed / 1 skipped**（peer 改动不影响运行时代码）。

## 11. 第 6 条（CLI 旧包名）已在 2026-10-23 的发布 run 中解除，但产生一个新问题

同日 `publish.yml` 的 `cli-release` job 重传了 `cli-v0.1.56` 的二进制资产（softprops action 更新模式），所以：

- 实测发布资产：`aimailsdk` **0 次** / `agent-aimail` **41 次** ⇒ 安装步骤已指向新包名，QA 第 6 条的前提消失；
- **但**：`0.1.56` 这个版本号的产物内容被替换了（版本号未变、二进制已变），破坏"版本 ⇄ 产物"一一对应。
- 建议：发一版 **CLI 0.1.57** 作为干净落点（内容 = 现在 0.1.56 资产），把不可变性还回去。此条属发布决策，等 owner 拍板。

## 12. 待验证方复跑

新版本 `agent-aimail@0.2.2` 发布后，请按 §4 三条重跑 L2/ts（以及 L2/py、L1/py、L1/ts 不回退）。实现方本地已完成 §10 全部场景，L2/ts 的最终判定权在验证方。

## 13. 发布落地（2026-10-23，实现方）

- **npm `agent-aimail@0.2.2` 已发布**，`dist-tags.latest = 0.2.2`，带 SLSA provenance（`dist.attestations.provenance.predicateType = slsa.dev/provenance/v1`，CI OIDC 发布）。
- **0.2.1 / 0.2.0 均已 deprecate**（0.2.1 文案指向 0.2.2+；0.2.0 文案已校正为同样指引）。
- **CLI `cli-v0.1.57` 已发布**（4 平台资产）：`aimail 0.1.57`，资产内 `aimailsdk` 0 次 / `agent-aimail` 41 次。
- **CI run `39880503781`**：attempt 1 有 1 例失败（`l3 / linux-arm64 / dsh-plugin`，卡在 "persona/signature 三标签草案"；同一场景 linux-amd64 已成功 ⇒ LLM 输出方差）；按"真实 L3 真绿"口径 rerun failed job，attempt 2 该 job success，**run 终态 26/26 全绿**。
- **发布前新门禁在 CI 实际生效**：`publish-npm.sh` → `l2-check-tarball.sh` 第 9 步（裸项目 + dsh-agent@latest 在位两次干净安装）通过后才 publish。
- 发包侧公开验收（本机实测，公网 registry）：`npm install agent-aimail` 干净项目 **rc=0**（无任何 flag），装到 0.2.2，`agentTools` / `activateAddressCodePersist` 均为 function。

