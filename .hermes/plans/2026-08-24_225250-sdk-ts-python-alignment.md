# aimail-sdk-ts 与 Python(AIMail)逻辑对齐方案

日期: 2026-08-24 22:52
参照基线: aimail 仓 2026-08-24 本地化改造后行为(HEAD 61e70bc)
对齐对象: dsh-aimail/packages/mail-core(@aimail/mail-core rc.10)+ packages/mail + 两个适配器 inbound

## 1. Goal

`@aimail/mail-core` 是 TS 侧与 Python `aimail_tools.py/aimail_base.py` 的 1:1 镜像
(文件头自述 "mirrors Python agentmail_tools.py" / "line-by-line contract baseline")。
2026-08-24 Python 侧完成三项改造后(meta/threads 本地化、outbox 先存再调、
X-AIMail-* 头改名 + thread-summary 端点删除),TS 侧仍停留在改造前逻辑,
且部分依赖的 gateway 端点已删除。本方案把 TS 逻辑对齐到 Python 现状。

## 2. 差异矩阵(调研结论, 2026-08-24 全文件对照)

### P0 — 功能性断裂(依赖的端点已删 / 数据模型已换)

| # | 位置 | 现状(TS) | Python 现状 |
|---|------|-----------|-------------|
| D1 | tools.ts L70-87 `loadMessageMeta/storeMessageMeta` | gateway agent_state `msg:{mid}` GET/PUT | 已本地化: `meta/{前2位}/{safe_mid}.json` 常写, 零 HTTP 往返; gateway 侧 `msg:` 预写块已删(Task 5) |
| D2 | tools.ts L388-404 `emailSummary/setEmailSummary` + gateway.ts L171-177 `threadSummaryPut/Get` | 调 `PUT/GET /api/v1/thread-summary/{mid}` | 端点已从 gateway 删除(Task 5, commit 2a1b818)→ **现在必 404**; Python 读 `threads/{前2位}/{tid}.json`(mid→thread_id 经本地 meta 解析), 空 summary=删文件, 2000 字符上限 |
| D3 | tools.ts sendMail L258-274 | 先调 API, 拿 `result.message_id||email_id` 回填 storeMessageMeta + bootstrap(沿用 8 月前的 email_id 回填错位 bug) | 先存再调: 本地生成 mid → 先写 meta(+outbox 快照按开关, 附件落 `attch/{mid}/`)→ 再调 API, 本地值即线上值, 失败不回滚 |

### P1 — 契约行为偏差(不报错但语义不一致)

| # | 位置 | 差异 |
|---|------|------|
| D4 | tools.ts L251 | 写端头 `X-Agentmail-Agent`(旧名); Python 已写 `X-AIMail-Agent`(gateway 白名单双名过渡保留中) |
| D5 | tools.ts L176/188/246 | `to` 数组 join `', '` 后按 ',' 再 split, 地址内逗号被破坏; Python 全程列表, 发送时 `",".join` |
| D6 | tools.ts L295-343 manageContacts | check 用 `r.in_contacts ?? false` —— gateway 实际返回 `{whitelisted, domain_addr, value, direction}`(http.rs L1362 已验证), TS 读错字段名 → check 恒 false; 另 `update` 的 direction 取值链有 bug(`args.direction ?? direction` 中 direction 已是默认 'all') |
| D7 | tools.ts L352-373 contactProfile | name 查询读 `r.data ?? r` —— gateway 返回 `{results: [...]}`(http.rs L2329 已验证)→ 读不到; 且缺 Python 的 ambiguous/candidates 多结果语义 |
| D8 | preprocess.ts L216-234 board 提取 | board_id 取自 body 正则 `board[-_]?id` —— Python 是 `sha256("{short}.a2a@{gw域名}")[:20]`(aimail_base.py _extract_board_gateway, 与 gateway derive_board_id 同算法, 防跨系统碰撞); TS 还会把错误 id 落 board_creds.json |
| D9 | preprocess.ts L187-201 sendPong | body 发 `''`; Python 发 `{"ping_id": "%s", "event": {"mail_id": "%s"}}` 且 pong 结果回写 pong_sent 日志(ok/error); TS 的 pong_status 恒 'sent' 与真实结果脱钩 |
| D10 | preprocess.ts L323 strip 字段 | 缺 Python 的 mail_id 缺失警告(收到 gateway RAW payload 而非预处理后 JSON 时); 其余字段列表一致 |
| D11 | preprocess.ts [WHOAMI]/board 段 | 只设 `_whoami_update_public`; Python 另注入 `_whoami_prompt`(role 文件 whoami.md 模板)与 `_role_prompt`(board_role 模板, 三级查找: 地址级→系统级→common.md)+ `{{KEY}}` 填充。dsh 契约是否要 role 模板 = 拍板项 |

### P2 — 环境/布局约定差异(跨仓文件布局, 需拍板)

