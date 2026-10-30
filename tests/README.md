# tests/（aimail 仓）—— 本仓脚本的归属

本仓 `tests/` 只放**代码级单测之外的** SDK / CLI 域 / 契约 / L3 脚本；
Rust 与 TS 单测仍内联在各包（`cli/src/**/#[cfg(test)]`、`pysdk`、`tssdk/**/test/`）。

> **跨仓总表在** `aimail-advanced/tests/README.md`（层级 L0–L3 × 场景 1–4 的完整矩阵，
> 含 gateway / advanced / SDK 五宿主那些脚本），机器真源是
> `aimail-advanced/tests/test-matrix.json`。
> 本文件只讲**本仓这一半**，两边由同一个校验器覆盖：
>
> ```bash
> AIMAIL_REPO=/home/ubuntu/aimail python3 /home/ubuntu/aimail-advanced/tests/check-test-matrix.py
> #   → 同时扫描 advanced(tests/*) 与本仓 tests/*；新增脚本不登记即 rc=2
> ```

## 三条铁律（与总表一致）

1. 红先归账：**场景**不符 / **环境**不成立 ⇒ GAP（rc=2），**产品**缺陷才进退回件。
2. 产品用例必须写明"在哪个场景、用哪个场景的工具"；同一脚本不承载两个场景的断言。
3. 场景之间代码路径不同、不能互证：场景 1 走 `activate-address-code`（无 CLI、agent-scope key）；
   场景 2/3/4 走 CLI `install`（系统级凭据、`register`）。

## 本仓脚本矩阵

| 路径 | 层级 | 场景 | 角色 | 说明 |
|---|---|---|---|---|
| `sdk/l0-gate-tests.sh` | L0 | any | runner | SDK 发布前 L0 全量：lint + pytest + tsc + vitest（`--release sdk` 的必跑集） |
| `sdk/test_*.py` | L0 | any | suite | SDK 行为基线：签名协议 / 绑定归属 / pull 决策 / `activate_address_code_persist` 落盘 / 幂等 |
| `sdk/release/l2-*.sh` | L2 | 1,2,3 | suite | 已构建 wheel / tarball 的**安装后**校验（装到干净环境再跑） |
| `contract/check-*.py` | L0 | any | suite | 四道静态闸：契约单一真源 / 文档↔实现一致 / 文件归属 / 零桥符号 |
| `shared/shared-boundary-checks.sh` | L0 | any | suite | 边界四闸的聚合入口（CLI 与 SDK 两侧共用） |
| `cli/run-cli-gates.sh` | L0 | any | runner | CLI 域门禁：共享边界 + rust 电池（fmt/clippy/test）+ 转调 `aimail-advanced/tests/cli/` 的 L1/L2 |
| `cli/test_*.py` | L0 | any | suite | 平台注册表 schema / 资源落位单测 |
| `cli/agent_host_smoke.py`、`cli/stage-resources.sh` | — | — | helper | 一次性冒烟 / 资源暂存，不产生判定行 |
| `l3/run-l3.sh` | L3 | 1,2,3 | runner | 发布产物闭环（l3.yml 12 格的数据源）：装宿主 → 取已发布 gateway/CLI → SDK 安装 → welcome 闭环 |
| `l3/run-s1-local.sh` | L3 | **1** | runner | 场景 1 发布后**本地**复验：advanced 闭源、不进云矩阵；固定硬件/OS 上的典型 agent（hermes/dsh） |
| `agent-env/prepare.sh`、`verify.sh` | — | — | helper | L3 环境准备与**自证**（pins 单一来源；不成立就是 GAP，不是 FAIL） |

层级含义（owner 2026-10-30）：**L0** = 固定环境下的工具 + 产品功能点；**L1** = 固定环境下的完整流程；
**L2** = 上线前多 agent；**L3** = 上线后多硬件多 OS 典型 agent 的发布产物复验。

## 运行

```bash
# 本仓 L0（每次提交）
bash tests/sdk/l0-gate-tests.sh            # → 判定行 [L0] PASS/FAIL
bash tests/cli/run-cli-gates.sh            # → CLI GATE L0: PASS；含 advanced/tests/cli 的 L1/L2
bash tests/shared/shared-boundary-checks.sh

# 本仓 L2（wheel/tarball 安装后）
bash tests/sdk/release/l2-verify-wheel.sh <wheel>

# 本仓 L3（发布后；也可由 aimail-advanced/tests/run-e2e.sh --release sdk 联动）
bash tests/l3/run-l3.sh <platform> <agent>
bash tests/l3/run-s1-local.sh              # 场景 1 本地复验（hermes / dsh）

# 归属自检（改完本仓任何 tests/ 脚本都要跑）
python3 ../aimail-advanced/tests/check-test-matrix.py
```

## 与其它入口的关系

- `aimail-advanced/tests/run-e2e.sh --release sdk` 会调用本仓 `tests/sdk/l0-gate-tests.sh`；
- `--release cli` 走 `aimail-advanced/tests/cli/run-cli-gate.sh`，本仓 `tests/cli/run-cli-gates.sh`
  是它的**上游**（共享边界 + rust 电池在这里）；
- L3 的云矩阵在 `.github/workflows/l3.yml`，数据源是本仓 `tests/l3/run-l3.sh`；
  场景 1 因 advanced 闭源不进云矩阵，只有 `tests/l3/run-s1-local.sh` 这条本地腿。
