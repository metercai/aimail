# L3 产品缺陷清单(供产品域修复)

> 来源:L3 对接闭环回归(`tests/l3/`,由 `l3-integration.yml` 9 格矩阵驱动),2026-10-08。
> 被测物全部来自**发布渠道**(GitHub Release / PyPI / npm),绝不来自工作树。
> 口径:L3 是上线最后一道防线,严格复现 README 公布的三段流程(自举 → SDK 安装 → `aimail welcome`),
> 不做产品修复、不自行适应和修订。以下每条均为**产品范畴**(自举脚本 / cli / SDK),
> 产品域修复后重触发 L3 验证。
>
> 每条含:现象 / 复现 / 期望 vs 实际 / 位置(file:line)/ 产品域 / 修复方向。
> 严重度:Blocker=阻断 README 公布流程 · Major=掩盖失败/契约不一致 · Minor=文档漂移。

> **2026-10-10 复核(当前修复队列态)**:今日两次 L3 全 9 格 `conclusion=failure`——
> ① 09:57(openclaw-aimail 0.1.50 publish success 触发)② 10:35(cli-release 0.1.38 success 触发)。
> 红先于 0.1.37(09:57 那次测的即是 0.1.37)⇒ **非本次发版引入,是发布产物链路上游的既有 Blocker**。
> 逐条复核 HEAD(0.1.38/0.1.49/0.1.50 之后)仍 live:
> - **C-3** detect all-of 全要 markers:`cli/src/core/platforms.rs:96` `markers.iter().all(...)` 仍在。
> - **C-1** 裸 install 不读 env:`cli/src/cmd/install.rs:272/610` `adm_key=a.admin_key.clone()`(只 -k flag),新建路径 gw_url 无 env 回落。
> - **C-4** hermes venv 失配:`cli/platforms.json` hermes `install_steps[0]` 仍钉 `{home}/hermes-agent/venv/bin/python`。
> - **C-7** install 不读 AGENT_HOME:`cli/src/cmd/install.rs:281-286`(ensure_system)+ `619-634`(install_human),home 只认 `--home`/`--system-id`,全仓 install 路径无 AGENT_HOME 消费。**修复方案已出,待 owner 批准后落码。**
> - **C-11 / C-12** prompt add ABI / rename 凭据:本轮已随 SDK 0.1.49 修复(L2 J5-7 全绿);但 L3 全 9 格未跑到 ⑤⑥ 无法闭环验证,保留待 L3 绿后确认。
> - **S-1 dsh-aimail 与 dsh 0.2.x 不兼容 —— 0.1.49 未修**(新核):`dsh-aimail@0.1.49` peerDependencies 仍为旧顶层包 `dsh-llm/dsh-agent/dsh-tools`(与 0.1.48 逐字相同),实际 `import` 亦然(inbound.ts:29 `@deepseek-ai/dsh-llm` / tools.ts:13 `@deepseek-ai/dsh-tools`);dsh latest `0.2.0-rc.2` 已拆成 `dsh-tool-*/dsh-agent-*` 细粒度包,不再提供这些顶层包 ⇒ **0.1.49 发版未动 S-1**,dsh-plugin L3 格仍红在兼容闸。
>
> 逐格首红(按未变 Blocker 集;新 run 的逐格逐行落点需读 CI 日志,gh 未登录 ⇒ 本地 L3 最小复现可补):
> - hermes × 3 + dsh-aimail × 3:④ `无法确定平台`(C-3,干净机无 profiles/storages)。
> - dsh-plugin × 3:④ dsh 兼容闸拒装(S-1,0.1.49 未修)。
> - **修复优先级(按 ④ 段首红拦截顺序):C-7(已修,e71ee21)→ C-3 → S-1 → C-1/C-4**。
>   C-7 是 hermes/dsh-aimail ④ 段裸 install 的最外层关口(此前 ④ 段在 install 入口即红,
>   根本到不了 detect);修后 ④ 段继续往下,hermes/dsh 会撞 C-3 detect,再往下才是 C-1/C-4。

