# L3 产品缺陷清单(供产品域修复)

> 来源:L3 对接闭环回归(`tests/l3/`,由 `l3-integration.yml` 9 格矩阵驱动),2026-10-08。
> 被测物全部来自**发布渠道**(GitHub Release / PyPI / npm),绝不来自工作树。
> 口径:L3 是上线最后一道防线,严格复现 README 公布的三段流程(自举 → SDK 安装 → `aimail welcome`),
> 不做产品修复、不自行适应和修订。以下每条均为**产品范畴**(自举脚本 / cli / SDK),
> 产品域修复后重触发 L3 验证。
>
> 每条含:现象 / 复现 / 期望 vs 实际 / 位置(file:line)/ 产品域 / 修复方向。
> 严重度:Blocker=阻断 README 公布流程 · Major=掩盖失败/契约不一致 · Minor=文档漂移。

## 汇总

| # | 产品域 | 缺陷 | 严重度 |
|---|--------|------|--------|
| C-1 | cli | 裸 `aimail install`(新建路径)不读 `AIMAIL_ADMIN_KEY` / `AIMAIL_URL` env — README 场景 4 失败 | Blocker |
| C-4 | cli | hermes 适配器 venv 路径假设与当前官方 install.sh 布局失配 — aimailsdk 装不进 | Blocker |
| C-3 | cli | detect 为 all-of 全要 markers(hermes `[hermes-agent, profiles]` / dsh `[profiles, storages]`),官方安装不建 profiles/storages | Blocker(干净机, 双 agent) |
| S-1 | SDK | dsh-aimail@0.1.48 与所有已发布 dsh 版本不兼容(peerDeps + 实际 import 均为 dsh 0.1.x 结构) | Blocker(dsh 双路径) |
| C-6 | cli | welcome 承诺的"本机单系统可自动判定"不存在(空 sid 直接返回空) — 多系统机裸 welcome 无上下文时必挂 | Major |
| C-5 | cli | hermes SDK 安装步在 SDK 不可用时仍报 "✓ SDK install done" — 掩盖硬失败 | Major |
| B-1 | bootstrap | bootstrap 宣告 README 场景 4 裸流程(env ready + next steps 指向裸 `aimail install`),CLI 无法满足 | Major(契约不一致) |
| C-2 | cli | l1.rs 错误提示引用不存在的 `--with-key` flag 与 `AIMAIL_ADMIN_KEY` env 行为 | Minor(文档漂移) |

---

## CLI(cli)

### C-1 裸 `aimail install`(新建路径)不读 `AIMAIL_ADMIN_KEY` / `AIMAIL_URL` env —— README 场景 C 失败

- **现象**:按 README 场景 C(自托管网关 + 系统级 key)公布的裸流程,`aimail install --home ~/.hermes` 失败,报误导性 `gateway_url is required`(rc=1)。
- **复现**(已实证,发布二进制 cli-v0.1.37):
  ```bash
  export AIMAIL_URL=http://<gw>   # 场景 C
  export AIMAIL_ADMIN_KEY=<系统级 key>
  export AIMAIL_DOMAIN=example.com
  export AIMAIL_MANAGER_ADDRESS=you@example.com
  aimail install --home ~/.hermes   # 裸命令,无 -k/-g
  # → ✗ gateway_url is required
  ```
  无 env 的裸 install 同样报 `gateway_url is required`(而非 "no credential")。