| # | 位置 | 差异 |
|---|------|------|
| D12 | config.ts AIMAIL_HOME | TS `AIMAIL_HOME` env, 默认 `~/.agentmail`; Python 数据目录 env 是 `AGENTMAIL_HOME`(默认同值)。两套 env 名并存 |
| D13 | preprocess.ts L142 attch 路径 | TS `{home}/mail/{clean_addr}/{yyyymm}/attch/{mid}/`(按地址分目录); Python `{AGENTMAIL_HOME}/{yyyymm}/attch/{mid}/`(目录根=per-agent 数据目录, 由指针/env 解析, 地址段已体现在目录根) |
| D14 | preprocess.ts L226 board_creds | TS 写 `{home}/systems/{sid}/{addr}/board_creds.json`, 只存 `{token}`; Python 存 `{gateway_url, token}`, 且目录解析走 config。路径一致, 字段少一个 |
| D15 | preprocess.ts L27 logPath | TS `{home}/logs/agentmail.{clean}.log`(clean 用 cfg.email); Python `agentmail_log_path` 同布局但 email 解析走指针链(env→指针→default)。路径规则一致, email 来源不同 |

### 一致项(无需改, 已核对)

- PING/PONG_PREFIX 字面值与 Python/gateway P0 拦截一致 ✓
- GatewayClient request 语义(JSON 解析、status 不被 body 覆盖、数组包 data)✓
- sendMail payload 形状(to/markdown/sender/subject/cc/attachments/headers)✓
- 附件解析(大小 10MB、skip 目录、深度 5、50 上限、bare filename 搜索)✓ 搜索根略异(见 D13 关联)
- parse_amail_persona/base_email 三分法(含 short form)✓
- header 地址解析、direct_message/mentioned 判定逻辑 ✓
- board 工具 6 个(双凭证 token+member email query)与 aimail_board.py 对齐 ✓(board_id 派生除外, 见 D8)
- cleanAddr 规则 `[^\\w.-]→_` 与 Python `_clean_agent_dir_name` 一致 ✓
- HMAC 验签 body-only hex sha256(与 Python/openclaw 桥一致; ts 参数是死参数)✓

## 3. Task 分解(每 Task 独立 commit + 回归)

**Task 0 — Python env 改名 AGENTMAIL_HOME → AIMAIL_HOME(aimail 仓, Q1)**
- **纯字符串改名, 零行为变更**。涉及: tools/aimail_tools.py(_agentmail_dir L1012 + 2 处 docstring)、
  tools/aimail_base.py(agentmail_log_path L478 + docstring L474)、
  scripts/check_status.py、scripts/ping_test.py、scripts/send_welcome.py、scripts/agentmail。
- 兼容读: 保留 `os.environ.get("AIMAIL_HOME") or os.environ.get("AGENTMAIL_HOME")` 一行,
  防已部署 agent 的旧 env 断裂; 旧名待 Task 10 批删(与头改名同批清理)。
- 回归: python import OK + 现有 integration 测试(读日志/数据目录路径)全绿。
- **发现(单列, 不在本 Task 修)**: Python `AGENTMAIL_HOME` 自身语义不一致——
  `_agentmail_dir()` 当 env=叶子数据目录, `agentmail_log_path()` 当 env=根 home。
  默认(无 env)两边逐字节一致; 仅设 env 时暴露此矛盾。叶子/根归一化有迁移风险,
  待用户单独决策(见拍板项 Q4)。

**Task 1 — 本地 meta 层(mail-core 新增 meta.ts)**
- 新增 `meta.ts`: `saveLocalMeta(mid, refs, myAddr, direction)` / `readLocalMeta(mid)` /
  `resolveThreadId(mid)` / `localMetaPath` / `threadPath` — 1:1 移植 Python
  `_save_local_meta/_read_local_meta/_resolve_thread_id`, 路径 `{dataRoot}/meta/{前2位}/{safe}.json`,
  常写(不受快照开关控制), 原子写(tmp+rename)。
- dataRoot 解析: 对齐 Python `_agentmail_dir`(env 优先 → per-agent 目录), 见拍板项 Q1。
- tools.ts: `loadMessageMeta/storeMessageMeta` 改本地读写, 删 agent_state `msg:` 依赖;
  gateway.ts 删 `agentStateGet/Put` 若无其他调用方(setPublicWhoami 用 agentStatePut → 保留)。
- 新增单测: meta 分片路径、thread 解析、常写。

**Task 2 — outbox 先存再调(tools.ts sendMail)**
- 对齐 Python send_mail L512-532: mid 只生成一次 → 先 saveLocalMeta(outbound, my_amail_addr=sender)
  → 快照开关下写 out-{safe}.json + 附件复制 `{root}/{yyyymm}/attch/{safe}/` → 再调 API。
- 删 `result.message_id||email_id` 回填链(D3 根因); bootstrap 用本地 mid;
  失败不回滚(本地留"尝试过"记录)。
- 修 D5(to/cc 列表全程处理, 发送时 join)。
- 单测: 先存再调顺序、失败不删本地、mid 唯一。

