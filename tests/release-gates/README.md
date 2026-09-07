# Release Gates (专项保存: SDK 发布质量门禁)

发布前/发布后必须执行的检查集。本地与 CI 用同一套脚本(publish.yml /
publish-pypi.yml 内嵌调用),覆盖三层:

| 层 | 脚本 | 时机 | 拦什么 |
|---|---|---|---|
| L0 提交级 | gate-tests.sh | 每次发布前(本地+CI test job) | lint/单测/编译失败、**注册链单元回归**(tests/test_registration_chain.py)、**平台字面泄漏**(platform-boundary gate) |
| L1 版本一致性 | check-versions.sh | tag 前 | 双源漂移、tag≠版本、npm 依赖顺序(被依赖版本未发布) |
| L2 产物门禁 | check-tarball.sh | pack 后、publish 前(CI 内) | E415 hardlink、symlink、workspace: 残留、版本错、main/types 悬空、空包 |
| **L2.5 宿主行为回归** | 手动清单(见下) | **stable tag 前**(rc 包或本地包装到真实宿主) | CI 单测覆盖不到的 SDK 行为:注册链×真实网关、绑定落盘、收信链(ping/welcome 双路 E2E) |
| L3 发布后冒烟 | verify-published.sh | 发布完成后 | registry 版本与本地不符、registry tarball 含 link/workspace:、安装冒烟失败 |

事故映射(2026-09-05 openclaw/pi E415 事故):npm pack 静默失败 → L0
脚本规范(禁吞 stderr)+ L2 空包检查;E415 hardlink(npm bundle 传递
typebox)→ L2 检查 1(与 npm 版本行为无关);CI Test 过但 publish 路径
未覆盖 → L2 内嵌 publish step、publish-pypi.yml 前置 test job。

## 用法(从仓库根)

```bash
tests/release-gates/gate-tests.sh                 # L0: python lint+pytest + tssdk tsc+vitest
tests/release-gates/check-versions.sh             # L1: 版本一致性(需 git tag 参数?自动取)
tests/release-gates/check-tarball.sh <tgz> <ver>  # L2: 单包 tarball 6 项检查
tests/release-gates/verify-published.sh           # L3: registry 冒烟(取本地最新 tag)
```

依赖:python3 + pyflakes + pytest;pytest 9.x;pnpm(9.15)+ node ≥ 22.5
(tssdk vitest)。npm view 需网络(registry.npmjs.org)。

## L2.5 宿主行为回归(发布前手动清单,stable tag 前必须全绿)

CI 单测覆盖不到 SDK 行为(真网关+真宿主),发布前逐项执行:

1. **L0/L1/L2 全绿**(本文件上表)。
2. **rc 包先行**(开发语义):TS/PyPI 发 `X.Y.Z-rc.N`(dist-tag rc),
   真实宿主装 rc 包(dsh profile / pi 扩展 / openclaw 插件)。
3. **注册链×真实网关**:`aimail install/reset` 复用真实系统(或新激活
   e2e 系统)→ 注册成功(agentmail.json 落盘含 api_key/webhook_url)→
   `aimail check` 全绿。
4. **绑定幂等**:重复 install/reset(同一系统)→ exists 幂等,不重复建
   地址、不刷新他人 webhook。
5. **双路 E2E(铁律,两路都测)**:`aimail ping` 三阶段闭环 + `aimail
   welcome` 管理员收到 Re: 回复(带头 `X-AIMail-Agent: {platform}/{ver}`)
   ——每平台各一路,两路都绿才算过。
6. **收信链**:投递→验签→处理(pending 被 ack、agent 收到原始 body)。
7. 全绿 → stable tag 发布;任一失败 → 修代码回 L0(L2.5 的目的:带
   问题的 SDK 不发 stable)。

## CI 接入点

- .github/workflows/publish.yml:publish step 循环内(pack+normalize 后)
  调 `../tests/release-gates/check-tarball.sh`
- .github/workflows/publish-pypi.yml:test job 调 gate-tests.sh