- **期望**:env 已设时,裸 install 用 env 里的 URL + 系统级 key 完成激活(README.md:127-141 明确 `AIMAIL_ADMIN_KEY`(或 `aimail install -k`)取系统级 key)。
- **实际**:
  - `adm_key` 仅取 `-k` flag,不读 env:`cli/src/cmd/install.rs:610` `let adm_key = a.admin_key.clone();`(新建路径凭据装配 707-731 三选一:产品码 / `-k` flag / sid 复用,全空时**静默进 setup** 而非报错)。
  - 新建路径 `gw_url` 不回落 env:`cli/src/cmd/install.rs:687-688` `resolve_gateway_url` 仅在复用分支(`!sid.is_empty() && prod_code.is_empty()`)被调用;新建路径 `gw_url = a.gateway_url`(flag,空)→ `setup.rs:59` 报 `gateway_url is required`。
  - 全 CLI 源码无 `AIMAIL_ADMIN_KEY` env 消费(仅 `cli/src/core/checks/l1.rs:123` 一句提示字符串)。
- **产品域**:cli。
- **修复方向**:新建路径读取 `AIMAIL_ADMIN_KEY`(系统级 key)与 `AIMAIL_URL` env;或 README/bootstrap 改为强制 `-k`/`-g`(见 B-1 契约不一致)。

### C-4 hermes 适配器 venv 路径假设与当前官方 install.sh 布局失配 —— aimailsdk 装不进

- **现象**:`aimail install --home ~/.hermes`(官方 install.sh 装好的 hermes)在 ④ SDK 安装步失败:`✗ ERROR: 运行时源未找到(pip aimail 未安装且仓库 pysdk/ 缺失)` + `ImportError: No module named 'aimail.install'`。
- **复现**:干净机 `curl install.sh | bash`(非 TTY)→ `aimail install --home ~/.hermes`。L3 CI run 实证:install.sh 把 venv 建在 `installs/<hash>/environments/<hash>/workspace/venv`(相对 CWD),而适配器 step 0 的 `when path_exists {home}/hermes-agent/venv/bin/python` 为 false → step 0 被跳过,aimailsdk 从未装入。
- **期望**:aimailsdk 装进 hermes gateway 实际使用的 venv,使 `import aimail` 可用。
- **实际**:`cli/platforms.json` hermes `install_steps[0]` 的 `when` 与 `argv` 均钉 `{home}/hermes-agent/venv/bin/python`,与当前官方 install.sh 的 PM 模型 venv 落点不符 → step 0 跳过 → 后续 `sdk_install`/`register` 全部无 SDK 可用。
- **产品域**:cli。
- **修复方向**:hermes 适配器的 python/venv 探测对齐当前 install.sh 实际布局(PM 的 workspace venv),而非硬编码 `{home}/hermes-agent/venv`。

### C-3 detect 为 all-of 全要 markers,官方安装不建 profiles/storages(hermes 与 dsh 双命中)

- **现象**:干净机官方安装完成后,`aimail install --home <root>` 报 `✗ 无法确定平台: <root> 目录无特征`(hermes 实证于 L3 CI run 3;dsh 同类未单独实证,见实际)。
- **复现**:
  - hermes:`curl install.sh | bash`(非 TTY,无 `hermes setup`)→ `aimail install --home ~/.hermes`。
  - dsh:`npm i -g @deepseek-ai/dsh` + `dsh --profile web --dump-config` warmup 后(官方 warmup 只建 profiles/,不建 storages/ —— 本地实证)→ `aimail install --home ~/.dsh`。
- **期望**:官方安装产物即可被 detect 识别为对应平台。
- **实际**:
  - `cli/src/core/platforms.rs:96` `markers.iter().all(|m| d.join(m).exists())` = **all-of(全要)**。
  - hermes `detect.markers = ["hermes-agent","profiles"]`;官方 install.sh `stage_config`(line 862-868)只建 `hermes-agent + cron/sessions/...`,**不建 `profiles`**(仅 `hermes profile import/create` 显式建,profiles.py:2227)。
  - dsh `detect.markers = ["profiles","storages"]`;官方 warmup 不建 `storages/`(dsh 运行 12s 后才自建)⇒ 安装时点 detect 必挂。
  - 生产机两目录均为使用期产物(存在),故生产可过——干净机必挂。
  - 注:hermes 侧 B4 实证已排除"hermes agent 运行依赖 profiles/"的假设(干净 HERMES_HOME 无 profiles/ 时默认 profile gateway 3s 起、webhook 端口可达)——detect 放宽不影响 hermes 运行。
