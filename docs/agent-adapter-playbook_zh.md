# Agent 适配层适配点清单(SDK 侧经验总结)

[English](agent-adapter-playbook.md) | 🇨🇳 中文

> 定位:SDK 适配层的**唯一维护文档**——单个 agent 宿主在两个同构 SDK 包里
> 逐点要做哪些适配、每个点的已知实现与验证口径。(CLI/SDK 边界内容在
> `cli/contract-boundary.md`;CLI 契约与配置字段表在 `cli/README.md`。)
> 路径均相对仓根 `/aimail`,行号为 2026-10-23 代码状态。

## 0. 边界铁律(适配层做什么 / 不做什么)

1. **平台知识只活在宿主模块**(TS `tssdk/packages/agent-aimail/src/{host}/`,
   py `pysdk/{host}/`);core / mail / identity / board / skill / pull 宿主无关,
   **永不复制**(见 `src/hosts/registry.ts` 头注释、§1 同构纪律)。
2. **core 不碰文件系统**:core 只把 agent 身份(`AgentConfig` / `config` dict)
   传给适配层注入的 provider;SOUL/skills 的文件解析全在适配层
   (`src/core/preprocess.ts:383-415`、`pysdk/aimail_base.py:350-376`)。
3. **入站单入口**:所有宿主、所有入站路径都汇入同一个
   `processInboundMail` / `process_inbound_mail`(HMAC 验签 → 预处理 →
   ping/pong 拦截 → 投递)。适配层只负责"把请求接到这个函数上"。
4. **工具单一真源**:`MAIL_TOOLS`(`src/core/tool-registry.ts:66`,14 个工具)。
   适配层只做"registry 条目 → 宿主工具格式"的翻译,不各自维护工具表。
5. **契约常量单一真源**:仓根 `contract/aimail-contract.json` →
   `src/core/contract.ts`(硬编码 + L0 门禁断言相等)→
   `pysdk/aimail_contract.py`。适配层引用常量,不写字面量;
   新字面量须进常量模块或标 `contract-allowed`(L0 棘轮:
   `agentmail.json` 字面量计数只降不升)。
6. **CLI 零平台协议**:CLI 只读宿主注册表跑通用执行器;加平台不许碰
   `cli/src/hosts/mod.rs` 与 `cli/src/hosts/*.rs` 数据文件(L0 门禁对那里
   任何平台字面量都会红)。边界定稿见 `cli/contract-boundary.md`。

## 1. 两个 SDK 包的同构架构

### 1.1 两个包

新 agent 的适配只落在两个同构的 SDK 包里(按宿主语言选边):

| | TS 包 | Python 包 |
|---|---|---|
| 发行 | npm `agent-aimail`(`tssdk/packages/agent-aimail/`) | PyPI `agent-aimail`(`pysdk/`,wheel 内包名 `aimail/`) |
| 现有宿主 | openclaw / dsh / pi | hermes / deer-flow |
| 入口 | `dist/{host}/index.js`(宿主 loader 消费)+ `dist/{host}/register-cli.js`(CLI spawn) | per-host 适配模块(注册) + `pysdk/sdk_ops.py`(进程命令契约) |
| 宿主 loader | npm 扩展机制(plugin entry / cordis bundle / pi.extensions) | 进程内 import / 宿主源码 patch(见 P4/P5) |

两个包同名、分注册表(npm 与 PyPI),互不依赖。宿主语言定边:
宿主自身是 TS/JS 运行时 ⇒ TS 包;Python 运行时 ⇒ Python 包。
一个宿主只出现在一个包里,不混装。

### 1.2 分层同构对照

