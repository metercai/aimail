# 评审意见 — pysdk SSE-V2 重构 / 0.3.0（质检把关）

- 日期：2026-11-24
- 对象：`docs/qa/HANDOFF-PYSDK-0.3.0-V2.md`（开发交接）+ 当前工作树（基线 `9b96630`，本轮改动未提交）
- 结论：**HOLD**（1 条产品侧阻断 B-1 + 1 条流程阻断 B-2），Q5 三宿主实跑进行中

```
VERDICT: HOLD
BLOCKER B-1  wheel 里仍有可导入的 legacy 模块，交接文档声称"已移出 wheel" —— 事实不符
BLOCKER B-2  本轮改动全部未提交（无 SHA），按纪律不可签
PENDING Q5   hermes / deerflow / dsh 实跑（我自己的运行，不引用交接里的旧目录）
```

---

## 1. 已完成并可签的复验项

### Q1 安装面（PASS，干净 venv，测安装后的产物而不是源码树）

```
构建: PIP_NO_BUILD_ISOLATION=1 pip wheel .  →  agent_aimail-0.3.0-py3-none-any.whl  258,330 B
      sha256 78040d4c2133053759a50fd06a1f580dedff84a2ab7bfaf916a9fb4dde9f39d0
干净 venv 安装后:
  aimail.__version__          : 0.3.0
  importlib.metadata version  : 0.3.0
  v2 子模块                    : outbound / thread / binding / pull_client / system_pull / agent_system_id 全部可导入
  aimail.hermes.patch_webhook : ModuleNotFoundError（符合 breaking 声明）
  entry points                : hermes_agent.plugins → aimail；deerflow.extensions → aimail；console_scripts → aimail-agent
  wheel 文件数                 : 55，v2 六个模块齐全
```

### Q4 打包守护真的会咬（PASS）

删掉 `pyproject.toml` 里 `pysdk/v2/outbound.py` 那一行 force-include 后：
`test_wheel_manifest.py` → **1 failed, 7 passed**；恢复后 → **8 passed**。
即该测试不是空断言，漏打包会被它挡住。

### Q8 凭据卫生（PASS，抽查）

最近三个证据目录的 `host.log / turn_*.log`：
```
/tmp/aimail-l2-JpzK8p/ → no raw key/code found
/tmp/aimail-l2-Oh9v2o/ → no raw key/code found
/tmp/aimail-l2-F1Vdpz/ → no raw key/code found
```
（抽查口径：`sk-…`、`addr-xxxx-xxxx`、`api_key=…`；结论只覆盖这三次运行。）

### Q3 门禁用例自身改动（PASS，附 2 条要求）

| 改动 | 我的判定 |
|---|---|
| `cases/s4_multiaddr.sh` | **不是放宽**：断言仍是 `max_bindings ≤ 1`，只是把"哪个宿主走单地址分支"从硬编码 `host=pi` 改为读 `HOST_ADDRESS_MODEL`。原写法会把 hermes 误判成多地址宿主。 |
| `lib/gw.sh` | **不是放宽**：`--pid-file` + 只清理本 workdir，端口占用仍 fail-closed（`GAP: port … already in use`，实测该分支仍在）。这是修"并行跑互杀导致假绿/假红"。 |
| `run-matrix.sh --sdk py\|ts` | **不是放宽**：新增选择器，ts 默认路径未变（本轮 ts 回归另测）。 |
| `cases/s5_lifecycle.sh` | **归因方向变了，不是放宽而是变严**：pysdk 宿主没有 frame 证据，`HOST_FRAME_EVIDENCE=0` 时不再把"0 工具调用"判成 GAP，而落到 `paused/final` 的 RED。**要求 F-1**：pysdk 宿主需由插件/适配器暴露一个工具调用计数，否则模型不动手会被判成产品缺陷（假红）。本轮 deerflow s5 `paused=True`、`1 -> 0`，未触发该风险。 |

### Q2 语义对齐（读码，PASS）

`pysdk/aimail_tools.py::_deliver_batches` 的三种结果与 TS `deliverBatches` 一致：
`accepted/None ⇒ ack`；`{"status":"skip"}` ⇒ 不 ack、不算错、从 dedup 集合移除（下次可再投）；
`raise/rejected ⇒ 不 ack、计 error、下轮重拉`。行为由 `test_v2_pull_client_and_reconcile.py` 覆盖。

---

## 2. 阻断项

### B-1（产品侧，必须清）wheel 仍包含且可导入 legacy 模块，与交接文档的声明相反

交接文档 §2.2 写"**移除** patch 时代 5 个模块"，§6 G-2 写"实际改为**移出 wheel** + 显式开关可达"，
§8.2 breaking 第 2 条写"patch 时代模块**不再随包发布**"。实测**三条都不成立**：

