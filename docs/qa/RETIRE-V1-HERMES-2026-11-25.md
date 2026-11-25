# 退役 v1 hermes 适配层（2026-11-25）

授权：owner 本轮明确"现在可以退役 V1 了"。

## 1. 删了什么

| 对象 | 处理 |
|---|---|
| `pysdk/hermes/aimail_hermes.py` | 删除（v1 源码补丁适配器） |
| `pysdk/hermes/{patch_webhook,patch_profiles,register_profiles,ensure_config,toolsets}.py` | 删除（补丁族；早已不在 wheel，但仍在树里） |
| `pyproject.toml` → `"pysdk/hermes/aimail_hermes.py" = "aimail/hermes/aimail_hermes.py"` | 移除该 force-include |
| `pyproject.toml` 硬依赖 `PyYAML` | 降为空（唯一用 yaml 的就是被删的 v1 适配器） |
| 契约键 `hermes_inbound_path` / `hermes_route_name` | 从清单与两侧镜像删除（`aimail-contract.json` version 2→3） |
| `aimail_contract.HERMES_INBOUND_PATH` / `HERMES_ROUTE_NAME` / `hermes_inbound_url()` | 删除 |
| `contract.ts` 的 `HERMES_INBOUND_PATH` / `HERMES_ROUTE_NAME` | 删除 |

保留（**仍然是 v2 面**）：`pysdk/hermes_plugin/`（`hermes_agent.plugins` 入口点）、`INBOUND_PATH`/
`INBOUND_PORT`（见 §4）。

## 2. 退役后行为（不再 ImportError）

`pysdk/install.py` 的 v1 hermes 三个入口先改成"明确拒绝 + 指路"（不再是 `ModuleNotFoundError`）；**当天晚些时候整个 `install.py` 也删除了**（见 `KEEP-ONLY-SSE-V2-2026-11-25.md`，`aimail install` 不再是命令）：

- `install --type hermes` / `uninstall --type hermes` / `register-profiles` ⇒ 打印"v1 已退役，
  v2 = `pip install agent-aimail` + `hermes_agent.plugins` 入口点"，**rc=2**。
- 删除随之失去引用的 `_import_hermes`、`hermes_webhook_anchor_gate`、`_release_hermes_skills`、
  `_uninstall_hermes_profiles`，并清掉 `install.py` 里因此变成 unused 的 import（json/re/sys/Path/
  resources_skills_dir）。
- `pysdk/hosts/registry.py` 的 hermes 条目改指 v2：`adapter_dir`/`register_module` = `hermes_plugin`、
  `register_fn` = `register`，去掉 `install_fn`；`pysdk/sdk_ops.py` 的挂载目录同步为 `hermes_plugin`。

## 3. 门禁与测试

- `tests/contract/check-legacy-push-scope.py` 重写为"端点作用域 + 退役不回潮"门禁（L0 已接入）：
  `local_endpoint` 两键必须在清单里且可映射；两个 hermes 键不得回到清单、两个镜像不得再有对应常量、
  代码里不得再出现 `/webhooks/aimail-inbound`、`pysdk/hermes/` 不得重现、v2 hermes 面不得注册 webhook。
- `check-contract-single-source.py`：`REQUIRED_KEYS` / `CONST_MAP` 去掉两项（现 6 项常量比对）。
- `check-docs-consistency.py`：`PLATFORM_CONTRACT_KEYS` 只留 `inbound_path`（6/6 契约值）。
- 测试：删除只测 v1 补丁器的 2 个文件（`test_hermes_patch_roundtrip.py`、`test_hermes_toolset_ensure.py`）；
  重写 `test_legacy_surface_pin.py`（`SHIPPED_LEGACY` 只剩 install/sdk_ops，v1 全进 ABSENT 且检查的是
  `hermes/` 下的真实路径——旧版检查的是 `pysdk/*.py`，实际一直没校验到）；`test_wheel_manifest.py`
  改为"树与 wheel 都不得再出现"；`test_hosts_layer.py` / `test_identity_header_contract.py` /
  `test_agent_pull_entry.py` / `test_sdk_cli_boundary.py` 里针对 v1 的 pin 删除或替换为 v2 不变量；
  `tests/sdk/legacy-suites.txt` 去掉两个已删文件。
- 发布与夹具：`publish.yml` 的 pyflakes 列表、`l2-verify-wheel.sh` 的 import/absence 列表、
  `l0-gate-tests.sh` 的 pyflakes 列表同步；`tests/l3/run-l3.sh` 里检查 hermes v1 webhook 路由的
  取证块换成检查 `hermes_agent.plugins` 入口点是否可发现。
- 文档：`pysdk/README.md`、`docs/agent-adapter-playbook.md` / `_zh.md`（P1/P4/P5/速查表）。

## 4. 验证（本轮实际执行）

```
bash tests/sdk/l0-gate-tests.sh                     # PASS — all gates green
python -m pytest tests/sdk -q                       # 516 passed（删掉 24 项 v1 用例，新增 1 项 v2 不变量）
python3 tests/contract/check-legacy-push-scope.py   # clean（188 files scanned）
python -m pip wheel . -w /tmp/whl                   # 构建成功
```

wheel 内实测（解包后）：
- `aimail/hermes*` 只剩 `hermes_plugin/*`，**没有** `aimail/hermes/`；
- `entry_points.txt` = `[hermes_agent.plugins] aimail = aimail.hermes_plugin:register`；
- `Requires-Dist` 只剩 `fastapi`(deerflow extra) / `markitdown`(docs extra)，**无 PyYAML**；
- 逐模块 import 通过；`install_hermes()`/`uninstall_hermes()` 返回 2 并打印 v2 指引；
  `aimail.hermes.aimail_hermes` / `aimail.hermes.patch_webhook` 均为 `ModuleNotFoundError`。

## 5. 未做（需 owner 决策 / 属另一件事）

- ~~`inbound_path` / `inbound_port` 的存废~~ **已决（同日）**：owner 决定连同回环监听一起删除，见
  `docs/qa/UNIFY-DSH-DELIVERY-AND-DROP-ENDPOINTS-2026-11-25.md`。
- **playbook 里 hermes 的逐点实现叙述**只改了 P1/P4/P5 与速查表；P6–P10 正文仍有 v1 行号引用（历史
  说明性质）。要彻底清理需整段重写，且 en/zh 必须同步。
- `pysdk/deer-flow/`（v1 push 路由）未退役——它仍被 wheel 携带、并仍用 `INBOUND_PATH`。