| 层 | TS 包 | Python 包 |
|---|---|---|
| 宿主注册表 | `src/hosts/registry.ts`(`HostSpec`: sdkSpecs/configDir/entry/registerCli/peer) | `pysdk/hosts/registry.py`(`HostSpec`: home_dir/marker/adapter_dir/register_module/register_fn/install_fn) |
| 宿主识别/自检 | `src/hosts/detect.ts`(多信号)+ `activate.ts`(`activateHost` 断言) | `pysdk/hosts/{detect,activate}.py`(tssdk 三件套移植) |
| 共享 core | `src/core/`(preprocess / contract / tool-registry / skill-roots / ensure-binding / release-resources) | `pysdk/aimail_base.py`(注入点组)+ `aimail_tools.py` + `aimail_board.py` + `_resources_release.py` |
| 宿主适配层 | `src/{host}/`(`index` / `identity` / `inbound` / `tools` / `skills`) | `pysdk/{host}/`(`aimail_{host}.py` + patch/ensure 族) |
| 契约常量 | `src/core/contract.ts`(硬编码 + L0 断言 == 清单) | `pysdk/aimail_contract.py`(同) |
| 资源释放 | `src/core/release-resources.ts`(prepack 暂存进包) | wheel force-include `resources/` → `aimail/resources/` |

**同构纪律**:两个包的 core 实现同一套契约(`contract/aimail-contract.json`
单一真源,L0 门禁双向断言相等);注入点清单两侧一一对应(§2 P9);
工具语义单一真源 `MAIL_TOOLS`,两侧各自翻译为宿主格式。
适配层只做"宿主翻译 + 注入点赋值 + 注册"三件事,永不复制 core。

### 1.3 新宿主加在哪(决策树)

1. **宿主语言**:TS/JS 运行时 → TS 包;Python 运行时 → Python 包。
2. **包内**(§4 checklist 展开):注册表加条目 → 新建 `{host}/` 适配目录 →
   provider 注入 → 入站接线 → 工具翻译 → 资源释放 → 注册入口。
3. **CLI 侧(配套,非 SDK 适配层)**:CLI 的宿主表是声明式注册
   (`cli/src/hosts/{name}.rs` + `ALL` 注册一行;六面 = identity/runtime/
   exec/health/pointer/目录,引擎不动——`cli/src/hosts/mod.rs`)。
   CLI 零平台协议(L0 门禁强制),只做"读注册表跑执行器";
   但它驱动 install/check/repair,新宿主必须同步登记,否则 CLI 面失明。

### 1.4 包内目录速览(TS 包,py 包同构)

```
tssdk/packages/agent-aimail/
├── src/
│   ├── core/        # 宿主无关: preprocess/contract/tools/skills/ensure-binding
│   ├── hosts/       # 注册表 registry + 检测 detect + 断言 activate
│   ├── openclaw/    # 适配层: index/identity/inbound/tools/skills
│   ├── dsh/         # 适配层: index/mail-service/inbound/tools/skills
│   └── pi/          # 适配层: index/identity/inbound/tools
├── resources/skills/  # prepack 暂存(真源 = 仓根 resources/)
└── test/            # 每宿主沙箱单测
pysdk/
├── aimail_base.py   # core: 注入点组 + process_inbound_mail
├── aimail_tools.py  # core: 14 工具实现
├── hosts/           # registry + detect + activate(移植)
├── hermes/          # 适配层: aimail_hermes + patch/ensure 族
└── deer-flow/       # 适配层: aimail_deerflow + manage + inbound
```

## 2. 适配点逐条(10 点)

### P1 宿主声明与入口注册

- **职责**:声明"本包支持哪些宿主、每个宿主怎么被加载",以及产物入口子路径。
- **契约**:TS 侧 `src/hosts/registry.ts` 的 `HostSpec`
  (`host / label / sdkSpecs / configDir / entry / registerCli / peer`),
  每宿主一条;py 侧 `pysdk/hosts/registry.py` 同构。
- **已知方法**:
  - openclaw:`openclaw.plugin.json` + package.json `openclaw.extensions` →
    `dist/openclaw/index.js`(plugin entry)。
  - dsh:`cordis.patch.yml` 自挂载 bundle,3 个子路径
    (`dsh-aimail/mail-service` → `ctx.mail`;`/tools`;`/inbound` →
    node:http 端点),见 `src/dsh/index.ts:1-14`。
  - pi:package.json `pi.extensions` → `dist/pi/index.js`。
  - hermes:不走 npm 扩展面,SDK 以 **patch 宿主源码** 形态接入
    (`pysdk/hermes/patch_*.py`,见 P4/P5)。
- **验证**:`detectHost()`(P2)+ 入口 `activateHost()` 断言通过;
  产物 `dist/{host}/index.js` 存在。

### P2 宿主识别与自检

