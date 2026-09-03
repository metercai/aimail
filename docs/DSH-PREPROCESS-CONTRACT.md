# DSH-PREPROCESS-CONTRACT — dsh TS 预处理逐行对照基准

> 状态:P0 产出(2026-08-18);2026-08-24 对齐修订(meta/threads 本地化、outbox 先存再调、X-AIMail-* 头改名、Q3 头路由、D8 board_id 派生、D11 role 模板);2026-08-25 角色管理闭环(批量画像 B1 / thread_summary 预加载 B2 / Role_Calibrator B3;my_role 与 _whoami_update_public 已删)
> 用途:dsh `mail-core` TS 实现入站预处理全链的**唯一对照基准**——TS 重写只换语言,不换行为。
> 基准源:aimail 仓库 `tools/aimail_base.py` 的 `preprocess_mail_payload` / `process_inbound_mail` / `handle_ping_pong` / `parse_amail_persona`(行号随版本演进,以语义为准)。
> 铁律:任一字段名/事件名/前缀/路径与本文不一致 = 契约破坏;验收以 `aimail ping` 三阶段日志 + welcome 双向为锁。

---

## 1. 输入契约(webhook payload)

bridge 转发的是 gateway 原始 body(逐字节透传)+ 头白名单(X-AIMail-Email / X-AIMail-Timestamp / X-Webhook-Signature)。

payload 字段(Rust gateway 入站队列产物):

| 字段 | 类型 | 说明 |
|------|------|------|
| `mail_id` | string | gateway 内部 UUID(预处理后剥离) |
| `message_id` | string | 邮件 Message-ID |
| `subject` | string | 主题(文本已清洗) |
| `body` | string | 正文(文本已清洗;可空) |
| `to` / `cc` | string \| string[] | 收件人(MIME 原始,可能含显示名) |
| `from` | string | 发件人(原始) |
| `headers` | dict | 原始 MIME 头(to/cc/from/subject 等) |
| `references` | string[] | 引用链 Message-ID |
| `attachments` | array | [{attachment_id\|id, filename\|name}] |
| `created_at` / `forwarder` / `forward_at` | — | backend-only(预处理后剥离) |
| `board_id` / `board_role` | string | A2A Board 上下文(仅 board 邮件注入) |

headers 必读键:`to` / `cc` / `from`(显示名解析源)。

## 2. 预处理 15 步(逐行对照)

| # | 步骤 | 逻辑(与 aimail_base 一致) | 输出/副作用 |
|---|------|------------------------------|-------------|
| 1 | body 检查 | `body` 空 → 记 warning(继续,不中断) | — |
| 2 | 加载 agent 配置 | 读 agentmail.json(`email` / `system_name`) | 缺失 email → 步骤 3 |
| 3 | 未配置检查 | 无 `email` → 记 warning,**返回** `{...payload, "_preprocess_error": "aimail email not configured"}` | 短路返回 |
| 4 | headers 解析 | 从 headers.to/cc/from 提取 (name, email) 对:`<...>` 内为邮箱,前缀为显示名;无尖括号且含 @ 则名=本地部分;逗号分隔;邮箱 lower() | `to_named` / `cc_named` / `from_named` |
| 5 | recipients 组装 | `result["recipients"] = {"to": [...], "cc": [...]}`,格式 `"名 <邮箱>"`(无名则裸邮箱);无 named 时用原始 to/cc | `recipients` |
| 6 | sender 设置 | `from_named` 存在 → `result["sender"] = "名 <邮箱>"`(SKILL.md 用 sender 不用 from) | `sender` |
| 7 | persona 提取 | 取属于 agent 域的收件人(以 `@agent_domain` 结尾的 to_bare 第一个);`parse_amail_persona(addr, system_name)` → (persona, profile, sys_name) | — |
| 8 | my_amail_addr | persona 存在且 **PERSONA_SUPPORTED=False → `= agent_email`**(归一基础地址,不做配置校验);支持且 persona 已配置 → `= my_to_addr`;未配置 → 回退 `agent_email`;无 persona → `= my_to_addr or agent_email` | `my_amail_addr` |
| 9 | direct_message / mentioned | DM = 单一 to 收件人 + 无 cc + 归一 base 相等;mentioned = 正文 `@profile`/`@显示名` 或分词命中(agent_local/profile/agent_display) | `direct_message`(bool)/ `mentioned`(bool) |
| 10 | 批量画像注入(B1) | 地址列表 `[sender] + to_bare + cc_bare`(去重,首地址=发件人)→ 单次 `GET /api/v1/contacts?addresses=a,b,c`(agent api_key)→ 返回 `{my_profile, sender_profile, recipients_profile}`;`my_profile`= 调用者已批准 persona(domain_addr_meta 唯一权威) | `my_profile`(string\|无)/ `sender_profile`(dict\|无)/ `recipients_profile`(dict\|无) |
| 11 | thread_summary 预加载(B2) | `thread_id = references[0]`(无则 message_id,与 store_inbound_message 写入同算法)→ 读本地 `threads/{xx}/{tid}.json`;**仅已存在**线程注入(首封无线程文件) | `thread_summary`(string\|无) |
| 12 | 附件下载 | 有 attachments → 用 **agent api_key**(非 admin_key)经 gateway `download_attachment(id)` 下载;落 `_aimail_dir()/{yyyymm}/attch/{sanitized_mid}/`;文件名取 basename | `attachments` = 本地路径数组 |
| 13 | 附件转换 | 扩展名 `.docx/.xlsx/.html/.htm` → markitdown 转 md,写同目录 `{stem}.md` 并追加路径;失败保留原文件 | `attachments` += md 路径 |
| 14 | 剥离 backend-only | pop:`mail_id` / `to` / `cc` / `headers` / `created_at` / `forwarder` / `forward_at` | 清理后 payload |
| 15 | 存储 + 日志 | `store_inbound_message(message_id, references, my_amail_addr, preprocessed_payload=result)`(save_raw_snapshots=true 时存快照)+ `_log_amail("inbound", from, my_addr, subject)` | 快照 `raw_email/{addr}/{yyyymm}/in-{mid}.json`;日志行 |