## 汇总

| # | 产品域 | 缺陷 | 严重度 |
|---|--------|------|--------|
| C-1 | cli | 裸 `aimail install`(新建路径)不读 `AIMAIL_ADMIN_KEY` / `AIMAIL_GW_URL` env — README 场景 4 失败 | Blocker |
| C-4 | cli | hermes 适配器 venv 路径假设与当前官方 install.sh 布局失配 — aimailsdk 装不进 | Blocker |
| C-3 | cli | detect 为 all-of 全要 markers(hermes `[hermes-agent, profiles]` / dsh `[profiles, storages]`),官方安装不建 profiles/storages | Blocker(干净机, 双 agent, 已实证) |
| S-1 | SDK | dsh-aimail@0.1.48 与所有已发布 dsh 版本不兼容(peerDeps + 实际 import 均为 dsh 0.1.x 结构) | Blocker(dsh 双路径) |
| C-6 | cli | welcome 承诺的"本机单系统可自动判定"不存在(空 sid 直接返回空) — 多系统机裸 welcome 无上下文时必挂 | Major |
| C-7 | cli | install 不读 `AGENT_HOME` env 作 home 来源 — README 官方流程(裸 `aimail install` + `AGENT_HOME`)失败 | Blocker(README 官方流程) |
| C-8 | cli | hermes SDK 安装步 host 分支不防 uv `exclude-newer` 隔离(CWD 发现宿主 pyproject 的 14 天隔离)⇒ 静默装旧版 | Major(静默降级) |
| C-5 | cli | hermes SDK 安装步在 SDK 不可用时仍报 "✓ SDK install done" — 掩盖硬失败 | Major |
| B-1 | bootstrap | bootstrap 宣告 README 场景 4 裸流程(env ready + next steps 指向裸 `aimail install`),CLI 无法满足 | Major(契约不一致) |
| B-2 | bootstrap | release 发现走未认证 api.github.com(60/h IP 限流),并发/重试场景撞 403 且误报 "no cli-v* release found" | Major(CI 9 并发实证) |
| C-9 | cli | node 平台 SDK 门统一成 python aimailsdk 但 install 链不装(86babad Rust 化回归)⇒ 注册链 + reset/uninstall 全新安装全挂 | Blocker(3 平台注册/复位/注销) |
| C-10 | cli | deerflow 全新安装链断裂:`{sdk}` 在 install_steps 执行前一次性解析,首装 skill/mcp 装配必 127(rc=0 掩盖) | Major(deerflow 装配断裂) |
| C-11 | cli | `prompt add` CLI↔SDK ABI 不匹配:CLI 把 `prompt_rules` 嵌在 `updates`/`fields` 键下,SDK `_op_update` prompt 分支只读顶层 `args.get("prompt_rules")` ⇒ 恒 None ⇒ "缺少要更新的字段"(rc=1)。所有平台 `aimail prompt add` 全挂(仅 deerflow 本轮首装成功才真跑到) | Major(prompt 面全断) |
| C-12 | cli | `address -n`(rename)SDK 从 **agent 绑定**读 `gateway_url/admin_key`,但绑定从不写 `admin_key`(repair 回填列表 `(gateway_url,domain,system_name)` 刻意不含;install 也不写)⇒ 新装后 rename 必 "lacks gateway_url/admin_key"。应读系统级 syscfg 而非绑定 | Major(rename 面全断) |
| C-2 | cli | l1.rs 错误提示引用不存在的 `--with-key` flag 与 `AIMAIL_ADMIN_KEY` env 行为 | Minor(文档漂移) |

---

## CLI(cli)

### C-1 裸 `aimail install`(新建路径)不读 `AIMAIL_ADMIN_KEY` / `AIMAIL_GW_URL` env —— README 场景 C 失败