- **职责**:防止"包装错宿主"的半失败;提供诊断。
- **契约**:`src/hosts/detect.ts` — 多信号、惰性、永不抛异常。
  信号 1(强):宿主 SDK 可 resolve(`sdkSpecs`,
  如 `openclaw/plugin-sdk/plugin-entry`、`@deepseek-ai/cordis`);
  信号 2(弱):`~/{configDir}` 存在。
- **已知方法**:每个宿主入口在 register 时调
  `activateHost('<host>')` 断言"加载我的宿主 == 我声称的宿主"
  (如 `src/openclaw/index.ts:41`)。
- **验证**:在宿主进程内 `detectHost()` 返回正确宿主键;
  裸 node/python 进程返回 null/None 不抛。

### P3 身份解析(绑定 + 自动绑定 + 出站标识)

- **职责**:从宿主环境解析出 `{system_id, email}` → `AgentConfig`;
  出站带 `X-AIMail-Agent: platform/ver` 头。
- **契约**:指针文件 `POINTER_FILE`(`.agentmail`)+ 绑定文件
  `BINDING_FILE`(`systems/{sid}/{cleaned_addr}/agentmail.json`)
  (`src/core/contract.ts`)。**指针是包级身份唯一来源**,不跨系统扫描、
  无 env 覆盖(见 `src/openclaw/identity.ts:1-13`、`src/pi/identity.ts:1-14`)。
- **已知方法**:
  - openclaw:factory `ctx.agentId` → `~/.openclaw/.agentmail` →
    mail-core `loadConfigByAgentId`;指针缺失但机器有系统配置时
    **auto-bind**(mail-core `ensureAgentBinding`,三宿主单真源,
    2026-10-16 批)。
  - pi:`~/.pi/.agentmail` 同链;入口在入站监听拉起**前**调
    `ensureAgentBinding`("端口起 = 绑定已写",见 `src/pi/index.ts:135-147`)。
  - dsh:身份取 `exec.agent.id`。
  - hermes:profile 即身份(`_resolve_profile_dir()`,见 P6)。
  - deer-flow:`systems/{sid}/{cleaned_addr}/` 目录即 agent
    (`pysdk/deer-flow/aimail_deerflow.py:116-134`)。
- **agent 级绑定前缀**:agent 级对话安装(地址码)产生的绑定落在
  **`shared_addr_`** 前缀的系统下(网关为地址码自动创建的系统;
  实现事实源 `AGENT_SCOPE_SYSTEM_PREFIX`,
  `src/core/poll-entry.ts:36`);系统级绑定用系统自身 id。
  适配层身份解析按 system_id 前缀区分两类绑定,不混用。
- **出站标识**:`setAgentIdentity()` / `_AGENT_IDENTITY_OVERRIDE`,
  版本取自**实测**的宿主安装(向上走已装包),不猜。
- **验证**:绑定文件字段与宿主环境一致;`X-AIMail-Agent` 头
  `platform/ver` 与宿主实际版本一致。

### P4 入站接线

- **职责**:把宿主入站流量接到单入口函数;挂 ping/pong 健康探测。
- **契约**:路径 `INBOUND_PATH = /aimail/inbound`(全平台不可变);
  hermes 唯一例外 `HERMES_INBOUND_PATH = /webhooks/aimail-inbound`
  (挂在 hermes 网关自身 webhook 路由,route 名 `aimail-inbound`)。
  端口 `INBOUND_PORTS = { dsh: 9099, pi: 9101, deerflow: 8001 }`
  — **端口可配,路径不可变**(`src/core/contract.ts`)。
- **已知方法**:
  - pi:extension 内 node:http handler → `verifySignature`(HMAC,
    per-address `webhook_secret`)→ `routeAddressFromHeaders` →
    `processInboundMail`(`src/pi/index.ts:66-114`)。
  - dsh:bundle 子路径 `dsh-aimail/inbound`(node:http 端点 + 投递)。
  - openclaw:plugin `register(api)` 内注册 HTTP 路由
    (`src/openclaw/index.ts:38`,inbound 逻辑在 `src/openclaw/inbound.ts`)。
  - hermes:patch hermes-agent 发行版内 `gateway/platforms/webhook.py`
    (外部文件,不在本仓)加 `PREPROCESS_REGISTRY` +
    `register_preprocessor()`,webhook handler 在 prompt 渲染前调用
    (`pysdk/hermes/patch_webhook.py`);同时 `ensure_config.py` 幂等确保
    profile `platform_toolsets.webhook`/`cli` 含 `agentmail`
    (缺任一项即"收得到回不出"断链)。
  - deer-flow:本地 gateway 进程内 `POST /aimail/inbound` →
    `process_inbound_mail` → ping/pong 拦截(200 吞掉不触发 agent)→
    `start_run`(thread = `uuid5("aimail", email)`,会话按地址稳定)→
    立即 200(`pysdk/deer-flow/aimail_inbound.py:1-20`)。
