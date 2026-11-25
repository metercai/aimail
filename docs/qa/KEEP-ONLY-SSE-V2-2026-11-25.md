# 只保留 SSE-V2：push 时代与 CLI 残留清除（2026-11-25，owner 指示）

owner 指令："把所有 push 时代与 CLI 相关的残留都清除掉，只保留 SSE-V2 所需的代码。"

判断标准：**SSE-V2 运行时需要的留下**（拉取投递 + 激活码绑定 + 工具 + 会话投递）；**靠 CLI 驱动、
靠 push/webhook 交付、靠"启动即注册"产生状态的，全部删除**。

## 1. TypeScript 侧（`tssdk/packages/agent-aimail`）

| 删除 | 为什么 |
|---|---|
| `src/openclaw/commands.ts` | legacy `openclaw aimail register/deregister/status` CLI 命令面；它能在没有激活码的情况下按"已有 system + 派生地址"写绑定，与"都必须激活码"冲突 |
| `src/core/sdk-ops.ts`（`assemble/update/teardown/prompt-test` + one-line JSON ABI） | CLI door ops：CLI 调用 SDK 的"运维 ABI"，SSE-V2 宿主不需要（宿主自己发现插件） |
| `src/core/ensure-system.ts` | CLI 反向调用 ABI（`aimail install --system-only`）：system 由激活码产生 |
| `src/core/auto-bind.ts` → `src/core/system-config.ts` | 整条 v1 注册链（`registerAddress`/`saveBinding`/`autoBind`/webhook 三态/secret 修复）删除；只留 `listSystemDirs`/`readSystemConfig`/`SystemGatewayConfig` |
| `src/core/ensure-binding.ts` | 唯一用途是 auto-bind（上一轮已删） |
| `src/core/inbound-notify.ts` | `--inbound-live/--inbound-down` CLI 通知通道 |
| `src/core/listen.ts`、pi/dsh/openclaw 的监听与路由 | 回环端点（上一轮已删） |
| `AIMAIL_TRANSPORT` / `AIMAIL_LEGACY_CLI` 开关 | push 时代的两个开关；现在连 `push` 分支都不存在 |
| `provision.ts` 里的 `activateSystem`/`createApiKey`/`registerPullAddress`/`writeSystemGatewayConfig`/`writeAgentmailPointer`/`removeRawSystemKey`/`writeRawSystemKey`/`defaultWorkerEmail` | 无人调用的 CLI 组装残留（system 只由 `activateSystem` 的 HTTP 版本… 不：由激活码产生） |
| `pi` 的 `inboundPort`/`transport` 选项 | 保留为 `@deprecated` 的接受位（旧配置不崩），不再有任何效果 |

保留的 TS 面：`core/`（gateway/config/meta/poll-entry/address-code/provision(激活+读取)/preprocess/
system-pull/runtime routing…）、`hosts/`（`activate/detect/registry`：宿主自检，非 CLI）、
`mail/`、`pi/`、`dsh/`、`openclaw/`、`desktop/`、`runtime/`。

## 2. Python 侧（`pysdk`）

| 删除 | 为什么 |
|---|---|
| `pysdk/install.py` | v1 CLI 安装器（`aimail install --type hermes|deerflow`），全套 source-patch 时代逻辑 |
| `pysdk/sdk_ops.py` | CLI door ops 的 Python 侧（与 TS `sdk-ops.ts` 对称） |
| `pysdk/deer-flow/`（`aimail_inbound.py`/`aimail_deerflow.py`/`manage.py`/`install-*.sh`） | v1 deer-flow 适配层：在宿主 gateway 进程内注册 push 路由 + patch manage.py；v2 = `deerflow_plugin` |
| `pysdk/hosts/`（registry/detect/activate） | 供 CLI 判断"这是哪个宿主"的注册表；插件模式下由宿主自己决定 |
| `pyproject.toml` 对应的 force-include 行、`fastapi` extra、`PyYAML` 硬依赖 | 上述模块是它们唯一的消费者 |
| `_aimail_bootstrap.py` 的 adapter-dir 白名单 | 扁平适配目录已不存在 |

保留的 Python 面：`aimail_base.py`/`aimail_tools.py`/`aimail_board.py`/`aimail_mcp_server.py`/
`aimail_agent.py`/`aimail_contract.py`/`_resources_release.py`/`_aimail_bootstrap.py`/`v2/`/
`hermes_plugin/`/`deerflow_plugin/`。

## 3. 测试与流水线

