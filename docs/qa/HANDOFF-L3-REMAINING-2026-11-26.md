# L3 交接（2026-11-26）：五宿主本地回归，剩余三项与精确修法

## 1. 已交付且证据在手

| 项 | 坐标 / 证据 |
|---|---|
| gateway v1.0.17 | tag `v1.0.17` @ `ac30461`；四平台资产 sha256 已下载自算；`tests/agent-env/pins.env` 已更新 |
| advanced v1.0.16 | tag @ `c0288b8`，内嵌 gateway `ac30461` |
| npm `agent-aimail@0.6.0` | tarball sha512 = registry `dist.integrity`（逐字节核对） |
| PyPI `agent-aimail@0.4.0` | wheel sha256 与 PyPI JSON 声明一致；METADATA 0.4.0 |
| CLI / bridge 退场 | 删除 118 文件 / 34,263 行；`ci.yml` 的 cli-gates、platform-check 与 publish.yml 的 meta/cli-build/cli-release 显式退役；L0 全绿 |
| L2 | 四宿主全绿；dsh 连续两次 31/0/0 |
| L3 本地 | **dsh ✅ PASS**、**pi ✅ PASS**（干净 HOME + 预装 LLM + 宿主自主安装已发布版） |

## 2. L3 剩余三项（按 S1 = "激活邮件 → 宿主自主安装 → 绑定" 判）

统一入口（今天新增）：`run-matrix.sh --sdk none` —— 沿用 L2 容器基座，只跳过 SDK 预装。
对照实验已证明这条路可行：`--sdk py` 时 hermes S1 PASS，`--sdk none` 时 S1 FAIL，差别只在安装步骤。

### 2.1 hermes（最接近闭环）

**现状**：`--sdk none` 下容器能起，但宿主报告 `no such executable: aimail_address`、`full-text search /opt/hermes for "aimail": 0 matches` —— 符合预期（还没装）。
**已修**：`hosts/hermes.sh` 在 `SDK_KIND=none` 时不再预写 `plugins.enabled: [aimail]`（启用不存在的插件会让网关拒绝启动）。
**待做**：S1 的语义是"agent 收到邮件后自己 `pip install`"，但当前 S1 的等待窗口内 agent 没有完成安装。需要：
1. 在 `--sdk none` 模式下把 S1 的等待时间放宽（agent 要跑 pip + 绑定，实测 hermes 单轮 200–300s）；
2. 或在 S1 前给宿主一句明确的系统提示（不改邮件正文，属夹具）。
**验收**：`hermes S1 PASS`，且 `hermes plugins list` 显示 `aimail | enabled | 0.4.0 | entrypoint`（此形态今天已单独验证通过）。

### 2.2 openclaw

**现状**：宿主直跑已证不可行（state lock / `plugins install` 参数歧义）。`GATE.md §9` 已记录 L2 的三条机制。
**待做**：`--sdk none` 分支已加（跳过 `openclaw plugins install`），需在容器内验证 agent 自行执行
`openclaw plugins install agent-aimail@latest --accept-capabilities` + `enable`；
绑定必须走 `dispatch_action{id:"aimail_address"}`（openclaw 无直调）。
**注意**：曾观测到装成 0.5.1（非 registry 最新 0.6.0），需查是否命中镜像内预置版本或 npm 缓存。

### 2.3 deerflow

**现状**：`--sdk none` 分支已加（跳过 `/opt/sdk/*.whl` 安装）。宿主是 FastAPI（`/app`），turn 走其 HTTP API。
**待做**：扩展记录 `use: aimail.deerflow_plugin:install` 必须在 L3 下仍然写入（否则宿主装完也加载不到）；
`plugin_import` 断言在 L3 下应改为"未安装时允许为空"。
**验收**：agent 自主 `pip install agent-aimail==0.4.0` → 宿主重启后加载扩展 → 绑定成功。

## 3. 今天修掉的夹具缺陷（全部已提交，供开发避免重复踩）

1. L3 prompt 传了整封原始 MIME 邮件 → 改为解码 text/plain、还原 quoted-printable、只取 `Use \`aimail_address\`` 起始段（与标准文案逐字一致）；
2. pysdk 安装位置只搜 `$HERMES_HOME` → 改为 `importlib.metadata` + `~/.local` 回退；
3. pysdk 版本判据误用 npm 最新 → 改为 PyPI 最新；
4. `pip install` 污染系统 python → `PIP_USER=1 PYTHONUSERBASE=$HOME/.local`；
5. 安装 nudge 与绑定耦合（agent 反复重试绑定）→ 拆成两个回合；
6. `prepare.sh --home` 对 hermes 是父目录、对 openclaw 是 `.openclaw` 本身（语义不一致，已分别处理）；
7. 宿主版本解析未剥前缀（`OpenClaw 2026.11.5 (sha)`）→ 取 semver；
8. uv dry-run 无法解析时误判红 → 降级 SKIP；
9. 遗留容器造成幽灵会话（s6 会话数 8→9→12）→ journey 前按镜像清理；
10. `verify.sh` 调用未定义的 `say()`；pi journey 的 persona 标签误用 dsh 的。

## 4. 归属划分

- **夹具/CI/pins/文档**：验证方（我），上述 10 条已处理完；
- **产品侧**：三个宿主的"自主安装"链路若在放宽等待窗口后仍不通，属 SDK/宿主编排问题，需开发确认；
- **owner 已签字**：`pi C8 半条 + pi C17 = N/A`、`hermes C17 = RISK-ACCEPTED`（不要求改）。

## 5. 复现命令

```bash
cd /home/ubuntu/aimail
set -a; . ~/.hermes/profiles/verifier/.env; set +a
export LLM_API_KEY="$XIAOMI_API_KEY"
GW_HTTP_PORT=18720 GW_SMTP_PORT=13120 \
  bash tests/host-acceptance/v2/run-matrix.sh --sdk none --host hermes --stages "s1"
```
对照（应 PASS）：同样命令把 `--sdk none` 换成 `--sdk py`。