- **现象**:按 README 场景 C(自托管网关 + 系统级 key)公布的裸流程,`aimail install --home ~/.hermes` 失败,报误导性 `gateway_url is required`(rc=1)。
- **复现**(已实证,发布二进制 cli-v0.1.37):
  ```bash
  export AIMAIL_GW_URL=http://<gw>   # 场景 C
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
- **修复方向**:新建路径读取 `AIMAIL_ADMIN_KEY`(系统级 key)与 `AIMAIL_GW_URL` env;或 README/bootstrap 改为强制 `-k`/`-g`(见 B-1 契约不一致)。

### C-4 hermes 适配器 venv 路径假设与当前官方 install.sh 布局失配 —— aimailsdk 装不进

- **现象**:`aimail install --home ~/.hermes`(官方 install.sh 装好的 hermes)在 ④ SDK 安装步失败:`✗ ERROR: 运行时源未找到(pip aimail 未安装且仓库 pysdk/ 缺失)` + `ImportError: No module named 'aimail.install'`。
- **复现**:干净机 `curl install.sh | bash`(非 TTY)→ `aimail install --home ~/.hermes`。L3 CI run 实证:install.sh 把 venv 建在 `installs/<hash>/environments/<hash>/workspace/venv`(相对 CWD),而适配器 step 0 的 `when path_exists {home}/hermes-agent/venv/bin/python` 为 false → step 0 被跳过,aimailsdk 从未装入。
- **期望**:aimailsdk 装进 hermes gateway 实际使用的 venv,使 `import aimail` 可用。
- **实际**:`cli/platforms.json` hermes `install_steps[0]` 的 `when` 与 `argv` 均钉 `{home}/hermes-agent/venv/bin/python`,与当前官方 install.sh 的 PM 模型 venv 落点不符 → step 0 跳过 → 后续 `sdk_install`/`register` 全部无 SDK 可用。
- **产品域**:cli。
- **修复方向**:hermes 适配器的 python/venv 探测对齐当前 install.sh 实际布局(PM 的 workspace venv),而非硬编码 `{home}/hermes-agent/venv`。

### C-3 detect 为 all-of 全要 markers,官方安装不建 profiles/storages(hermes 与 dsh 双命中,均已实证)

- **现象**:干净机官方安装完成后,`aimail install --home <root>` 报 `✗ 无法确定平台: <root> 目录无特征`。L3 run 37883302756(042c715)实证:hermes 3 平台 + dsh-aimail 3 平台**全 6 格**均红在此行。
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
- **实际上下文链**(welcome.rs:25-48):`--system-id` flag > `AGENT_HOME` env → `{AGENT_HOME}/.agentmail` 指针(install/插件注册时写)→ 结束。无第三级。(contract-allowed: 缺陷报告引用契约指针文件名)
- **产品域**:cli。
- **修复方向**:实现报错文案承诺的"单系统自动判定"(`systems/*/` 唯一匹配),或修正文案;README 补 `AGENT_HOME` 上下文说明(用户侧)。

### C-7 `install` 不读 `AGENT_HOME` env 作 home 来源 —— README 官方流程(裸 `aimail install`)失败

- **现象**:README 公布的 CLI 入口是 `export AGENT_HOME=~/.hermes` + **裸 `aimail install`**(2026-10-09 定稿,与 `welcome` 的 `AGENT_HOME` 上下文通道对齐)。当前 CLI 报 `✗ install 需要 --home,或带 --system-id 以便从本地配置反查`,rc=1。
- **复现**:`HOME=<scratch> AGENT_HOME=<scratch>/.hermes aimail install`(agent 已装)→ 即报上述错误。L3 ④ 段(hermes / dsh-aimail 格)红在此。
- **期望 vs 实际**:期望 = 裸 install 从 `AGENT_HOME` env 解析 home(与 welcome/ping/check 的 `AGENT_HOME` 上下文一致);实际 = `install.rs:281-286` home 只认 `--home` flag / `--system-id` 反查,全仓 install 路径无 `AGENT_HOME` 消费。
- **产品域**:cli。
- **修复方向**:`install` 的 home 解析加入 `AGENT_HOME` env 回落(flag > `AGENT_HOME` > sid 反查),与 welcome 的上下文链对齐;或 README 回退为 `--home` 显式参数(但那样 AGENT_HOME 仅 welcome 用,两命令上下文不一致)。

### C-8 hermes SDK 安装步 host 分支不防 uv `exclude-newer` 隔离 ⇒ 静默装旧版

- **现象**:hermes 宿主机的 `[tool.uv] exclude-newer`(实测镜像 `/opt/hermes/pyproject.toml:451` 配 `exclude-newer = "14 days"`)会被 uv **按 CWD 向上发现**。产品 hermes `install_steps[0]`(host 分支)的 `uv pip install ... aimailsdk` **不带版本号也不做隔离豁免**;当 CWD 落在隔离配置覆盖范围内且"最新已发布版"超出隔离窗口时,uv 把最新版滤掉、**回退装 14 天内的旧版**——aimailsdk 静默降级,`import aimail` 可用但非最新,无任何告警。
- **复现**(已实证,aimail-host-hermes 镜像):容器 WORKDIR=`/opt/hermes`(隔离配置所在)下 `uv pip install --python /opt/hermes/.venv/bin/python3 --index-url https://pypi.org/simple/ aimailsdk==0.1.48` → `No solution found`(0.1.48 发布于 2026-10-07,超出 14 天窗口);同命令加 `--exclude-newer-package aimailsdk=false` → `Installed 1 package: + aimailsdk==0.1.48`(豁免目标包即修复)。不带 `==` 时 uv 回退旧版而非报错 ⇒ 静默。
- **对照**:同一 `install_steps` 的 **docker 分支已防**(`cd /; unset UV_EXCLUDE_NEWER UV_INDEX_URL ...` 中性 CWD + 清 env),host 分支没有 ⇒ 两分支行为不一致。
- **位置**:`cli/platforms.json` hermes `install_steps[0]`(kind=spawn,`when = {path_exists: {home}/hermes-agent/venv/bin/python, runtime_not: docker}`)。
- **产品域**:cli(platforms.json)。
- **修复方向**:host 分支对齐 docker 分支的隔离防护(中性 CWD 或 `--exclude-newer-package aimailsdk=false`),或装后断言已装版本 == PyPI 最新已发布(不匹配即 fail 而非静默)。门禁侧已按 `--exclude-newer-package aimailsdk=false` 自行规避(install-sdk.sh,2026-10-09)。

### C-5 hermes SDK 安装步在 SDK 不可用时仍报成功 —— 掩盖硬失败

- **现象**:同一 install 内先打印 `✓ hermes SDK install done(重启 hermes gateway 生效) — see warnings`,随后 `register` 步报 `✗ SDK 未安装或不可用`,整体 install 失败。成功/失败自相矛盾。
- **实际**:`cli/platforms.json` hermes `install_steps[2]`(kind=`sdk_install`)`on_error: warn` + `ok_text` 无条件打印 "SDK install done",即使 `import aimail` 失败(ImportError)也报成功 → 掩盖 C-4 的硬失败,误导诊断。
- **产品域**:cli。
- **修复方向**:SDK 不可 import 时该步应明确 fail(而非 warn + 假成功),让 install 在正确步骤失败并给出准确原因。

### C-9 node 平台(dsh/pi/openclaw)SDK 门要求 python aimailsdk 但 install 链不装 —— 注册链 + reset/uninstall 全新安装全挂(Rust 化回归)

- **现象**:node 平台(dsh/pi/openclaw)上:
  ① 注册链 `✗ <plat> agent registration failed ... 异常: TransportError: 可执行门输出异常(rc=1, 0 行): /usr/local/bin/python3: ... ModuleNotFoundError: No module named 'aimail'`;
  ② `aimail reset` / `aimail uninstall` 硬退出 rc=2,报 `✗ SDK 未安装或不可用:ERROR: 运行时源未找到(pip aimail 未安装且仓库 pysdk/ 缺失)`。
  install 的 npm 步本身成功(dsh plugin add / pi install / openclaw plugins install),但注册链与 reset/uninstall 全挂。
- **复现**(已实证,CLI L2 journey 全跑 2026-10-09,`/tmp/cli-l2-logs/20261009-192801`):dsh/pi/openclaw 容器注册失败(No module named 'aimail')+ reset/uninstall rc=2,共 ~30 处红(3 平台 × 注册/reset/uninstall/check + 级联 J2/prompt/J4/J6)。
- **期望 vs 实际**:期望 = node 平台按 `register.rs:233` 的"按 kind 惰性解析"走 node SDK(register-cli.js),不强制 python aimailsdk;实际 = Rust 化(`86babad`,owner 2026-10-07)把 SDK 门统一成 `python3 -m aimail.sdk_ops`(`sdk.rs:203-214` door_command_in,`-m aimail.sdk_ops`,零路径、无 node 分支),而 node 平台 install_steps 只装 npm 包、从不装 python aimailsdk ⇒ 门必中 `ModuleNotFoundError`。legacy python CLI 对 node 平台是 node_entry dispatch(`cli/aimail:2296 _resolve_node_entry` 直 spawn `register-cli.js`,不经 python 门)⇒ 0929 基线 openclaw journey 51 PASS 0 FAIL;Rust 化后丢失该路径。owner `86babad` 自报"容器内 aimailsdk=0.1.48 · 五平台 7/0"靠的是验证环境**预装** aimailsdk,掩盖了缺口 —— 全新安装环境必炸。
- **位置**:`cli/src/core/sdk.rs:203-214`(door 一律 `-m aimail.sdk_ops`);`cli/src/core/register.rs:230-237`(assemble 走 sdk_ops_call,按 kind 惰性解析 SDK 根但门本身要 python 包);`cli/src/cmd/reset.rs:185` / `uninstall.rs:187`(无条件 resolve_or_placeholder);`cli/src/core/sdkroot.rs:157-166`(resolve 失败 exit(2));`cli/platforms.json` dsh/pi/openclaw install_steps(只 npm,无 aimailsdk)。
- **产品域**:cli。
- **严重度**:Blocker(3 平台注册链 + reset/uninstall 全新安装不可用)。
- **修复方向**:二选一 —— (a) door 恢复按 kind dispatch(node 平台走 register-cli.js,对齐 sdk.rs:8 的声明与 register.rs:233 口径);或 (b) node 平台 install_steps 补装 python aimailsdk(与 hermes/deerflow 一致),使 `-m aimail.sdk_ops` 可用。选 (a) 更贴合"node 平台无 pysdk"既定口径;选 (b) 省事但违背 register.rs:233。

### C-10 deerflow 全新安装链断裂:`{sdk}` 在 install_steps 执行前一次性解析,首装 skill/mcp 装配必 127

- **现象**:deerflow `aimail install` 在 skill/mcp 装配步报 `bash: /deer-flow/install-skill.sh: No such file or directory` + `✗ bash /deer-flow/install-skill.sh exit 127`(install-mcp.sh 同)。install 整体 rc=0(掩盖硬失败,同 C-5 形态),但 skill/toolset 装配实际未发生。
- **复现**(已实证,CLI L2 journey 全跑 2026-10-09,`/tmp/cli-l2-logs/20261009-192801`):deerflow 容器 install 段 aimailsdk 装上了(✓ import 成功),但 `install_steps[2]`(bash {sdk}/deer-flow/install-skill.sh)/[3](install-mcp.sh)展开成 `/deer-flow/...`(空 sdk_root)⇒ 127。
- **机制**:`install.rs:894-899` 在 install_steps **执行前**一次性 `sdkroot::resolve()` 取 `{sdk}`;deerflow `install_steps[0]` 才装 aimailsdk ⇒ 解析时 SDK 未装 ⇒ resolve 失败 ⇒ `{sdk}` 回落 core_dir(也空)⇒ 步 [2]/[3] 的 `{sdk}/...` 展开成不存在路径。`86babad` commit 声称"steps.rs: {sdk} 实时解析"但代码是执行前一次性解析,step[0] 装完后**不重解析**。
- **位置**:`cli/src/cmd/install.rs:876-899`({sdk} 预解析);`cli/platforms.json` deerflow install_steps[2]/[3](引用 {sdk})。
- **产品域**:cli。
- **严重度**:Major(deerflow 全新安装 skill/toolset 装配断裂,且 rc=0 掩盖)。
- **修复方向**:`{sdk}` 改为 install_steps **执行期**实时解析(step[0] 装完后重解析),或 deerflow 的 skill/mcp 步改用 SDK 自足 inline 入口(不经 {sdk} 路径)。

### C-11 `prompt add` CLI↔SDK ABI 不匹配:`prompt_rules` 键名对不上,prompt 面全断

- **现象**:`aimail prompt add -s <sid> -e <addr> -n 20_j5matrix --subject j5matrix` ⇒ rc=1,`update_binding failed: Usage("update action=prompt: 缺少要更新的字段")`。
- **复现**(已实证,CLI L2 journey 全跑 2026-10-09,`/tmp/cli-l2-logs/20261009-231637`):deerflow J5-7a(J5 子命令矩阵,活环境有在册地址 `agent@sdk-e2e-deerflow-cli.local`)。此前各平台要么注册失败无地址(C-9/C-4 下游)要么无在册地址,prompt add 这一格全平台 CANNOT JUDGE,此 ABI 缺口从未被测到。
- **机制**:Rust CLI `prompt.rs:81-84` 把补丁放进 `{"system_id", "binding", "action", "updates": patch, "fields": patch}`(`patch` 含 `prompt_rules`);SDK 门 `sdk_ops.py:522` 的 prompt 分支只读**顶层** `args.get("prompt_rules")` ⇒ 恒 None ⇒ `updates` 过滤后为空 ⇒ `UsageError("update action=prompt: 缺少要更新的字段")`。`_op_update` 入口(443-530)与 dispatch 表(568)均无 `updates`/`fields` 解包逻辑。
- **位置**:`cli/src/cmd/prompt.rs:81-95`(调用方)↔ `pysdk/sdk_ops.py:520-528`(SDK 门 prompt/persona/webhook-secret 分支)。
- **产品域**:cli(ABI 契约 v1.0 §4.1 的调用面)。
- **严重度**:Major(`prompt add/rm 之外的受控 prompt 变更面`全断;persona/webhook-secret 同分支同形态,待各自旅程暴露)。
- **修复方向**:二选一对齐——SDK `_op_update` 入口统一从 `args["updates"]/args["fields"]` 解包后按 action 取字段(推荐,兼容两种键名);或 CLI 侧把 `prompt_rules` 提到 args 顶层。

### C-12 `address -n`(rename)从 agent 绑定读网关凭据,而绑定从不写 `admin_key` ⇒ 新装后 rename 必挂

- **现象**:`aimail address -s <sid> -e <addr> -n <new>` ⇒ rc=1,`地址改名失败: system config lacks gateway_url/admin_key`。
- **复现**(已实证,CLI L2 journey 全跑 2026-10-09):deerflow J5-6ea(改名→信号→路由跟随,活环境有在册地址;J1 install -k rc=0、J2 install -c rc=0,系统级 cfg 里 gateway_url/admin_key 俱在)。
- **机制**:SDK `_op_update` rename 分支(`sdk_ops.py:461-463`)把 `_agent_binding(...)` 取回的**绑定 cfg** 传给 `rename_address(system_id, old_email, new_name, cfg)`;`aimail_base.py:560-562` 在该 cfg 上找 `gateway_url`+`admin_key`,缺则抛 "system config lacks gateway_url/admin_key"。而绑定从不写 `admin_key`:install 不写;repair 回填(`sdk_ops.py:471-476`)列表刻意只含 `("gateway_url","domain","system_name")`。⇒ 任何新装系统的首次 rename 必挂。系统级 syscfg(同函数 449 行已加载,含 admin_key)就在手边却未被 rename 使用。
- **位置**:`pysdk/sdk_ops.py:461-463`(传参)+ `pysdk/aimail_base.py:530-562`(rename_address 凭据读取)。
- **产品域**:cli(SDK 门,agent 命名/改名域)。
- **严重度**:Major(rename 面全断;改名是 README 日常操作面)。
- **修复方向**:`rename_address` 的凭据读取改用系统级 syscfg(或 `_op_update` rename 分支把 syscfg 的 gateway_url/admin_key 合入后传入),绑定只作 email 定位源。

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
- **修复方向**:与 C-1 联动——要么 CLI 读 `AIMAIL_ADMIN_KEY`/`AIMAIL_GW_URL`,要么 bootstrap 的 env 闸门 + next steps 改为指引 `aimail install -k <key> --home <root>`。

### B-2 release 发现走未认证 api.github.com,撞 IP 限流且误报 "no release"

- **现象**:L3 run 37883302756(9 格并发)中,macos-arm64-dsh-plugin 格 ③ 在线自举失败:`curl: (56) The requested URL returned error: 403`(api.github.com 限流)→ 误报 `✗ no cli-v* release found on metercai/aimail — the CLI has not been released yet`(CLI 已正常发布,误导)。同一 run 同平台其它格的 ③ 正常 ⇒ 非资产缺失,是限流。
- **复现**:多 job 并发(或 CI 共享出口 IP 高流量)下 `bash scripts/bootstrap.sh`;`curl --retry 2` 不重试 403,`-f` 下静默失败,`TAG` 为空后落入资产缺失分支。
- **位置**:`scripts/bootstrap.sh:64-68`(tag 发现,未认证 `api.github.com/repos/$REPO/releases?per_page=30`,60/h IP 限流)+ `75-79`(asset 发现,`releases/tags/$TAG`,同样未认证)。
- **期望**:release 发现在 CI 并发下稳健;限流时给出可区分的错误(限流 vs 真无资产),不误报"未发布"。
- **产品域**:bootstrap。
- **修复方向**:发现改走 `https://github.com/$REPO/releases/latest` 重定向(web 端点,CDN 分发,不受 API IP 限流)+ 对 403 单独处理/退避重试。L3 门禁侧已按此思路自行规避(run-l3.sh ② 已改 latest 重定向,见 042c715),bootstrap 需产品侧同样处理。

---

## 附:L3 测试侧环境准备(与产品缺陷的边界)

L3 当前**不携带**任何掩盖产品缺陷的脚手架(2026-10-09 移除):
- hermes `mkdir -p profiles` 脚手架已移除 —— B4 实证(干净 HERMES_HOME 无 profiles/ 时 hermes 默认 profile gateway 正常运行,webhook 端口可达)⇒ 按裁决归**产品适配**(C-3),让门禁红在 C-3 上,不绕。
- dsh `mkdir -p storages` 脚手架已移除 —— 官方 warmup 不建 storages/(dsh 运行期自建)⇒ 同归 C-3(dsh 侧)。
- dsh pnpm 预热**保留** —— 属 dsh 基础环境(journey Dockerfile.dsh / r43 教训:冷容器首调用联网拉 pnpm 坏网挂死),是"agent 运行环境完备"的组成部分,非产品缺陷掩盖。

> 结论:当前 L3 红 = 正确拦截。9 格矩阵(3 平台 × hermes / dsh-aimail / dsh-plugin)run 37883302756(042c715)逐格落点:
> - hermes × 3 + dsh-aimail × 3:红在 ④ `无法确定平台`(C-3,双 agent 全实证)
> - dsh-plugin × 2(linux):红在 ④ 插件兼容闸(S-1);macos-dsh-plugin 红在 ③(B-2 限流)
>
> C-1 / C-4 / C-3 / S-1 为四个 Blocker,修复后重触发 L3 验证闭环。
