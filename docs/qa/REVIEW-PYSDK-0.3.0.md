# 评审意见 — pysdk SSE-V2 重构 / 0.3.0（质检把关）

- 日期：2026-11-24
- 对象：`docs/qa/HANDOFF-PYSDK-0.3.0-V2.md`（开发交接）+ 当前工作树（基线 `9b96630`，本轮改动未提交）
- 结论：**GO（附 2 条件）** —— 两条阻断已在 `4128390` 清掉，Q5 五轮全绿

```
VERDICT: GO（条件见 §2′）
B-1 已收敛：实测"5 个 patch 模块确实不在 wheel"（README 的措辞本来是准的），但
    install.py / sdk_ops.py / hermes/aimail_hermes.py 仍在包内且可导入 —— 交接 G-2 的
    "已移出 wheel" 表述不准确。已改 README 写明事实，并加 test_legacy_surface_pin.py 双向钉住。
B-2 已清：批次已提交为 4128390（作者标注开发、验证方复核），wheel 从该 SHA 重建。
Q5 已完：见 §4，五轮全绿，且 inject.log 证明是"真 ack"而非 0→0 假象。
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

### B-1（已收敛为文档精度问题 + 已加守护）wheel 里 legacy 面的真实边界

交接 §2.2 写"移除 patch 时代 5 个模块" —— 这一条**是真的**；但 §6 G-2 的"实际改为移出 wheel"、
以及"legacy 面整体已不在包内"的读感**不准确**。逐模块实测（干净 venv，`find_spec`）：

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

**已按"改文档为事实 + 加守护"清关**（这属于发布坐标与测试，不动产品 src）：
`pysdk/README.md` 补一句"三个 legacy 模块仍随包发布、可导入、无 entry point 接线、0.3.1 移除"；
新增 `tests/sdk/test_legacy_surface_pin.py` 双向钉住（现在在包里的必须仍在且 README 必须这么说；
已移除的 5 个不得回流；console script 只能是 aimail-agent）。

**如果后续要做选项 1（彻底移除）**，那是产品改动，归开发：

1. 真正把 3 个模块排除出 wheel（同时确认 `aimail/deerflow_plugin`、`hermes_plugin` 不 import 它们），
   并让 `test_wheel_manifest.py` 增加"legacy 模块不得出现在 wheel 里"的断言；
2. 或**改文档为事实**：README/§8.2 写"legacy 模块仍随包发布、但没有任何 entry point 接线（install 命令已退役）；
   0.3.1 物理移除"，并加同样一条断言把当前状态钉住（防止"以为删了"再次发生）。

选项 2 只要改文档 + 一条测试，代价最小；选项 1 更彻底。**我倾向 1**，因为这次 breaking 的核心卖点就是
"安装只剩 pip install"。

### B-2（流程，已清）本轮改动已提交为 `4128390`

提交内容：`pysdk/`（含 v2 与两个插件）、`pyproject.toml`、`tests/`、夹具、计划与交接文档 +
我的 README 修正与守护测试。提交后在该 SHA 上重跑：

```
pytest tests/sdk → 522 passed / 0 failed        （原 518 + 我的 4 条守护）
bash tests/sdk/l0-gate-tests.sh → [L0] PASS — all gates green
wheel（从 4128390 构建）→ agent_aimail-0.3.0-py3-none-any.whl
                        258,435 B  sha256 40e5d1597407605682418139aecd418788a5508e8e97d571732c0bb8f3df7182
```

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

## 4. Q5 三宿主实跑（我自己的运行，已完成，全部 PASS）

| 运行 | 结果 | 关键证据 |
|---|---|---|
| `py hermes s1 s2 s3 s4 neg` | **PASS=14** RED=0 GAP=0 | S3 three rows: whitelisted → inbound stored (emails=1) → queued then acked；S4 走单地址分支（第二地址被拒） |
| `py deerflow s1 s2 s3 s4 neg` | **PASS=15** RED=0 GAP=0 | S3 同上；S4 `max=2` + `aggregate stream reconciled from 2+ bindings` |
| `py deerflow s5` | **PASS=2** | `paused=True`；unbind `1 -> 0` |
| `py hermes s5` | **PASS=2** | 同上（**交接文档没跑这一轮，我补的**） |
| `ts dsh s1 s2 s3 s4 neg` | **PASS=16** RED=0 GAP=0 | pysdk 改动没有连带弄红 TS |

`inject.log`（这是判定"真 ack"的关键，交接 §5.3 修的正是这里）：
```
deerflow : sent_before_boot  acked_after_poll=20  observed max=1 final=0  emails=1
hermes   : sent_before_boot  acked_after_poll=23  observed max=1 final=0  emails=1
```
即 watcher **先见到 pending≥1、随后归零**——排除了"从未入队"被判成通过的可能。

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

---

## 5. 发版计划（我据此执行）

**对象**：pysdk `agent-aimail` 0.3.0
**坐标**：`4128390` · wheel sha256 `40e5d1597407605682418139aecd418788a5508e8e97d571732c0bb8f3df7182`（258,435 B）

| 步骤 | 动作 | 判据 |
|---|---|---|
| 0 | 已完成：提交 + 从 SHA 重建 wheel + `pytest tests/sdk` 522 passed + `[L0] PASS` | 三者同时为真 |
| 1 | `publish.yml` **dry_run=true** | gate / pypi-build（pytest + pyflakes）/ 打包全绿；不发布、不打 tag、不跑 L3 |
| 2 | `publish.yml` **dry_run=false** | npm 侧按 `publish-npm.sh` 幂等跳过 0.5.2；`cli-release` 因 `cli-v0.5.2` 已存在而跳过；**pypi 上传 0.3.0**（OIDC / Trusted Publishing，环境 `pypi-release`，无本地令牌） |
| 3 | 发布后回读（不认 job 绿） | `pip download agent-aimail==0.3.0` → wheel sha256 **必须等于** 上面的值；干净 venv `pip install` 后 `aimail.__version__==0.3.0`；`https://pypi.org/integrity/agent-aimail/0.3.0/<wheel>/provenance` 显示 `workflow=publish.yml` + `environment=pypi-release` |
| 4 | 留痕 | 打 `pysdk-v0.3.0` 标签指向 `4128390`（现有 pypi 流程不建 pysdk 标签）；把 §3 的 VERDICT 与产物 hash 写进 `REVIEW-REPLY` |

**发布说明必须包含的两条（否则说明与产物不符）**
1. "legacy 面：5 个 patch 模块已不在包内；`aimail.install`/`aimail.sdk_ops`/`aimail.hermes.aimail_hermes`
   仍随包发布、可导入，但无 entry point 接线，`aimail install` 不再是命令，0.3.1 移除。"
2. "C8（回复我们的回复留在同一 session）**宿主侧未实机验证**，s6 夹具为 0.3.1 强制项。"

**回滚**
- 可用回滚 = PyPI `yank` 0.3.0 + 用户显式 `pip install agent-aimail==0.2.9`。
- 但 0.2.9 依赖本轮**已移除**的 push/patch 面（webhook 入站 + 宿主 patch），退回需**连宿主一起退**；
  因此 0.3.0 的回滚成本高于寻常补丁版，这一点必须在发布说明里写明。
- 测试侧回滚开关：`AIMAIL_LEGACY=1` 显式执行 legacy 套件（默认 `SKIP_LEGACY`）。
