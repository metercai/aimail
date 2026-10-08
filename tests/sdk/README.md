# SDK 域门禁（域=SDK，层=L0/L1/L2/L3 + 发布工具）

> 域×层地图与跨仓指针见上级 [../README.md](../README.md)。本目录放 **SDK 域**的门禁脚本、
> 行为基线 pytest 与发布工具。SDK 分两路：pysdk（python）与 tssdk（nodejs），L0 两路都扫。

## 分层（按发布生命周期卡点）

| 层 | 脚本 | 时机 | 拦什么 |
|---|---|---|---|
| **L0 开发闭环** | `l0-gate-tests.sh` | 每次提交(本地 + advanced run-e2e) | 代码本身:pyflakes/pytest(行为基线)/tsc/vitest + 契约 4 检查器(单一真源/zero-bridge/文件归属/docs↔impl)——快速全扫,跟上开发调整 |
| **L1 集成** | 本目录无;= advanced `tests/SDK/sdk-e2e/`+`session-e2e/` | 针对具体 agent 环境 | 功能模块集成:注册链×真实网关、绑定落盘、收信链、宿主行为不漂移 |
| **L2 发版门禁(用户环境)** | `release/l2-check-tarball.sh`(npm)· `release/l2-verify-wheel.sh`(pysdk)· advanced `tests/SDK/docker-regression/` | 正式发布前,脱离开发环境 | 面向所有 SDK 的核心契约检测:干净 venv 安装冒烟、tarball 结构、纯净环境安装链(装 rc→reset 双路径→register_all spawn→幂等)——重点路径不过关不发版 |
| **L3 发布后冒烟** | 本目录无;= **统一 L3** 上级 `tests/l3/` + `.github/workflows/l3-integration.yml` | 发布完成后(GitHub Actions 自动) | 站用户视角取**已发布产物**(Release/registry 真身):CLI×SDK×基础版网关在真 OS+真 agent 环境完成对接闭环(见 ../README.md) |

> tag 前的版本合法性(tag==PyPI==TS、rc 两态、依赖序)是**发布工具**不是 L1 ——
> 见下方 `tooling/check-versions.sh`(它校验元数据、不跑代码)。

事故映射(2026-09-05 openclaw/pi E415 事故):npm pack 静默失败 → L0
脚本规范(禁吞 stderr)+ L2 空包检查;E415 hardlink(npm bundle 传递
typebox)→ L2 检查 1(与 npm 版本行为无关);CI Test 过但 publish 路径
未覆盖 → L2 内嵌 publish step、publish-pypi.yml 前置 test job。

## 行为基线 pytest（本目录 33 个 `test_*.py`，261 用例）

SDK 侧行为快照:注册链、绑定文件归属、入站路由、search_mail、hermes patch
字节回环、v1 签名协议、check JSON 语义。跑法 = `python3 -m pytest tests/sdk/ -q`
(CI 同命令)。conftest 把 `pysdk/`+`pysdk/hermes/` 塞进 sys.path,直接 import
仓库源码、无需安装。

## 用法(从仓库根)

```bash
tests/sdk/l0-gate-tests.sh                     # L0 开发闭环: python lint+pytest + 契约/文档门禁 + tssdk tsc+vitest
tests/sdk/release/l2-check-tarball.sh <tgz> <ver>   # L2(npm): 单包 tarball 检查
tests/sdk/release/l2-verify-wheel.sh           # L2(pysdk): 干净 venv 3-layout 冒烟
# L3 = 统一 L3,见 ../l3/run-l3.sh + .github/workflows/l3-integration.yml(发布后自动)
```

## 发布工具（tooling/，非门禁层）

```bash
tests/sdk/tooling/check-versions.sh            # tag 前版本单线制(tag==PyPI==TS,rc 两态,依赖序)
tests/sdk/tooling/release-precheck.sh          # R4: 核心包(mail-core|mail)变动 ⇒ 五包齐升
tests/sdk/tooling/what-changed.sh              # 各 SDK 包自上次 tag 是否有内容变化
tests/sdk/tooling/bump.sh <pkg|pysdk> <ver>    # 按包 bump 版本(逐包调用)
```

依赖:python3 + pyflakes + pytest 9.x;pnpm(9.15)+ node ≥ 22.5(tssdk vitest)。
npm view 需网络(registry.npmjs.org)。

## L2 用户环境部分（advanced 仓，发布前手动）

CI 单测覆盖不到的 SDK 用户环境行为,在 advanced 仓跑:
- `tests/SDK/docker-regression/hosts/` —— 纯净环境安装链(install→注册→绑定→幂等,dsh/pi 已 4/4+4/4)。
- stable tag 前 rc 包先行:真实宿主装 rc 包 → 注册链×真实网关 → 绑定幂等 →
  双路 E2E(ping 三阶段 + welcome Re: 回复)→ 收信链(投递→验签→处理)。
  全绿才发 stable;任一失败修代码回 L0。

## CI 接入点(2026-10-08 实测核对)

- `.github/workflows/publish.yml`(npm):publish step 逐包调 `scripts/publish-npm.sh`,
  其内部(pack+normalize 后)调 `tests/sdk/release/l2-check-tarball.sh`
  (`tssdk/scripts/publish-npm.sh:112`)。
- `.github/workflows/publish-pypi.yml`(pysdk):test job 跑
  `python3 -m pytest tests/sdk/ -q`;build job 调 `tests/sdk/release/l2-verify-wheel.sh`
  (干净 venv 冒烟)。
- `l0-gate-tests.sh`(L0)由 **本地** 与 **advanced `tests/run-e2e.sh`**(SDK_GATE)调用,
  aimail 的 CI workflow 不直调。
- 统一 L3(`.github/workflows/l3-integration.yml`):CLI/PyPI/npm 任一发布 workflow
  完成即自动触发,另加 nightly + 手动;8 job(4 平台 × hermes/dsh)。