附加(仅在对应字段存在时):
- 步骤 14.5 `[WHOAMI]` 主题前缀 → `_whoami_prompt`(role 文件 whoami.md 模板填充),**early-return**(不再走 board extras 与 ping/pong 判定)
- 步骤 14.6 **Role_Calibrator(B3)**:subject 含 `update persona`(不区分大小写)→ `_role_prompt`(role 文件 **role_calibrator.md** 模板填充,SOUL/skills 经 build_ctx 自动注入),**early-return**(防 board role 覆盖)。role 文件名统一小写,`_read_role_file` 查找前强制 `.lower()`(大小写不敏感)。网关不拦截该邮件(无 manager 触发词),由 LLM 会话内归纳 draft persona+signature 并回复 manager
- 步骤 14.7 `board_id` + `board_role` → `_role_prompt`(board 角色文件模板填充,三级查找:地址级 `{sid}/{addr}/role_prompt/{role}.md` → 系统级 `{sid}/board/role_prompt/{role}.md` → 兜底 `common.md`)+ `_a2a_session_key = "a2a:{board_id}:{from}"`
- board_id 派生(board gateway 注册信):`sha256("{short}.a2a@{gw域名}")[:20]`,与 gateway `derive_board_id` 同算法;board_creds.json 存 `{gateway_url, token}`

**dsh 差异点(允许,契约内明示)**:本地存储已对齐——dsh 与 Python 同写 `meta/{xx}/{mid}.json` 常写(回复链依赖,不受快照开关控制)、`threads/{xx}/{tid}.json`(email_summary);raw 快照(in-/out-{mid}.json)受 save_raw_snapshots 开关控制(默认关);`_log_amail` 日志事件必须保留(三阶段日志判定依赖)。出站:sendMail 本地生成 Message-ID → 先存 meta 再调 API(先存再调,失败不回滚);写端头 `X-AIMail-Agent`(新名,gateway 白名单双名过渡);入站路由:bridge 单投注入 `X-AIMail-Email`(旧名 `X-Amail-Email` 过渡回退)为权威路由,头缺失才遍历 payload.to(批量投递无该头)。

## 3. 输出契约(富化后 payload,agent 可见)