**Task 3 — threads 本地化(emailSummary/setEmailSummary)**
- 对齐 Python: mid→thread_id 经本地 meta 解析, 读写 `threads/{前2位}/{tid}.json`;
  空 summary=删文件; 2000 上限 + error_code 语义; 删 gateway.ts threadSummaryPut/Get(D2)。
- 单测: 线程解析、空删、上限。

**Task 4 — 头改名 + 白名单/联系人字段修复**
- sendMail 写端 `X-AIMail-Agent`(D4); identity 缺省值语义保留(适配器注入)。
- manageContacts check 读 `whitelisted` 字段(D6); 修 update direction 取值。
- contactProfile name 查询读 `results` + ambiguous/candidates 语义(D7)。
- 单测: 三个工具对 mock gateway 响应的字段映射。

**Task 5 — inbound preprocess 对齐**
- board 提取改 sha256 派生(D8: 从 from 地址取 short + gw 域名, sha256[:20], 与 gateway
  derive_board_id 同算法); token 落盘补 `gateway_url` 字段(D14)。
- sendPong body 对齐(D9: ping_id+mail_id JSON) + pong 结果回写日志状态。
- RAW payload 守卫警告(D10)。
- [WHOAMI]/board role 模板注入(D11)——按拍板项 Q2。
- 单测: board_id 派生(固定向量)、pong body、RAW 守卫。

**Task 6 — 布局/环境对齐(按拍板项 Q1/Q3 执行)**
- D12/D13/D15: dataRoot/logs/attch 路径统一到一个 `mailHome()` 解析函数,
  与 Python `_agentmail_dir`/`agentmail_log_path` 同规则(env 名按拍板)。
- 同步 `@aimail/mail` resolve 层与适配器 inbound 受影响面(路由逻辑不变, 仅路径/字段)。

**Task 7 — 契约文档 + 回归**
- DSH-PREPROCESS-CONTRACT.md: "dsh 无快照" 差异说明更新为 "meta 常写对齐, 快照按开关"。
- mail-core/mail 全量 vitest + tsc 回归; 与 Python 侧交叉核对(工具名/参数 parity 测试已存在, 保持绿)。

## 4. 拍板项(2026-08-24 已拍板, 全部固化)

- **Q1 dataRoot/env 名** — **已拍板: 新名 = `AIMAIL_HOME`**。`AGENTMAIL_HOME` 是旧名,
  Python 没改 → **改 Python 侧补上**(aimail 仓), TS 侧已用 AIMAIL_HOME 零改动。
  落地 = 新增 **Task 0**(aimail 仓): AGENTMAIL_HOME → AIMAIL_HOME 机械改名。
  注: 默认(无 env)两边布局本就一致(`~/.agentmail/mail/{addr}/...`);仅 env 覆盖时
  语义不同(Python env=单 agent 数据目录, TS env=共享根)→ 记入风险, 不改语义, 仅统一名字。
- **Q2 dsh role 模板注入** — **已拍板: 要, 两边一致**。TS 移植 `_whoami_prompt`(whoami.md)
  + `_role_prompt`(board_role, 三级查找 地址级→系统级→common.md 兜底)+ `{{KEY}}` 填充。
  → 并入 **Task 5**。
- **Q3 适配器 inbound 路由** — **已拍板: 对齐 Python**。读 `X-AIMail-Email` 头路由,
  缺失回退 `to` 遍历(现 TS 仅遍历 to)。→ 并入 **Task 6**。
- **Q4 env 叶子/根归一化(新发现, 待拍板)** — Python `AGENTMAIL_HOME` 在
  `_agentmail_dir()`(叶子)与 `agentmail_log_path()`(根)语义矛盾。默认无 env 时两边一致,
  仅设 env 暴露。归一化到根(对齐 TS + log_path)需迁移已按叶子设 env 的部署。
  推荐: **Task 0 纯改名不动语义, 归一化单独决策**(避免迁移风险混入本次对齐)。

## 5. 验证

- mail-core: `vitest`(现有 preprocess/tool-registry/resolve 测试 + 新增单测)全绿
- tsc 无新增 error
- 交叉验证: 用同一 payload 跑 Python preprocess 与 TS preprocessInboundMail, 对比
  富化字段(my_amail_addr/direct_message/mentioned/attachments 路径结构)
- 工具名/描述 parity 测试保持绿(amail_mcp_server.py 契约)

## 6. 风险

- 布局对齐(Q1)影响已落盘的 dsh agent 数据目录 → 需要迁移或兼容读(旧路径回退),
  方案内处理: mailHome() 旧路径存在时兼容读, 新写走新路径。
- TS 仓有 parity 测试锚定 Python 工具注册表 → Task 4 改语义文本时须同步(测试会暴露)。
- 头改名: TS 写新名后, 旧 gateway(未升级)白名单仍含旧名不含新名 →
  生产部署协调与 aimail 仓 Task 10 同批(白名单双名过渡中, 新名已在白名单, 无窗口问题)。

## 7. 回滚

每 Task 独立 commit, `git revert <sha>` 单步回滚; 本地目录(meta/threads)纯新增, 无迁移风险。