- **验证**:网关原件 → bodyproc → 本地入口全链实测,ping/pong
  契约成立;入站日志落 `~/.aimail/systems/{sid}/{cleaned_addr}/agentmail.log`。

### P5 工具注册

- **职责**:把 `MAIL_TOOLS`(14 个:send_mail, manage_contacts,
  contact_profile, set_contact_profile, email_summary,
  set_email_summary, search_mail, board_status, board_task_list,
  board_task_show, board_heartbeat, board_members, board_roles,
  activate_address_code — 单真源 `src/core/tool-registry.ts:66`)
  翻译成宿主工具格式并注册。
- **已知方法**:
  - openclaw:`definePluginEntry` factory 形式迭代 `MAIL_TOOLS`
    (`src/openclaw/index.ts:38` 头注释、`src/openclaw/tools.ts`)。
  - dsh:`for (const tool of MAIL_TOOLS)` → dsh `defineTool`
    (`src/dsh/tools.ts:89-94`)。
  - pi:同迭代 `MAIL_TOOLS`(`src/pi/tools.ts`)。
  - hermes:patch `hermes-agent/toolsets.py` 把 7 个 mail 工具名注册进
    `_HERMES_CORE_TOOLS`;`who patches, who unpatches`(精确文本剥离,
    `pysdk/hermes/toolsets.py:57-63`)。
- **验证**:宿主内工具可见且参数 schema 与 registry 一致;
  注释里的工具**数量**会随增删漂移(如 `src/dsh/index.ts:6` 仍写
  "15",whoami 移除后实为 14)——数量只认 registry,注释不写死。

### P6 SOUL per-agent 解析

- **职责**:board 上下文模板 `{{SOUL_MD_CONTENT}}` 的填充源。
  适配层注入 `_SOUL_PROVIDER`,core 在 `build_ctx` 里调用
  (`src/core/preprocess.ts:378`、`pysdk/aimail_base.py:230`)。
- **契约**:签名 TS `(cfg: AgentConfig) => string`、
  py `(config: Optional[dict]) -> str`;**未注入 ⇒ 空串**(谁有 SOUL
  谁填,core 永不摸 FS);**缺失 ⇒ ''**(模板渲染空,文档化)。
- **已知锚点**(按宿主布局,多 agent 平台按 agent 身份解析):
  - openclaw:`{resolveAgentWorkspaceDir(agentId)}/SOUL.md`,
    `agentId = cfg.agent_id ?? 'main'`(`src/openclaw/index.ts:78-87`)。
  - dsh:`~/.dsh/profiles/{DSH_PROFILE}/SOUL.md`
    (env `DSH_HOME`/`DSH_PROFILE`,默认 `~/.dsh`/`web`,
    `src/dsh/inbound.ts:329-336`)。
  - pi:`~/.pi/agent/SOUL.md`(`~/.pi/agent/` 即 per-agent 目录,
    `src/pi/index.ts:184-193`)。
  - hermes:`_resolve_profile_dir()/SOUL.md`,锚点链
    `HERMES_PROFILE_DIR` env → live `HERMES_HOME`
    (contextvar `get_hermes_home()`,gateway spawner 按 profile 设置)
    → `~/.hermes`(`pysdk/hermes/aimail_hermes.py:103-160`)。
  - deer-flow:`systems/{sid}/{cleaned_addr}/SOUL.md`(目录即 agent,
    `pysdk/deer-flow/aimail_deerflow.py:116-121`)。