保留:subject / body / message_id / references / recipients{to,cc} / sender / my_amail_addr / direct_message / mentioned / attachments(local paths)+ 原样保留未列字段(from 等)。
已剥离:mail_id / to / cc / headers / created_at / forwarder / forward_at。
可选:`_preprocess_error`(未配置短路)/ `my_profile` / `sender_profile` / `recipients_profile` / `thread_summary`(批量画像 + 线程预加载,命中才注入)/ `_whoami_prompt` / `_role_prompt` / `_a2a_session_key`。

## 4. ping/pong 契约(核心不可错)

| 项 | 值 |
|----|-----|
| PING_PREFIX | `__aimail_ping__:`(agent 侧识别入站 ping;ping 进入 agent 预处理链) |
| PONG_PREFIX | `__amail_pong__:`(agent 侧出站 pong 前缀;gateway send.rs P0 **精确匹配**拦截,不一致 pong 永不回环) |
| 拦截时机 | 预处理全链**最后一步**(`handle_ping_pong` 在 `preprocess_mail_payload` 之后调用;中间任何一步失败 → 不回 pong) |
| 判定 | subject 以 PING_PREFIX 开头 = ping;以 PONG_PREFIX 开头 = pong 回环 |
| 响应 | ping → `send_pong(payload, ping_id)`:调 send_mail 回发 subject=`__amail_pong__:{ping_id}`,body=`{"ping_id": ..., "event": {"mail_id": ...}}`(pong 按原 mail_id 键控,回复链可解析);pong 回环 → 拦截吞掉 |
| 三阶段事件 | `ping_intercepted`(agent 侧拦截入站 ping)→ `pong_sent`(agent 侧发出 pong)→ `pong_returned`(agent 侧收到回环 pong) |
| 日志 | `_log_ping_event(dir, ping_id, payload, pong_status)`:每事件一行 JSON 落 `~/.aimail/logs/agentmail.{cleaned_addr}.log`(cleaned_addr = 地址清洗,`.` 保留) |
| 判定源 | ping_test 只信 agent 侧日志三事件(ping_intercepted / pong_sent / pong_returned 按 ping_id 配对) |

## 5. 辅助函数契约

| 函数 | 行为(与 Python 一致) |
|------|----------------------|
| `parse_amail_persona(email, system_name="")` | 共享域(三/二段):`persona.profile.sys_name@domain` → (persona, profile, sys_name);短形式 `sys_name@domain` → ('', 'default', sys_name);独立域(两段):`persona.profile@domain` → (persona, profile, '');单段 → ('', local, '') |
| `base_email(email, system_name)` | 剥 persona 前缀:parse 后按 `profile[.sys_name]@domain` 重组 |
| `_to_list(v)` | list → strip 过滤空;string → 逗号拆分 strip;其它 → [] |
| `sanitize_message_id(mid)` | 文件名安全化(同 `_sanitize_message_id`) |

## 6. 目录契约

| 用途 | 路径 |
|------|------|
| 日志 | `~/.aimail/logs/agentmail.{cleaned_addr}.log` |
| 附件 | `~/.aimail/mail/{addr}/{yyyymm}/attch/{sanitized_mid}/`(Python `_aimail_dir()`) |
| meta(常写) | `~/.aimail/mail/{addr}/meta/{前2位}/{mid}.json`(256 桶分片) |
| threads | `~/.aimail/mail/{addr}/threads/{前2位}/{tid}.json`(email_summary 本地化) |
| 快照(可选,save_raw_snapshots=true) | 入站 `raw_email/{yyyymm}/in-{mid}.json`;出站 `{yyyymm}/out-{mid}.json` + 附件复制 `attch/{mid}/` |

dsh 侧 TS 实现沿用同一 `~/.aimail/` 根布局(附件/日志/meta/threads 落盘与 Python 共享,便于运维与双测判定);数据目录 env 统一为 `AIMAIL_HOME`(Python 侧旧名 `AGENTMAIL_HOME` 过渡兼容读)。

## 7. 契约验证(双测锁)

1. `aimail ping --system-id <dsh>`:三阶段事件齐全且 ping_id 配对 → 预处理链 + pong 出站契约通过。
2. `aimail welcome --system-id <dsh>`:管理员收到 Re: 回复(头 `X-AIMail-Agent: dsh/{ver}`)→ 完整双向契约通过。
3. 字段级断言(开发期):构造与 Python 相同入站样例,断言输出字段集与 §3 完全一致(recipients/sender/my_amail_addr/direct_message/mentioned/my_profile/thread_summary/剥离字段)。
