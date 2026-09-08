# SDK 适配与安装:关键环节与上线必检点(中文真源)

> 经验备案 + 新 SDK 开发参考 + 上线检测基准。来源:五平台生产实装
> (hermes/openclaw/dsh/pi/deer-flow)、v0.1.9→v0.1.11 三轮审计(P1/P2/P3
> 共 51 项)与 L2.5 宿主回归(rc.1→rc.7)中暴露的全部真实问题。
> 每一条"必检点"都对应一个曾经真实翻车的坑。
> 英文版:SDK-ADAPTER-GUIDE.md(本文件为真源,两版语义同步)。

---

## 0. 三大核心要点(适配的灵魂,先读这个)

### 0.1 Agent 的 session 创建和调用

**关键环节**:agent 会话的标识与地址的绑定关系,决定注册链的形态。

| 形态 | 平台 | 语义 | 适配要点 |
|---|---|---|---|
| 多 agent(一等) | hermes(profiles/*)、openclaw(agents/*)、deer-flow(assistants) | 平台枚举 N 个 agent,每个 agent 一个地址;`register_all` 全量注册 | SDK 提供 `register_all` 入口,枚举平台 agent 集合逐个跑注册链;单 agent 是长度 1 的退化 |
| 单 agent | pi、dsh | 一个运行时地址,session 级派生 | pi=指针单 agent;**dsh=session 惰性绑定**(首次使用自动绑,一 session ⇔ 一地址,存在性守卫) |

**必检点**:
- [ ] `resolveBySessionId` 路径:session→地址的映射在你的平台怎么落地(dsh 按 sessionId 查 agentmail.json;pi 按指针)
- [ ] 多 agent 平台:枚举 API 真实性(hermes=profiles 目录扫描;deer-flow=`list_custom_agents()`;openclaw=readdir agents/*)
- [ ] 单 agent 退化:`--all-agents` 在无 register_all 定义平台必须零感退化(不是报错)
- [ ] session 惰性绑定的幂等:同一 session 二次绑定不新建地址(dsh 存在性守卫)

### 0.2 Agent 的标识方法

**关键环节**:一个地址背后站着谁,必须是单一可信源。

- **指针文件**(`{platform_home}/.agentmail`):`{system_id, email}` —— 平台↔系统的绑定事实,单 agent 平台的标识真源
- **agentmail.json**(address 级):`systems/{sid}/{cleaned_email}/agentmail.json` —— 地址级凭证唯一真源(api_key/webhook/webhook_secret/manager)
- **email 派生**:统一规则 `{agent_name}@{domain}`;agent 名清洗用 `_clean_agent_dir_name`(同源函数,防路径遍历——曾真实审计发现不同实现不一致)

**必检点**:
- [ ] 指针落盘:注册成功后 `{home}/.agentmail` 必须存在且 JSON 可解析(check_status 探针项)
- [ ] 别名归一:`agent.xian@` 与 `xian@`、`agent.` 前缀剥离必须走 `email_for_agent` 同一实现(降级副本仅 import 失败兜底;主路径禁止平台各自重写——曾因两份实现行为漂移被审计点名)
- [ ] 凭证文件权限:agentmail.json/webhook_subscriptions 必须 0600(曾以 0644 落盘被审计点名)
- [ ] `[REDACTED]` 纪律:任何输出/日志/文档不得泄露 api_key/webhook_secret

### 0.3 toolset/skills 的注册和调用

**关键环节:这是两个范畴,缺一不可**(审计中曾被误判为可选,用户裁定纠正):

| 范畴 | 内容 | 载体 | 各平台现状 |
|---|---|---|---|
| tools 注册 | 工具怎么用(name/description/parameters) | 进程内 `registerTool`(TS 平台)/MCP server(pysdk) | 五平台全有 |
| SKILL | inbound message 的 6 步处理流程(协议级规范) | `resources/skills/SKILL.md` 释放到平台 skills 目录 | hermes=pysdk install 释放 profiles/*/skills;**openclaw/dsh/pi=插件 entry 释放到各自 skills 目录**(v0.1.11 修复——曾断链:CLI 拆分删了释放脚本没人接管,SKILL 缺失导致 agent 不懂入站协议) |

**skills 释放必检点**(三平台对称铁律):
- [ ] openclaw:`~/.openclaw/skills/agentmail/`(entry 幂等释放,内容相同跳过)
- [ ] dsh:`<dshHome>/skills/agentmail/`(dshHome 解析:AIMAIL_SYSTEM_HOME > DSH_HOME > ~/.dsh,镜像 mail-service;dsh skill-filesystem provider 拥有 `<dshHome>/skills`)
- [ ] pi:`~/.pi/agent/skills/agentmail/`(pi 用户 skills 根)
- [ ] hermes:`{home}/profiles/*/skills/agentmail/`(install 时全 profile 覆盖)
- [ ] **释放责任归插件**(资源在包内,不依赖外部 CLI);check_status 对每个平台有 skills 探针项
- [ ] 新增平台必须同时做 tools + skills 两件事,只做 tools = agent 收得到信但不会处理

**tools 注册必检点**:
- [ ] 工具语义唯一源 = mail-core `tool-registry.ts`(13 bare tools);Python 侧 parity 测试逐参数对齐(类型 integer/number/boolean 曾漏被 parity 测试拦截)
- [ ] 工具名跨平台一致(SKILL.md 里的 bare name 与注册名严格一致,否则 agent 学了流程调不到工具)
- [ ] CLI 命令面 ≠ chat 命令面(openclaw:`api.registerCommand` 只注册 chat 侧;CLI 分发需 `api.registerCli` registrar;manifest `cliCommands` 只是 help 占位——三层曾各断一次,L2.5 宿主回归才暴露)

---

## 1. 语言归属与代码布局(边界规则)

**一条铁律**:平台适配代码必须用平台宿主的语言。

| 平台 | 语言 | SDK 包 | 注册入口形态 |
|---|---|---|---|
| hermes | Python | pysdk | `python_script`(register_profiles.py,HERMES_PROFILE_DIR env 串接) |
| deer-flow | Python | pysdk | `python_script`(manage.py register --all,需 --manager) |
| openclaw | TS | openclaw-aimail | `host_command`(openclaw aimail register-all) |
| dsh | TS | dsh-aimail | register-cli node 入口(session 惰性绑定) |
| pi | TS | pi-aimail | register-cli node 入口 |

**必检点**:
- [ ] CLI(python)永远不写平台协议;注册表驱动(platforms.json 唯一知识源),平台字面=0(gate 白名单空)
- [ ] TS 包自带 register-cli 随包发布(`files` 字段含 dist/)
- [ ] 复用不抢归属:共享逻辑进 mail-core,不在平台包复制粘贴(审计发现的 12→13 工具注释漂移就是复制粘贴病)

## 2. 安装链(四步:环境变量 → 自举 → 宿主安装 → 闭环验证)

### 2.1 环境变量设置
- [ ] `AIMAIL_SYSTEM_HOME` 优先于平台默认 home(多平台机禁"任意即短路")
- [ ] AIMAIL_HOME 解析唯一真源 = `aimail_base.aimail_home()`(用户裁定:一致性来自构造;cli 侧全部 import 它,import 失败的降级副本仅坏态保底)
- [ ] `X-AIMail-Agent` 头 = `{platform}/{真实宿主版本}+{主模型}`(版本探测失败才退占位)

### 2.2 aimail 自举(ensure-system)
- [ ] 单 agent 第一人称自举:`aimail ensure-system -H {home}`(SDK 反调 CLI,单行 JSON)
- [ ] 自举=系统激活唯一路径(激活码在 CLI 侧,SDK 只反调——B 方案裁定)
- [ ] 新机器 activation pending 是正常态,register 链必须处理 `activation_code` 保存/重试(deer-flow 曾因 pending 死循环被 P1 点名)

### 2.3 宿主安装(install)
- [ ] install_steps 必须含 register_default(曾缺失导致装完未绑,P2-12)
- [ ] 批量安装的 webhook 串接:多 profile 循环必须每 profile 传独立 HERMES_PROFILE_DIR(P2-1,曾全部串到第一个)
- [ ] default sid 失配静默→对齐 named 分支重注册(P2-9)
- [ ] 卸载对称:uninstall_steps 覆盖 skills/board/指针/工具的清理

### 2.4 闭环验证(安装后必跑)
- [ ] `aimail check` 核心项全 ✓:指针/agentmail.json 完整/webhook 密码学验证/ping-pong
- [ ] 幂等重跑:reset/install 二次执行零新建、零破坏
- [ ] 出站实测:ping_test 发到 manager 收到
- [ ] 入站实测:外部 SMTP → 网关 → 本地端点收到(双路铁律)

## 3. 注册链与网络模型

- [ ] webhook_url 三态决议(resolveRegisterWebhook):显式 env > 平台默认端口 > 空(空=不下发,不猜)
- [ ] register 请求带唯一 req id(uuid4,可追踪)
- [ ] exists 空 body 更新不得用 NULL 覆写已有值(P2-2)
- [ ] Bearer 契约:board API 用 Bearer+email 双凭证;mail API 用 X-AIMail-Api-Key+X-AIMail-Signature(HMAC)——两套鉴权不要混(P2-5 实证网关源码后修正)
- [ ] MCP 坏帧容错:-32700 回应后 server 存活继续服务(P2-3)
- [ ] port 扫描走 resolver 不硬编码(P2-8)

## 4. 上线检测基准(L0→L3)

| 级别 | 内容 | 命令 |
|---|---|---|
| L0 | 单元+契约(python 47 + vitest 141 + 5 包 tsc) | `pytest tests/` + `pnpm test` |
| L1 | 版本单线制(tag==PyPI==TS,rc 格式两态)+ 依赖序 | `tests/release-gates/check-versions.sh` |
| L2 | 注册表 schema(全平台 install/register/uninstall/health 完整性) | gate-tests.sh |
| L3 | 本机真实宿主 check(check_status 全探针) | `aimail check --system-id ...` |
| L2.5 | 宿主实装回归(装 rc 包→reset 双路径→register_all spawn→幂等) | 发布 rc 后手工跑,L2.5 清单见 tests/release-gates/README.md |
| docker | 纯净环境安装链(镜像内 install→注册→绑定→幂等) | tests/docker-regression/hosts/(dsh/pi 已 4/4+4/4) |

**发布纪律**:
- [ ] rc 先行→宿主 L2.5→stable;workflow already-published skip 意味着 tag 不可重发,修复必须 bump 新版本(rc.1 重打 tag 教训)
- [ ] PyPI 包名 = `aimailsdk`(不是 aimail;轮询/脚本曾用错名空转)
- [ ] 版本单线制:TS=`X.Y.Z-rc.N` / PyPI=`X.Y.ZrcN` / tag=`vX.Y.Z-rc.N`
- [ ] docker 镜像源:官方 Hub(nousresearch/hermes-agent 在 Hub 不在 ghcr);受 IPv6 断网环境可用 mirror retag(dockerproxy.net 实证)

## 5. 新平台适配 checklist(浓缩版,逐项打勾)

1. [ ] 语言归属确认(TS 包 or pysdk 模块;无第二种选择)
2. [ ] platforms.json 注册表条目(kind/install_steps/register_all/uninstall_steps/health_checks)
3. [ ] agent 枚举(session 模型:多 agent 一等 or 单 agent 退化)
4. [ ] 地址派生(email_for_agent 同源;目录名清洗同源)
5. [ ] tools 注册(语义来自 mail-core tool-registry;parity 测试过)
6. [ ] skills 释放(平台 skills 目录;SKILL.md 6 步协议)
7. [ ] 本地入站端点(listener 跟宿主进程生命周期;端口 resolver)
8. [ ] 指针落盘 + agentmail.json 0600
9. [ ] register-cli 随包(TS);ensure-system 反调自举
10. [ ] check_status 探针项(skills/指针/webhook/hook)
11. [ ] docker 纯净安装回归(镜像内四步)
12. [ ] L0→L2.5 全绿再发 stable

## 6. 历史事故速查(每条都是必检点的出处)

| 事故 | 根因 | 沉淀 |
|---|---|---|
| deer-flow 注册 pending 死循环 | activation_code 未保存 | P1-1;pending 是正常态,链必须闭环 |
| hermes 批量装 webhook 串接 | HERMES_PROFILE_DIR 不随循环变量 | P2-1;循环体内 set/finally 清理 |
| 12→13 工具注释漂移 | 复制粘贴多副本 | 注释同源;TS 包 skills/board 资源释放统一 |
| openclaw CLI 命令三层断 | registerCommand≠CLI 分发 | api.registerCli + variadic positional;L2.5 才能发现 |
| skills 释放断链 | CLI 拆分删脚本无人接管 | 释放责任归插件;check 探针覆盖 |
| AIMAIL_HOME 五处副本 | Path('') 恒真 bug 史 | aimail_home() 单一真源;一致性来自构造 |
| dsh-llm ERESOLVE | peer-deps 冲突 | npm --legacy-peer-deps(docker 构建/宿主安装同) |
| 容器 npm EAI_AGAIN | daemon dns=127.0.0.1 不可达 | docker build --network host |