- **踩坑(2026-10-23 修)**:hermes 旧锚点只认 `HERMES_PROFILE_DIR`
  (hermes 运行时**不设**该 env)→ 回退读到通用 `~/.hermes/SOUL.md`
  (513B)而非 per-profile SOUL(907B)。真 per-agent 锚点是
  `HERMES_HOME`。
- **验证**:读出的 SOUL 与"该 agent 身份"对应文件逐字节一致
  (size + 首行);未配置 SOUL 的宿主返回 ''。

### P7 skills 枚举

- **职责**:board 上下文模板 `{{SKILLS_LIST}}` 的填充源。
  适配层注入 `_SKILLS_PROVIDER`,core 在 `build_ctx` 里
  `", ".join(...)`(`src/core/preprocess.ts:379`、
  `pysdk/aimail_base.py:231`)。
- **契约**:签名 TS `(cfg: AgentConfig) => string[]`、
  py `(config: Optional[dict]) -> list[str]`;**未注入 ⇒ []**。
- **裁决(2026-10-23,owner)**:上报标准 = "**该 agent 配置可用的
  skills list**";共享目录的 skills 计入,不因"目录是共享的"而返回空。
- **发现粒度**(共享枚举器 `src/core/skill-roots.ts`
  `listSkillNames` / `combineSkillNames`):
  顶层子目录含 `SKILL.md` 或裸 `.md`;name = frontmatter `name:` ??
  目录基名;`fs.statSync` **跟 symlink**(`readdir` 的
  `withFileTypes` 的 `isDirectory()` 不跟,plugin-skills 的符号链接
  会全漏)。
- **已知方法**(各宿主镜像 host 运行时自己的发现链):
  - openclaw:镜像 `resolveWorkspaceSkillSourcePlan` 的 roots 顺序
    extraDirs → bundled(+custodian 仅 owner)→ workshop → managed →
    personal(仅默认 state)→ workspace(`.agents/skills` + `skills`)→
    plugin-skills;bundled 经
    `createRequire(...).resolve('openclaw/plugin-sdk/plugin-entry')`
    锚定后向上找 `skills/`(`src/openclaw/skills.ts`)。
  - dsh:镜像 `FileSystemSkillProvider.roots`:
    project(cwd 上溯 `.git` 的 `.dsh/skills` + `.agents/skills`)→
    user(`$DSH_HOME/skills` + `$DSH_AGENTS_HOME/skills`)→
    bundled(`$DSH_BUNDLED_SKILL_DIR`)(`src/dsh/skills.ts`)。
  - pi:skills 天然 per-agent,单根 `~/.pi/agent/skills/`
    (`src/pi/index.ts:196-200`)。
  - hermes:**不复刻** walk+过滤,直接 import host 自身权威入口
    `tools/skills_tool`(`skills_list` 工具底层 `_find_all_skills`,
    live profile-scoped `HERMES_HOME` + external_dirs +
    platform/env/disabled 门 + 去重,含 plugin skills);
    import 失败(非 hermes 进程)兜底 `[]`
    (`pysdk/hermes/aimail_hermes.py:121-158`)。
- **踩坑(2026-10-23 修)**:hermes 旧实现读 `config.yaml → skills:`
  字段——那是 skills **子系统配置块**(dict:external_dirs /
  template_vars / inline_shell…),不是名字列表;`", ".join(dict)`
  把键名拼成垃圾串填进 SKILLS_LIST。
- **验证口径**:与宿主自己可见的列表对拍。本机实测(2026-10-23):
  openclaw 86、dsh 32、pi 0、hermes(agentmail profile)147
  (147 < 裸 walk 151,差 4 个被 host 的 platform/env/disabled 门
  正确过滤——口径正是"agent 实际可见")。

### P8 资源释放(board / skill,幂等)

- **职责**:把 SDK 自带的资源释放到宿主可读位置,覆盖"只装 TS 包、
  不装 Python SDK/CLI"的机器。
- **已知方法**(`src/core/release-resources.ts` +
  `packageResourcesRoot()`):
  - board 资源 → `~/.aimail/systems/*/board/`(幂等,**永不覆盖**
    用户个性化文件;`releaseAllSystems`)。
  - agentmail skill 资源 → 宿主 skills 目录 `agentmail/`
    (幂等,内容相同跳过):
    openclaw `~/.openclaw/skills/agentmail/`、
    dsh `<dshHome>/skills/agentmail/`、
    pi `~/.pi/agent/skills/agentmail/`、
    hermes `_release_hermes_skills`(`pysdk/install.py:265`,安装链调用)。