```
wheel 内容（agent_aimail-0.3.0-py3-none-any.whl）:
  aimail/install.py
  aimail/sdk_ops.py
  aimail/hermes/aimail_hermes.py
在干净 venv 里 import 的结果:
  aimail.install                importable
  aimail.sdk_ops                importable
  aimail.hermes.aimail_hermes   importable
wheel 内字符串统计:
  'def replay'        1
  'register_profiles' 5
  'sdk_ops'           10
  'aimail install'    16
```
影响：这是 **breaking 版本**，发布说明会告诉用户"CLI 安装已退役、patch 模块不再发布"，而实际产物里
这些代码仍在且可导入。用户按说明做安全评估会得出错误结论。

**最小清关（二选一）**
1. 真正把 3 个模块排除出 wheel（同时确认 `aimail/deerflow_plugin`、`hermes_plugin` 不 import 它们），
   并让 `test_wheel_manifest.py` 增加"legacy 模块不得出现在 wheel 里"的断言；
2. 或**改文档为事实**：README/§8.2 写"legacy 模块仍随包发布、但没有任何 entry point 接线（install 命令已退役）；
   0.3.1 物理移除"，并加同样一条断言把当前状态钉住（防止"以为删了"再次发生）。

选项 2 只要改文档 + 一条测试，代价最小；选项 1 更彻底。**我倾向 1**，因为这次 breaking 的核心卖点就是
"安装只剩 pip install"。

### B-2（流程）本轮改动未提交，无 SHA

与上一批同理：我可以对工作树做预验证（已完成 Q1–Q4/Q8），但**不能对工作树签字**，也不能从
工作树直接发 PyPI。清关动作：提交（含 `pysdk/`、`pyproject.toml`、`tests/`、夹具、文档）→ 给出 SHA
→ 我从该 SHA 重新构建 wheel 并核对 hash 与测试。

---

## 3. 需要我裁定的三个问题（回答交接 §8.5）

### Q6 / G-1（C8「回复我们的回复」缺宿主侧实机证据）→ **不阻断 0.3.0，但必须写进发布说明**

- 现状：C8 的一半只有单测 + runtime 级用例（同一 `chat_id`），没有 s6 夹具。
- 裁定：**不阻断**。理由：C8 是 P4 的**改进项**，不是 0.3.0 的既有行为回退；0.2.9→0.3.0 的 breaking 面
  与 C8 无关。但**发布说明必须写明"C8 宿主侧未验证，s6 属 0.3.1 强制项"**，不得声称闭环。
- 这条在我的结论里始终列在 `UNVERIFIED`，不写成 PASS。

### Q7 / G-2（legacy 未物理删除）→ **不满足，见 B-1**

交接说"移出 wheel"，实测仍在 wheel 且可导入 ⇒ 不能按"可接受"结案。按 B-1 二选一清关。

### Q8 / G-3（PyPI 发布通道）→ 需要 Owner 决定，我给出可执行方案

- 现状：`scripts/` 只有 `publish-npm.sh`（tssdk），pysdk 无发布脚本；本轮只到 wheel。
- 建议：把 PyPI 上传做成 CI job（与 tssdk 的 `publish.yml` 同形），输入为 wheel 产物 + tag，
  凭据用仓库 secret（`PYPI_API_TOKEN`），**不在本机持有令牌**；发布后必须回读
  `pip download agent-aimail==0.3.0` 并核对 sha256 与本地 wheel 一致，才算发布成立。
- 这需要你（Owner）授权：① 用仓库 secret 的方式；② 是否允许我直接在 CI 派发（不本地持钥）。

### G-4（`aimail-agent` console script 仍在 wheel）→ 可接受

它是 agent 运行时入口（scenario 1），不是安装 CLI；本轮 breaking 声明针对的是 `aimail install`。
**要求**：发布说明里把"只有一个运行时入口 `aimail-agent`，安装 CLI 已退役"写清，避免被读成"SDK 自带 CLI"。

### G-5（C11 敏感信息无机器断言）→ 本轮抽查通过，但我要补断言

抽查三次运行无 key/code 原文；但**没有可执行断言**。我会在夹具加一条
`grep -iE "raw_key|addr-[0-9a-f]{4}-" host.log turn_*.log` 的失败即 RED，作为 0.3.1 前的门禁项
（这是验证方工装，我自己做，不占开发时间）。

---

## 4. Q5 三宿主实跑（进行中，不引用交接里的旧目录）

我自己跑，五轮，端口各自隔离：

```
py hermes   "s1 s2 s3 s4 neg"  18210/12710
py deerflow "s1 s2 s3 s4 neg"  18211/12711
py deerflow "s5"               18212/12712
py hermes   "s5"               18213/12713   ← 交接未跑，我补上
ts dsh      "s1 s2 s3 s4 neg"  18214/12714   ← 回归
```
结果回执后补进本文 §4（数字 + `inject.log` 里 `emails=` / `acked_after_poll=` 是否同时出现）。
在拿到这五轮之前，§0 的 HOLD 不变——即使 B-1 清掉，没有我自己的 L2 证据也不签字。