- **产品域**:cli。
- **修复方向**:detect 放宽为平台特征 marker(hermes `hermes-agent` / dsh `profiles`),或 install 时自建缺失目录,与官方安装布局一致。

### C-6 welcome 承诺的"本机单系统可自动判定"不存在 —— 多系统机裸 welcome 无上下文时必挂

- **现象**:`aimail welcome`(裸,无 `--system-id` 且 `AGENT_HOME` 未设/无指针)报 `✗ system_id 未解析(需 --system-id,或本机单系统/平台指针可自动判定)`(rc=1)。报错文案承诺的"本机单系统可自动判定"路径**不存在**——多系统机(README Notes 明确支持 "one machine can host several Agent platforms")上,用户未 export `AGENT_HOME` 时裸 welcome 无路可走,且报错文案误导。
- **位置**:`cli/src/cmd/welcome.rs:50-61`(sid 为空 → `uninstall::resolve_system_id(ah, "")` → `cli/src/cmd/uninstall.rs:45-47` 空 explicit_sid **直接返回空**,无 systems/ 扫描);错误文案 welcome.rs:59。
- **实际上下文链**(welcome.rs:25-48):`--system-id` flag > `AGENT_HOME` env → `{AGENT_HOME}/.agentmail` 指针(install/插件注册时写)→ 结束。无第三级。
- **产品域**:cli。
- **修复方向**:实现报错文案承诺的单系统自动判定(`systems/*/` 唯一匹配),或修正文案;README 补 `AGENT_HOME` 上下文说明(用户侧)。

### C-5 hermes SDK 安装步在 SDK 不可用时仍报成功 —— 掩盖硬失败

- **现象**:同一 install 内先打印 `✓ hermes SDK install done(重启 hermes gateway 生效) — see warnings`,随后 `register` 步报 `✗ SDK 未安装或不可用`,整体 install 失败。成功/失败自相矛盾。
- **实际**:`cli/platforms.json` hermes `install_steps[2]`(kind=`sdk_install`)`on_error: warn` + `ok_text` 无条件打印 "SDK install done",即使 `import aimail` 失败(ImportError)也报成功 → 掩盖 C-4 的硬失败,误导诊断。
- **产品域**:cli。
- **修复方向**:SDK 不可 import 时该步应明确 fail(而非 warn + 假成功),让 install 在正确步骤失败并给出准确原因。

### C-2 l1.rs 错误提示引用不存在的 `--with-key` flag 与 `AIMAIL_ADMIN_KEY` env 行为

- **现象**:check 提示文案 `Run: aimail install --with-key (或 AIMAIL_ADMIN_KEY 激活)`,但 `aimail install --help` 无 `--with-key`,且 CLI 不读 `AIMAIL_ADMIN_KEY` env(见 C-1)。
- **位置**:`cli/src/core/checks/l1.rs:123`。
- **产品域**:cli。
- **修复方向**:提示对齐 CLI 实际接口(`-k <key>`)。

---

## SDK(tssdk)

### S-1 dsh-aimail@0.1.48 与所有已发布 dsh 版本不兼容

- **现象**:`dsh plugin --profile web add dsh-aimail`(以及 `aimail install --home ~/.dsh` 内部同源的 plugin add)被 dsh 兼容闸拒装:
  `dsh: installation rejected: Plugin dsh-aimail@0.1.48 is incompatible with dsh 0.2.0-rc.2: peerDependencies {...}`。