删除（v1/CLI 主题，逐项都对得上上表的删除）：`test/core/{sdk-ops,ensure-system,ensure-binding,
auto-bind,listen}.test.ts`、`test/openclaw/openclaw-aimail.test.ts`、`test/pi/inbound-port-source.test.ts`、
`tssdk/test/{startup-hook,inbound-notify}.test.ts`；Python：`test_deerflow_{inbound_route,mcp_env,
pull_wiring,register_name}.py`、`test_hosts_layer.py`、`test_bridge_route_side.py`、
`test_register_webhook_url.py`、`test_registration_secret_{never_nulled,single_source}.py`、
`test_snapshot_default_on.py`、`test_sdk_ops_entry.py`、`test_sdk_cli_boundary.py`、
`test_hermes_{patch_roundtrip,toolset_ensure}.py`。

重写：`test_legacy_surface_pin.py`（现在断言 v1 面**彻底不存在** + v2 运行时存在）、
`test_wheel_manifest.py`（同上，对 wheel 生效）、`tssdk/test/pull-startup-hook.test.ts`
（"无端点 + 单一路由"的形状断言）。

流水线同步：`publish.yml` 的 pyflakes 列表改成 v2 运行时、去掉 PyYAML；
`tests/sdk/l0-gate-tests.sh` 同；`l2-verify-wheel.sh` 的 import 表换成 v2 模块，
并把 `aimail.install`/`aimail.sdk_ops`/`aimail.hosts.*`/`aimail.deer-flow.*` 放进**断言缺失**列表。

## 4. 守卫（L0）

`tests/contract/check-legacy-push-scope.py` 现在同时判：

1. 清单不含任何端点键；2. 两个镜像不含端点常量；3. 源码不含监听符号 / 端点字面值；
4. **整条 v1 路径不得复现**：`pysdk/{hermes,deer-flow,hosts}`、`pysdk/install.py`、`pysdk/sdk_ops.py`。

## 5. 验证（本轮实际执行）

```
bash tests/sdk/l0-gate-tests.sh        # PASS — all gates green
cd tssdk && npx vitest run              # 498 passed / 56 files
python -m pytest tests/sdk -q           # 410 passed
npx tsc --noEmit -p tsconfig.json       # clean
python -m pip wheel . -w /tmp/whl3      # 构建成功
```

wheel 解包实测：只剩 v2 模块（`aimail/aimail_*.py`、`aimail/v2/*`、`aimail/hermes_plugin/*`、
`aimail/deerflow_plugin/*`）；`aimail.install`/`aimail.sdk_ops`/`aimail.hosts.registry`/
`aimail.deer-flow.manage`/`aimail.hermes.aimail_hermes` 均 `ModuleNotFoundError`；
`Requires-Dist` 只剩 docs extra 可选依赖。

## 6. 仍然保留的两个"非 SSE 通道"，需 owner 决议

1. `aimail_mcp_server.py`（stdio JSON-RPC MCP 服务器）：`deerflow_plugin/tools.py` 通过
   `aimail_agent` 读它的 `TOOLS/HANDLERS` 作为**工具定义单一来源**，不是投递通道。建议保留。
2. `aimail-agent` console script + `aimail_agent.py`：v2 插件的工具集/技能/pull 装配入口
   （`hermes_plugin/tools.py`、`deerflow_plugin/tools.py` 都 import 它）。它是运行时代码，不是 CLI
   安装器，建议保留（若要改名/移除需同步两个插件与 pyproject 的 `[project.scripts]`）。

## 7. 未做

- **L2 未重跑**：这轮只动了 Python/TS 的组装面与测试，dsh/pi/openclaw 的 L2 journey 需要重跑确认
  （尤其 dsh 的 s6/s7/s8，现在走的是统一后的出货链路）。
- `tests/l3/run-l3.sh`、`tests/dsh-acceptance/*`、`tests/qa-2026-11-15/*` 里仍有历史叙述提到
  `aimail install` / `sdk_ops`（文档/夹具注释，不作为断言）。

## 8. 补记（同一轮，清最后几条"CLI 反向调用"与误导性提示）

- `pysdk/aimail_base.py::ensure_system()` 删除：它是"SDK 反向调用 CLI `aimail install --system-only`"
  的 ABI（TS 侧 `ensure-system.ts` 已删），无人调用。
- `pysdk/gateway_api.py` 的 `activate_system` 文档改成"遗留，SSE-V2 运行时不调用"。
- 面向用户的提示不再指向已不存在的命令：
  - `pi/identity.ts`：未配置时提示"向网关运营要激活码，然后 `aimail_address` action=bind"，并明确
    "没有安装器、没有自动绑定"；
  - `aimail_base.py` 两处 warning：改为"该 profile 还没有绑定地址（需消费激活码）"。

复跑：`l0-gate-tests.sh` PASS · `pytest tests/sdk` 410 passed · `npx vitest run` 501 passed ·
`pip wheel` 构建成功。