- **验证**:二次安装无 diff(幂等);用户改过的文件不被覆盖。

### P9 注入点赋值(适配层与 core 的接口面)

- **职责**:import 共享 core 后,把宿主实现赋给 core 的注入点
  (两侧同构;TS 经 `setSoulProvider` 等函数,py 经模块级赋值):
  `_CONFIG_LOADER`(agent 配置加载 `/ () -> Optional[dict]`)、
  `_PROFILE_DIR_RESOLVER`(agent 目录 `/ () -> Optional[str]`)、
  `_PERSONAS_PROVIDER`(personas 配置)、`_SOUL_PROVIDER`、
  `_SKILLS_PROVIDER`、`_BOARD_GATEWAY_SINK`(board 网关注册回调)、
  `PERSONA_SUPPORTED`(能力开关,False → 归一化基址)、
  `_AGENT_IDENTITY_OVERRIDE`(出站身份头)、
  `_PERSONA_NAME_PROVIDER`(当前 persona 名)。
- **已知方法**:
  - TS:`setSoulProvider` / `setSkillsProvider`(
    `src/core/preprocess.ts:392-401`)+ 入口内其余赋值
    (如 `src/openclaw/index.ts:38-111`)。
  - py:模块级直接赋值,hermes 一屏可见
    (`pysdk/hermes/aimail_hermes.py:876-889`);
    deer-flow(`pysdk/deer-flow/aimail_deerflow.py:199-200`)。
- **纪律**:core 侧注入点默认值必须是**最坏情况持平**的
  (None → 空串/空列表),未注入不得硬断。

### P10 agent 级 pull

- **职责**:agent 级拉取(与系统级 push 对偶:
  **pull = agent 级,push = 系统级**)。
- **已知方法**:决策在 mail-core `startAgentPullEntries`
  (宿主无关单真源),宿主入口只调用
  (如 `src/pi/index.ts:147`、`src/dsh/inbound.ts`)。
  适配层不实现拉取逻辑。

## 3. 各宿主速查表(2026-10-23 本机状态)

| 适配点 | openclaw (TS) | dsh (TS) | pi (TS) | hermes (py) | deer-flow (py) |
|---|---|---|---|---|---|
| P1 入口 | plugin entry + extensions | cordis.patch.yml 自挂载 3 子路径 | pi.extensions | patch 宿主源码族 | 本地 gateway 进程 |
| P2 自检 | activateHost('openclaw')@register | activateHost('dsh')@mail-service 加载 | activateHost('pi')@入口 | 进程即宿主 | 进程即宿主 |
| P3 身份 | `~/.openclaw/.agentmail` + auto-bind | exec.agent.id | `~/.pi/.agentmail` + auto-bind | profile 即身份 | `systems/{sid}/{cleaned_addr}/` 目录即 agent |
| P4 入站 | plugin HTTP 路由 | bundle `/inbound`(node:http) | extension node:http(:9101) | 网关 webhook `/webhooks/aimail-inbound` | 本地 gateway POST :8001 |
| P5 工具 | 迭代 MAIL_TOOLS(factory) | 迭代 MAIL_TOOLS(defineTool) | 迭代 MAIL_TOOLS | patch toolsets.py(7 mail 工具入 _HERMES_CORE_TOOLS) | core 工具函数直接暴露 |
| P6 SOUL | `{workspaceDir(agentId)}/SOUL.md` | `~/.dsh/profiles/{DSH_PROFILE}/SOUL.md` | `~/.pi/agent/SOUL.md` | `{HERMES_HOME}/SOUL.md`(env→live→~/.hermes) | `systems/{sid}/{cleaned}/SOUL.md` |
| P7 skills | 镜像 resolveWorkspaceSkillSourcePlan(本机 86) | 镜像 FileSystemSkillProvider.roots(本机 32) | `~/.pi/agent/skills/`(本机 0) | import host `tools/skills_tool`(本机 147) | `systems/{sid}/{cleaned}/skills/` |
| P8 资源 | `~/.openclaw/skills/agentmail/` | `<dshHome>/skills/agentmail/` | `~/.pi/agent/skills/agentmail/` | `_release_hermes_skills` | — |
| P9 注入 | index.ts register 内 | inbound.ts 启动内 | index.ts 内 | aimail_hermes.py:876-889 | aimail_deerflow.py:199-200 |
| P10 pull | startAgentPullEntries | 同 | 同 | —(hermes 走 webhook push) | — |