- **复现**:`npm i -g @deepseek-ai/dsh`(装到 0.2.0-rc.2,09-29 发)→ `dsh plugin add dsh-aimail`(装到 0.1.48,10-07 发)→ 拒装。本地 dsh 0.1.7-rc.1 同样被拒(缺 dsh-llm/dsh-tools)。
- **期望**:已发布 dsh-aimail 与已发布 dsh 兼容,用户可正常装插件。
- **实际**:
  - `tssdk/packages/dsh-aimail/package.json`(0.1.48)`peerDependencies = {cordis ^4.0.1, dsh-agent ^0.1.0-rc.6, dsh-llm ^0.0.1-rc.1, dsh-tools ^0.0.1-rc.1}`,且插件代码**实际 import** 这些包(dsh-tools ×2、dsh-llm ×2、dsh-agent ×1、cordis ×6)。
  - dsh 0.2.0-rc.2 已重构为细粒度 `dsh-tool-*` / `dsh-agent-*` 包,**不再提供** dsh-llm/dsh-tools/dsh-agent 顶层包 → 兼容闸正确拒装,且即使绕过闸门运行期 import 也会失败。
  - 即:当前发布的 dsh-aimail 与**任何**已发布 dsh 都不兼容。
- **产品域**:SDK(tssdk)。
- **修复方向**:dsh-aimail 对齐 dsh 0.2.x 包结构(改 peerDeps + import)后重新发布。

---

## bootstrap(scripts/bootstrap.sh)

### B-1 bootstrap 宣告 README 场景 C 裸流程,CLI 无法满足(契约不一致)

- **现象**:bootstrap 的 env 就绪闸门**接受** `AIMAIL_ADMIN_KEY`(README 场景 C),其 "next steps" 明确指引用户跑**裸** `aimail install --home <agent-host-root>`(无 `-k`),但该裸流程因 C-1 必然失败。
- **位置**:
  - env 就绪闸门:`scripts/bootstrap.sh:149-156`(step 8 `have_env`),line 153 `have_env AIMAIL_ADMIN_KEY && have_env AIMAIL_DOMAIN` 作为合法场景 C → 打印 `env ready (one of the README scenarios)`。
  - next steps:`scripts/bootstrap.sh:163`(step 9)`say "  1. activate a system: →  aimail install --home <agent-host-root>"`。
- **期望**:bootstrap 宣告的流程与 CLI 实际能力一致。
- **实际**:bootstrap 持久化 `AIMAIL_ADMIN_KEY` 到 `~/.aimail/.env` 并放行,但 CLI 不读该 env(C-1)→ 用户按 bootstrap 指引走到 install 即失败。
- **产品域**:bootstrap(与 cli 的契约不一致;根因在 C-1)。
- **修复方向**:与 C-1 联动——要么 CLI 读 `AIMAIL_ADMIN_KEY`/`AIMAIL_URL`,要么 bootstrap 的 env 闸门 + next steps 改为指引 `aimail install -k <key> --home <root>`。

---

## 附:L3 测试侧环境准备(与产品缺陷的边界)

L3 当前**不携带**任何掩盖产品缺陷的脚手架(2026-10-09 移除):
- hermes `mkdir -p profiles` 脚手架已移除 —— B4 实证(干净 HERMES_HOME 无 profiles/ 时 hermes 默认 profile gateway 正常运行,webhook 端口可达)⇒ 按裁决归**产品适配**(C-3),让门禁红在 C-3 上,不绕。
- dsh `mkdir -p storages` 脚手架已移除 —— 官方 warmup 不建 storages/(dsh 运行期自建)⇒ 同归 C-3(dsh 侧)。
- dsh pnpm 预热**保留** —— 属 dsh 基础环境(journey Dockerfile.dsh / r43 教训:冷容器首调用联网拉 pnpm 坏网挂死),是"agent 运行环境完备"的组成部分,非产品缺陷掩盖。

> 结论:当前 L3 红 = 正确拦截。C-1 / C-4 / C-3 / S-1 为四个 Blocker(双 agent 双路径全覆盖),修复后重触发 L3 验证闭环。
