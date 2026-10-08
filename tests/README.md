# tests/ —— 域 × 层地图

> 两轴正交：**域**（测谁）= CLI / SDK / 共享 / 契约 → 决定**目录**；
> **层**（发布生命周期哪个卡点）= L0/L1/L2/L3 → 决定**文件名前缀与子目录**。

## 层定义（各层的"目的"）

| 层 | 目的 | 在卡什么 |
|---|---|---|
| **L0 开发闭环** | 构建闭环测试脚手架,提供快速端到端能力,让测试跟得上需求变化 | 代码本身:构建/编译/lint/单测/契约,本地+CI 每次都跑 |
| **L1 集成** | 建立系统化的功能模块测试集,让开发结果与目标持续匹配、不走样不漂移 | 功能面:针对具体 agent 环境的集成行为(宿主黑盒契约) |
| **L2 发版门禁** | 将积累的开发结果正式发布前,脱离开发环境、回归用户环境做更严格全面的测试;重点功能/路径/效果必须过关,不过关不发版 | 用户环境:真实安装链、真实宿主、真实产物 |
| **L3 发布后冒烟** | 完全站用户视角、用户资源环境,获取**已发布产物**做使用验证,防发布过程漂移 | GitHub Actions 用户环境(4 平台 × hermes/dsh):已发布 CLI×SDK×网关对接闭环 |

## 一眼表

| 域 | 目录 | 层 | 脚本/内容 | 在哪跑 |
|---|---|---|---|---|
| **SDK** | `tests/sdk/` | L0 开发闭环 | `l0-gate-tests.sh`(pyflakes+pytest+tsc+vitest+契约 4 检查器) | 本地 + advanced `run-e2e.sh` |
| | `tests/sdk/` | 行为基线(pytest) | 33 个 `test_*.py`(261 用例)+ `conftest.py` | `pytest tests/sdk/`(CI + 本地) |
| | `tests/sdk/release/` | L2 发版门禁 | `l2-check-tarball.sh`(npm)· `l2-verify-wheel.sh`(pysdk) | CI 内嵌(`publish-npm.sh`/`publish-pypi.yml`) |
| **统一 L3** | `tests/l3/` | L3 发布后冒烟 | `run-l3.sh` + `.github/workflows/l3-integration.yml`(CLI×SDK×基础版网关对接闭环) | GitHub Actions(发布后自动 + nightly + 手动) |
| | `tests/sdk/tooling/` | 发布工具(非层) | `check-versions.sh`(tag 前版本单线制)· `release-precheck.sh` · `what-changed.sh` · `bump.sh` | 发版流程 |
| **CLI** | `tests/cli/` | L0 开发闭环 | `run-cli-gates.sh`(共享块 + rust 电池 + delegate advanced L1/L2) | 每次提交 |
| **共享** | `tests/shared/` | 边界检查块 | `shared-boundary-checks.sh`(4 契约检查器的运行壳) | 被 CLI L0 / SDK L0 各调 |
| **契约** | `tests/contract/` | 检查器+基线 | `check-*.py` + `*-baseline.json`(防漂移棘轮,独立于四层) | 被 shared 块 / SDK L0 调 |

## 跨仓指针（L1/L2 的"用户环境"部分在 advanced 仓）

门禁跟着**被测物**走:SDK 发布机制在 aimail(CI 直调),CLI 宿主集成在 advanced(宿主环境在那)。

- **CLI L1 集成** = `aimail-advanced/tests/cli/l1-contract.sh`(宿主黑盒契约面,被测物 = rust 二进制)
- **CLI L2 发版门禁** = `aimail-advanced/tests/cli/l2-docker.sh`(用户环境,发布前;旅程段 2026-09-27 并入)
- **统一 L3(2026-10-08 起)** = 本仓 `tests/l3/` + `l3-integration.yml`:CLI L3 与 SDK L3 合并——
  以终为始,用**已发布**的 CLI(bootstrap 在线取)+ SDK(PyPI/npm registry)+ 基础版网关(Release 二进制),
  在 GitHub Actions 的 4 平台 × hermes/dsh 用户环境验证对接闭环(welcome→安全员 approve→身份名片+签名生效)。
  替代 2026-09-27"CLI L3 并入 L2"的过渡裁决与旧 `l3-verify-published.sh`(已退役)。
- **SDK L1 集成** = `aimail-advanced/tests/SDK/sdk-e2e/` + `session-e2e/`(针对具体 agent 环境的功能集成)
- **SDK L2 用户环境** = `aimail-advanced/tests/SDK/docker-regression/hosts/`(纯净环境安装链:install→注册→绑定→幂等)
- 发布门禁(`tests/sdk/release/`)为何留 aimail 不跨仓:CI 直调(`publish-npm.sh` / `publish-pypi.yml`),移走即断 npm/PyPI 发布链。

## 单测位置（不在 tests/ 下）

- **CLI 单测** = `cli/tests/*.rs`(rust,随 `cargo test`;含 `gateway_standalone_parity` 对活文件 `pysdk/gateway_api.py` 的跨语言验收)。
- **SDK TS 单测** = `tssdk/packages/*/test/*.test.ts`(vitest)。

## 跑法（从仓库根）

```bash
# SDK 域 L0 开发闭环(含行为基线 pytest)
bash tests/sdk/l0-gate-tests.sh

# CLI 域 L0 开发闭环(共享块 + rust 电池 + delegate advanced L1/L2)
bash tests/cli/run-cli-gates.sh

# 共享边界块(单跑,4 个契约检查器)
bash tests/shared/shared-boundary-checks.sh

# SDK 行为基线(CI 同命令)
python3 -m pytest tests/sdk/ -q
```
