# AIMail SSE-V2 端到端改造 — L2/L3 测试交接文档(开发方 → 质检)

版本:`agent-aimail 0.5.1`(tssdk)/ `aimail-gateway v1.0.11`
交接日期:2026-11-23
仓:`metercai/aimail` @ `d415e72`(main)· 配套 `metercai/aimail-gateway` @ tag `v1.0.11`
用途:提交给质检做 sse-v2 改造 + 端到端 L2/L3 测试任务的质量评审

> 体例:所有结论带 commit SHA / run id / 可复跑命令。质检不必采信本文自述,
> 按 §7 复验命令逐项跑即可。

---

## 1. 一句话结论(如实)

- **sse-v2 交付面已证明绿色**:tssdk 已从 push 入站改造为 SSE 聚合拉取;
  L3 的 dsh cell 换成完整 V2 自助激活旅程(激活码 → 宿主绑定 → 校准审批闭环
  → whoami 签名)。基线 run 45041546371(sha `1affaa1`)**L3 全矩阵 9/9
  success**,含全部 4 平台 dsh cell;L2 双 profile(SSE 无 CLI 为主、legacy
  push 保留)门禁全绿。
- **当前 main(`d415e72`)的 L3 矩阵不是全绿**:18:10Z 起连续 3 个 run
  (45053595295 / 45057579223 / 45062883460)中 hermes×4、pi、dsh-windows
  cell 红,而 **dsh linux×2(sse-v2 交付核心)恒绿**。已证明该红**不是本次
  sse-v2 改动引入**(§5#1 证据链:代码净差=0 + 发布件窗口零漂移)。但红因
  **尚未定位** —— hermes 的 webhook 签名拒绝是确定性错误(12/12 cell 同
  签名)却只在 18:10Z 后出现,与 16:02Z 全绿并存,说明存在一个尚未查明的
  时间相关变量(候选:runner 镜像、pysdk webhook 协商、LLM 服务态),需专项
  排查(§9#1)。质检评审 sse-v2 交付时,以 `1affaa1` 的 9/9 为基线证据;
  矩阵当前红按 §5#1 签名复现即可,**勿判为 sse-v2 回归**,但也不要当作
  已知问题放过 —— 它是待排查项。

## 2. 版本与发布件(质检起点)

| 构件 | 版本 | 证据 |
|---|---|---|
| tssdk 包 `agent-aimail`(npm) | **0.5.1** | `tssdk/packages/agent-aimail/package.json`;commit `f77a577` "release: 0.5.1" |
| pysdk(独立仓,旧版) | 0.2.9(Python `agent-aimail`,PyPI) | PyPI 上传 2026-11-16,其后无新版;保留 push 入站,不在本仓范围 |
| gateway | **1.0.11** | tag `v1.0.11`(published 2026-11-22T07:52Z);4 平台资产 linux-amd64 / linux-arm64 / macos-arm64 / windows-amd64.exe(14.5-19.5MB) |
| CLI(published) | 0.1.65 | metercai/aimail release `cli-v0.1.65`(2026-11-17),其后无新版 |
| dsh(宿主 pin) | 0.2.2-rc.1 | `tests/agent-env/pins.env` `DSH_VERSION` |
| pi(宿主 pin) | 1.5.2 | 同上 `PI_VERSION` |
| hermes(宿主 pin) | 0.23.2 @ commit `9dbaa77c4c` | 同上 `HERMES_HOST_VERSION` / `HERMES_COMMIT` |

发布件一致性:L3 用 **release 二进制**(非开发构建)——`tests/l3/run-l3.sh`
① 段从 gateway release 资产下载。L2 real-host 用**开发构建**(L2 vs L3 的
owner 裁决:两条不同流程,见 `tests/host-acceptance/run-l2-dsh-real-host.sh`
头部注释)。

## 3. 改动清单(按时间线,全部 git 可查)

### 3.1 tssdk SSE-V2 改造主体(11-16 ~ 11-19,`metercai/aimail` tssdk/)

| 阶段 | commit | 内容 |
|---|---|---|
| SSE pilot(dsh 先行) | `3c42dcf` `c92947f` `c08680d` `045ad7d` `5dc9a09` `4044804` `491b215` `5bb8e4d` `8c9379a` `c34c959` | agent-aimail 0.2.13-0.2.18:SSE 入站会话绑定、按稳定 workspace 解析、fresh-host 默认 workspace |
| 工具面/共享流 | `f9cca13` `c65ed44` `7f96931` `41c2fbb` `cc23197`(0.3.2) | aimail_system 暴露;MAIL_TOOLS/BOARD/ADMIN 分组;no-CLI SSE 路径禁 CLI fallback;按 credential scope 共享单条 SSE 流 |
| 宿主切流 | `8e1ce41` `a591917` `65a4450` `757331c`(0.4.0) `e752140` `ceef57f` `00f656e` `b4e7120` `805ee0a` `3660e77` `76c0cb0` | host-neutral SSE runtime 契约与路由;Pi/OpenClaw in-agent 激活 + SSE 模式;默认 SSE(push 降 opt-in);按 credential scope 规划共享流;dsh mail hub 切共享 SSE planner;system-scoped 共享 SSE pull 原语 |
| 契约/修复 | `8527b58` `a57c1fe` `fa60db7` `00f10ca` `d474b8e` `43c208f` `506a838` `d9cf7e3` `8c04a65` `7b8a0f9` `460d51b` `d3ba914` `18f65cc` `0967656` | dsh 0.2.2-rc.1 兼容 + pull envelope 断言;禁启动自动绑定;workspace 绑定强制;openclaw 按地址绑 agentId、session key 前缀;Pi 单地址/OpenClaw session-key profile 契约固化;SSE 契约符合性审计;re-arm pull |
| V2 核心 | `74ae2ee` `badf320` `ad39e29` `849c3c9` `d897736` `b7d436c` `7d8f666` `0bf9d46` | AgentSystemId + address-only 工具面;地址 move / DSH unbind 生命周期;激活携带 AgentSystemId;聚合 transport 接入 tssdk;Pi/OpenClaw/DSH 聚合拉取;DSH 持久化 AgentSystemId + 聚合 L2 |
| 发布 | `51489fe`(0.5.0 notes)→ `f77a577`(**0.5.1**) | 0.5.1:QA findings B1-B4/F1/F3 关闭 + host-acceptance 门禁入 CI |

**push 入站的最终状态(口径,11-23 owner 裁定)**:
- **dsh 路径:无任何 HTTP 入站监听**(`src/dsh/index.ts` 头注:"No HTTP
  inbound listener / webhook / HMAC in the dsh path";`src/dsh/inbound.ts`
  不在 dsh bundle,src 内无引用)。
- **Pi/OpenClaw 路径:`transport: 'sse' | 'push'`,默认 `sse`;push 是遗留
  opt-in**(`src/pi/index.ts` `inboundPort` 注释 "legacy push mode only")。
- **tssdk 是一个域,不存在 pi/core 与 dsh/sse-v2 的域区分**(owner 11-23)。
- **pysdk 是独立仓,仍为旧版,保留 push 入站 —— 不在本仓范围。**

### 3.2 网关侧配套(`metercai/aimail-gateway`)

| commit | 内容 |
|---|---|
| `5b6369d` | SCHEMA 收敛:对齐 advanced 激活码结构,删除自造表 |
| `d5afefa` | **B5 修复**:V2 自助激活把 applicant 设为 default manager(V1 对齐,契约 7.1/8/10) |
| `d2af10a` | bump 1.0.11(SCHEMA 收敛发版,tag `v1.0.11` 含以上两者,已验证) |

B5 背景:QA 独立质检(`tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md` §4)
指出 V2 自助激活只建 system_domains、不写 manager、不建白名单(V1 parity
回归);`ac0d9c0` 在 findings 文档补了 B5 判定记录。

### 3.3 测试与 CI 体系(L2/L3 V2 化,11-22 ~ 11-23)

| commit | 状态 | 内容 |
|---|---|---|
| `f77a577`(含) | ✅ | host-acceptance 门禁入 CI:`run-l2-dsh-real-host.sh`(B4:真实 dsh 宿主工具面)、`harness.mjs`、`check-warm-deps.py`、`Dockerfile.dsh` + pnpm 预热、`llm-config.py` dsh guard + 单测 |
| `00defe8` | ✅ | 契约字面量棘轮:测试门禁处的契约值加 `contract-allowed` 标注(单真源纪律) |
| `b2bd057` | ✅ | ci:兄弟仓契约测试在仓缺席时 skip 不 fail |
| `ac0d9c0` | ✅ | docs(qa):B5 判定记录入 findings |
| **`1affaa1`** | ✅ **绿基线** | **L3 dsh cell V2 化**:`.github/workflows/l3.yml` 矩阵 13→9 cell(每平台 dsh 两入口合一,pi 保留);`tests/l3/run-l3.sh` dsh 早退接线(全依赖组 export + rc 透传,ENV GAP rc=2 也红)+ 删 ④/④b 死 dsh 分支;新增 `tests/l3/dsh-v2.sh`(自包含 [0]-[7] 旅程)。**L3 全矩阵 9/9 绿(run 45041546371)** |
| **`9a4cf40`** | ✅ | 契约门禁修复:dsh-v2.sh 的绑定文件名从 manifest 单真源派生(`contract/aimail-contract.json` `binding_file`),消除字面量棘轮违规 |
| `5db76e2` | ❌ **已回撤** | 夹具进程卫生(setsid 进程组清理 + 预扫 + 泄漏变红)。当时归因 CI 7/9 红于此,后被证伪(§5#2) |
| **`d415e72`** | ⚠ **当前 HEAD** | revert 5db76e2。`tests/l3/` 与 9a4cf40 基线逐字节一致(`git diff 9a4cf40..d415e72 -- tests/l3/` = 0 行)。**注意:其 L3 矩阵仍红,原因不在本仓改动(§5#1)** |

### 3.4 dsh-v2.sh 的 V2 旅程(run-l3.sh 的 dsh cell 实际执行体)

```
[0] fixture:SMTP sink + relay + domain offer + gateway(1.0.11 release 二进制)
[1] 外部申请人:邮箱验证 + apply(激活码从邮件正文提取)—— 无 admin key
[2] turn 1:干净 profile(无插件),LLM 真跑取证(不等关完;弱模型 240s+ 属正常)
[3] 用户 dsh plugin add + tool-mail 行断言(15 个邮件工具的唯一入口,B4 根因)
[4] host 重启(插件安装只在下次 boot 生效)
[5] turn 2:宿主 agent 自助 bind(ownership 行 + binding 0600 + SSE 上行)
[6] welcome 闭环:SKILL §3 校准 MUST —— 弱模型 bind 后不主动校准时,
    脚本以"用户 turn"身份经 web API 催(不代调 gateway API,D2 纪律);
    断言 manager 审批 → persona-applied 送达
[7] whoami 签名断言:agent_persona + agent_signature 生效(审批闭环终态)
```

## 4. 测试分层与触发面(质检入口)

| 层 | 内容 | 触发 | 判据 |
|---|---|---|---|
| **L0** | 契约单真源棘轮(`tests/contract/check-contract-single-source.py`)+ pytest `tests/sdk/` + vitest(tssdk 全量) | push → `ci` workflow `sdk-gates` job | 契约字面量白名单只减不增;pytest/vitest 全绿 |
| **L2 dsh SSE** | `tests/dsh-acceptance/scripts/run-dsh-l2-sse.sh` + base/advanced/rotation 等 10 脚本 | 手动 / 门禁 | 无 CLI、无 register-cli、无 listener/webhook;SSE pending 交付 |
| **L2 real-host** | `tests/host-acceptance/run-l2-dsh-real-host.sh`(B4 工具面)+ `harness.mjs` | CI `host-acceptance` | 开发构建插件装入 web 宿主 profile ⇒ 合成工具面含 mail 行 |
| **L3** | `.github/workflows/l3.yml` **9 cell**(linux-amd64/arm64、macos-arm64、windows-amd64 × hermes/dsh;pi 仅 linux 两平台) | `workflow_call`(publish 后)/ nightly / `workflow_dispatch`(可选 `cell=<platform>/<agent>`) | **release 二进制 + 真实 LLM**(xiaomi,OpenAI 路由);dsh cell 跑完整 V2 旅程 |

**L2 vs L3 口径(owner 裁决 11-21,勿混)**:
- L2 = 测试**前**把**开发构建**插件装入宿主 profile,测该开发构建;
- L3 = 从**干净态**(无插件)起步,agent 在测试**中**按激活提示自行安装
  **最新已发布版**。

**L3 dsh cell 关键机制**(防误读):
- 确定性:`DSH_PERMISSION_MODE=danger-full-access`(web session 默认
  `approval:ask` 会挂 UI 审批,回归 cell 必须确定性);
- LLM 直连真实端点,路由按 host 判定 —— **不经 proxy**(proxy host 会让
  llm-config.py 误选 Anthropic 路由);
- turn 1 语义 = 取证(不等会话关完,`llmMs>0` + progressed 即可,[4] 重启
  自然终止);bind 断言用 llmMs **增量**(防 turn 1 假绿);
- 校准兜底 = 用户 turn(§3.4 [6]);
- dsh V2 走 `provision.js` self-provisioning(无 CLI 闭环),只写
  per-address binding(`saveAgentConfig`),**不写** `aimail_gateway.json`
  (那是 CLI/pysdk 概念)—— 正确行为,非缺陷。

## 5. 已知问题与裁定记录(质检勿误判)

| # | 项 | 裁定 / 状态 |
|---|---|---|
| 1 | **当前 main 的 L3 矩阵红(时间相关,非 sse-v2 改动)** | 18:10Z 起 3 个 run(45053595295=7/9、45057579223=7/9、45062883460=3/9)中 hermes×4、pi、dsh-windows 红;**dsh linux×2 恒绿**。失败签名:① hermes(4 平台,3 run 共 12 cell 0 绿):宿主日志 `Route 'aimail-inbound' sent legacy body-only HMAC, rejected because it is replayable … require X-Webhook-Signature-V2` → 404 → "No reply within 300s";② pi:"extension did not bind a default agent in 120s (no SSE pull)";③ dsh-windows:"no LLM time ADDED (0→0)"(bind turn 无 LLM 时间,linux dsh 同 LLM 端点却绿)。对照:16:02Z 的 1affaa1 run 9/9 全绿。**排除证据**:a) `git diff 1affaa1..d415e72` 全树净差 = 仅 dsh-v2.sh binding_file 派生(11+3-),hermes/pi 路径代码逐字节相同;b) 发布件窗口零漂移 —— CLI 0.1.65(11-17 后无新版)、npm 0.5.1、PyPI 0.2.9(11-16 后无新版)、gateway 无 v1.0.12;c) 同代码 d415e72 两次 run 结果不同(7/9 vs 3/9,macos-dsh 一转绿)→ 非确定性代码回归。**已排除 sse-v2 改动与发布件漂移;具体触发尚未定位**(候选:GitHub runner 镜像滚动、pysdk 0.2.9 webhook 协商、LLM 服务态),待专项排查(§9#1),不属 sse-v2 交付范围。 |
| 2 | **5db76e2 setsid 归因更正(留痕)** | 11-23 曾提交 5db76e2(setsid 进程组清理修本地夹具泄漏),其 revert 消息(d415e72)归因"setsid 方案导致 7/9 红"。**该归因已被证伪**:回撤后的 d415e72(无 setsid,代码回到 9a4cf40 基线)矩阵**同样红**,失败 cell 完全相同(§5#1)——红与 setsid 无关,真实红因同 §5#1(尚未定位)。setsid 方案本身的跨平台缺陷(macOS/Windows runner 无 util-linux setsid)依然成立,故保持回撤。教训:本地夹具泄漏问题(§5#4)的修复必须先隔离"CI 矩阵红"这个独立问题,再谈夹具卫生。 |
| 3 | `listen.test.ts:122` 偶发红(`expected [p, p+2]` 类) | **不是 tssdk 产品竞态**(owner 11-23 裁定:归因 L3 测试夹具,非 tssdk)。SSE-V2 下 dsh 无入站监听;push 监听仅 legacy opt-in,`listenWithRetry` 的"双 starter ⇒ 不同端口、无 double-bind"契约本身成立。遗留观察项:测试对 loser 精确端口(p+1)的断言比文件头契约 #7 更严,并发下可落 p+2。**未动该文件**,不在本次范围。 |
| 4 | 本地 L3 夹具进程泄漏(开发机) | **真实存在,待方案**(§9#2):开发机观察到 smtp_sink 孤儿(PPID=1,持端口 12627 活 9h+)。CI runner 每 job 隔离,跨 cell 端口遮蔽不适用于 CI —— 修复域是**本地/开发机夹具卫生**,不应以牺牲 CI 跨平台稳定性为代价。已验证的局部事实:组 kill 机制(父+子同死)、泄漏断言逻辑(占端口⇒rc=1)单测均过。 |
| 5 | 本地 dsh 版本遮蔽(`~/.local/bin/dsh`=0.1.7 排在 nvm pinned 0.2.2-rc.1 前) | 本地回归必须 PATH 前置 pinned 二进制,否则全绿也可能是旧版跑的(假绿)。CI runner 无此遮蔽。 |
| 6 | 弱模型(mimo 级)bind 后不主动校准 | 非产品 bug(SKILL §3 MUST 由 agent 执行);dsh-v2.sh [6] 已按方案 D.④ 预留用户 turn 兜底(实测生效:CI dsh cell 日志 "user nudge → welcome queued → approval processed → persona-applied")。 |

## 6. CI 证据(截至本文定稿,全部可复查)

| run | sha | 时间(UTC) | 结果 | 备注 |
|---|---|---|---|---|
| `ci`(sdk-gates 等 5 job) | d415e72 | 11-22 18:52 | **success** | 契约棘轮/pytest/vitest 全绿 |
| `ci` | 5db76e2 / 9a4cf40 / 1affaa1 | — | success | 历史对照 |
| **L3 全矩阵 45041546371** | **1affaa1** | 11-22 16:02 | **9/9 success** | **绿基线**。linux-amd64/dsh 日志取证:ownership / binding 0600 / SSE up / welcome 审批闭环 / whoami 签名全 ✓;artifact `l3-linux-amd64-dsh-logs` |
| L3 全矩阵 45053595295 | 5db76e2 | 11-22 18:10 | 7/9 failure | §5#1/§5#2(已回撤) |
| L3 全矩阵 45057579223 | d415e72 | 11-22 18:53 | 7/9 failure | §5#1(无 setsid 同红 → 证伪 setsid 归因) |
| L3 全矩阵 45062883460 | d415e72 | 11-22 19:49 | 3/9 failure | dsh linux×2 + dsh macos 绿;hermes×4 + pi + dsh-windows 红。同代码结果再次不同 → 非确定性 |

本地:pinned dsh 0.2.2-rc.1 全旅程 rc=0 ×2(真实 xiaomi 端点,20 项断言
全过,llmMs 57-89s);契约门禁本地 PASS;组 kill/泄漏断言机制单测过。

## 7. 质检复验命令(自包含,按序)

```bash
# 0) 版本基线
cd <aimail>; git log -1 --oneline            # 期望 d415e72…(或其后)
grep -E "DSH_VERSION|PI_VERSION|HERMES_COMMIT" tests/agent-env/pins.env
#    期望 0.2.2-rc.1 / 1.5.2 / 9dbaa77c4c5b6391510832602f841bc95d390b22

# 1) L0(契约棘轮 + pytest + vitest)
python3 tests/contract/check-contract-single-source.py   # 期望 PASS
pytest tests/sdk/ -q && cd tssdk && pnpm -r test         # 期望全绿
#    (vitest 若 listen.test 偶红,重跑一次判 flake,见 §5#3)

# 2) sse-v2 交付面单 cell(dsh,~10-20min,真实 LLM)
gh workflow run l3.yml -f cell="linux-amd64/dsh"
#    判据:日志 "dsh V2 JOURNEY PASS"(含 [0]-[7] 全 ✓ 与 whoami 签名断言)

# 3) 全矩阵(9 cell 并行,~20-30min)
gh workflow run l3.yml
#    当前期望:见 §5#1 —— dsh linux×2 应绿;hermes/pi/dsh-windows 若红,
#    按 §5#1 签名核对(legacy HMAC 404 / no SSE pull / llmMs 0→0),
#    勿判为 sse-v2 回归。

# 4) 绿基线证据(无需重跑,直接看)
#    run 45041546371(sha 1affaa1)9/9 success + linux-amd64/dsh 日志取证
#    (artifact l3-linux-amd64-dsh-logs)

# 5) gateway 发布件核对
#    release v1.0.11 四资产与 L3 ① 段下载一致(run 日志 "fetching gateway")
```

## 8. 证据位置索引

| 证据 | 位置 |
|---|---|
| V2 QA 独立质检结论(B1-B5 判定链) | `tests/host-acceptance/HANDOFF-V2-QA-FINDINGS.md` |
| 上一轮发版 QA 交接(0.5.1 体例参照) | `docs/v2-qa-release-handoff.md` |
| release notes | `docs/v2-release-notes.md` |
| SSE 契约 | `docs/aimail-host-sse-contract-v2.md` |
| L2 双 profile 说明 | `tests/dsh-acceptance/L2-PROFILES.md` |
| B4 根因与门禁说明 | `tests/host-acceptance/run-l2-dsh-real-host.sh` 头部 |
| L3 旅程脚本 | `tests/l3/dsh-v2.sh`([0]-[7] 带行内注释) |
| L3 主 runner | `tests/l3/run-l3.sh`(dsh 早退块) |
| CI 矩阵 | `.github/workflows/l3.yml`(9 cell,dispatch cell 过滤) |
| 回归留痕 | commit `5db76e2` + `d415e72`(revert);CI run 45053595295 / 45057579223 / 45062883460 各 job 日志 |

## 9. 不在本次范围 / 待方案(冻结项,勿越界)

1. **L3 矩阵当前红的专项排查(§5#1)** —— 已排除 sse-v2 改动与发布件漂移,
   具体触发未定位;18:10Z 后 hermes 12/12 cell 同签名(webhook legacy HMAC
   被拒),16:02Z 时 4/4 绿 —— 存在时间相关变量。需单独排查:复现 hermes
   cell 单跑(`cell=linux-amd64/hermes`)、对比 runner 镜像版本、核对
   pysdk 0.2.9 webhook 签名路径与 gateway v1.0.11 的 legacy HMAC 协商。
   不属 sse-v2 交付,但阻塞矩阵全绿。
2. **本地 L3 夹具泄漏修复(§5#4)** —— 在 §9#1 隔离后再动;修法须本地域
   内(不碰 CI 跑的 run-l3.sh/dsh-v2.sh 的跨平台面),需单独方案 + 批准。
3. `listen.test.ts` 断言精确度(§5#3 遗留观察项)—— 未动。
4. pysdk(独立仓,旧版 push 保留)—— 未动。
5. hermes-multiplex-v2 / deerflow / pysdk-v2 三份 plan(`docs/`,规划态)
   —— 未实施。
6. gateway 产品行为 —— 本次仅 SCHEMA 收敛 + B5 修复(已随 v1.0.11 发版)。