## 4. 新宿主接入 checklist

**TS 宿主**(五步):
1. `src/hosts/registry.ts` 加一条 `HostSpec`(sdkSpecs/configDir/
   entry/registerCli/peer)。
2. 新建 `src/{host}/` 目录:`index.ts`(activateHost → 身份 →
   入站 → 工具 → 资源 → SOUL/skills provider)、`identity.ts`、
   `inbound.ts`、`tools.ts`;skills 若需多根,新增
   `{host}/skills.ts` 镜像宿主运行时发现链。
3. package.json 加 peer(安装时全可选)+ 宿主扩展 manifest。
4. 测试:`test/{host}/` 沙箱单测(临时目录造布局,不依赖本机状态)。
5. 验证:`npx tsc -b tsconfig.json` rc=0 + vitest 全绿 +
   L0 契约门禁 + **真实布局对拍**(宿主可见 skills 数逐项吻合)。

**py 宿主**:
1. `pysdk/hosts/registry.py` 加宿主条目。
2. 新建 `pysdk/{host}/`:`aimail_{host}.py`(provider 定义 +
   注入赋值,签名 `(config) -> str | list[str]`)、入站接线、
   工具注册;patch 宿主源码的(如 hermes)按
   "who patches, who unpatches" 提供对称 unpatch,并做
   **版本自适应**(自动探测宿主 commit 调整插入点)。
3. 幂等 ensure 配置(断链项逐条落 `ensure_config` 类模块)。
4. 验证:pytest 全绿 + 宿主进程环境实测(hermes = venv python +
   per-profile `HERMES_HOME`)。

## 5. 经验与裁决沉淀(带日期)

1. **读 host 自己的权威入口 > 复刻发现逻辑**(2026-10-23,hermes):
   SDK 内复刻 walk+过滤必漂移;能 import host 权威入口就 import
   (hermes `tools/skills_tool`),import 失败兜底 `[]`
   (与未注入的最坏情况持平,不新增硬断)。host 是源码树 + venv 部署时
   注意 import 只在宿主进程内成立——兜底正是为此。
2. **配置字段 ≠ 名字列表**(2026-10-23,hermes):`config.yaml` 的
   `skills:` 是子系统配置块(dict),不是 skill 名单;
   对"配置即清单"的假设要拿真实配置实锤再落码。
3. **per-agent 锚点 = 宿主真正按 agent 分目录的那个变量**(2026-10-23,
   hermes):hermes 运行时不设 `HERMES_PROFILE_DIR`,真锚点是按
   profile 设置的 live `HERMES_HOME`(contextvar)。锚点选错 =
   静默读别人的 SOUL。
4. **共享目录 ≠ 不报**(2026-10-23,owner 裁决):上报标准是
   "该 agent 配置可用的 list",共享 roots 计入。
5. **symlink 陷阱**(2026-10-23,openclaw):plugin-skills 是符号链接;
   `readdir withFileTypes` 的 `isDirectory()` 不跟链 → 用
   `fs.statSync`。
6. **工具数量只认 registry**:注释里的数量(如 dsh "15")会随
   工具增删漂移,whoami 移除后即陈旧;文档/注释不写死数字。
7. **验证必须真实布局对拍**:单测(沙箱)只证逻辑,口径对不对
   要拿宿主自己可见的列表逐项比(本机 86/32/0/147)。
8. **L0 契约棘轮**:新契约字面量进常量模块;`agentmail.json`
   字面量计数只降不升,判据可修错但禁改假绿。
9. **patch 宿主源码的纪律**(hermes):who patches, who unpatches;
   版本自适应(自动探测插入点);幂等(二次运行无 diff);
   安装链缺项逐条落 ensure 模块(2026-08-16 断链根因沉淀)。
10. **未注入 = 最坏情况持平**:所有注入点默认值(空串/空列表/None)
    保证"没适配的宿主"行为与适配前一致,不新增失败面。
