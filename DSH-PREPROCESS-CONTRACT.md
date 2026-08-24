# DSH-PREPROCESS-CONTRACT — dsh TS 预处理逐行对照基准

> 状态:P0 产出(2026-08-18)
> 用途:dsh `mail-core` TS 实现入站预处理全链的**唯一对照基准**——TS 重写只换语言,不换行为。
> 基准源:agentmail 仓库 `tools/aimail_base.py` 的 `preprocess_mail_payload` / `process_inbound_mail` / `handle_ping_pong` / `parse_amail_persona`(行号随版本演进,以语义为准)。
> 铁律:任一字段名/事件名/前缀/路径与本文不一致 = 契约破坏;验收以 `agentmail ping` 三阶段日志 + welcome 双向为锁。

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

## 2. 预处理 13 步(逐行对照)

| # | 步骤 | 逻辑(与 aimail_base 一致) | 输出/副作用 |
|---|------|------------------------------|-------------|
| 1 | body 检查 | `body` 空 → 记 warning(继续,不中断) | — |
| 2 | 加载 agent 配置 | 读 agentmail.json(`email` / `system_name`) | 缺失 email → 步骤 3 |
| 3 | 未配置检查 | 无 `email` → 记 warning,**返回** `{...payload, "_preprocess_error": "agentmail email not configured"}` | 短路返回 |
| 4 | headers 解析 | 从 headers.to/cc/from 提取 (name, email) 对:`<...>` 内为邮箱,前缀为显示名;无尖括号且含 @ 则名=本地部分;逗号分隔;邮箱 lower() | `to_named` / `cc_named` / `from_named` |
| 5 | recipients 组装 | `result["recipients"] = {"to": [...], "cc": [...]}`,格式 `"名 <邮箱>"`(无名则裸邮箱);无 named 时用原始 to/cc | `recipients` |
| 6 | sender 设置 | `from_named` 存在 → `result["sender"] = "名 <邮箱>"`(SKILL.md 用 sender 不用 from) | `sender` |
| 7 | persona 提取 | 取属于 agent 域的收件人(以 `@agent_domain` 结尾的 to_bare 第一个);`parse_amail_persona(addr, system_name)` → (persona, profile, sys_name) | — |
| 8 | my_amail_addr | persona 存在且 **PERSONA_SUPPORTED=False → `= agent_email`**(归一基础地址,不做配置校验);支持且 persona 已配置 → `= my_to_addr`;未配置 → 回退 `agent_email`;无 persona → `= my_to_addr or agent_email` | `my_amail_addr` |
| 9 | direct_message / mentioned | DM = 单一 to 收件人 + 无 cc + 归一 base 相等;mentioned = 正文 `@profile`/`@显示名` 或分词命中(agent_local/profile/agent_display) | `direct_message`(bool)/ `mentioned`(bool) |
| 10 | 附件下载 | 有 attachments → 用 **agent api_key**(非 admin_key)经 gateway `download_attachment(id)` 下载;落 `_agentmail_dir()/{yyyymm}/attch/{sanitized_mid}/`;文件名取 basename | `attachments` = 本地路径数组 |
| 11 | 附件转换 | 扩展名 `.docx/.xlsx/.html/.htm` → markitdown 转 md,写同目录 `{stem}.md` 并追加路径;失败保留原文件 | `attachments` += md 路径 |
| 12 | 剥离 backend-only | pop:`mail_id` / `to` / `cc` / `headers` / `created_at` / `forwarder` / `forward_at` | 清理后 payload |
| 13 | 存储 + 日志 | `store_inbound_message(message_id, references, my_amail_addr, preprocessed_payload=result)`(save_raw_snapshots=true 时存快照)+ `_log_amail("inbound", from, my_addr, subject)` | 快照 `raw_email/{addr}/{yyyymm}/in-{mid}.json`;日志行 |

附加(board,仅在对应字段存在时):
- 步骤 12.5 `[WHOAMI]` 主题前缀 → `_whoami_prompt`(模板填充)+ `_whoami_update_public=true`
- 步骤 12.6 `board_id` + `board_role` → `_role_prompt`(模板填充)+ `_a2a_session_key = "a2a:{board_id}:{from}"`

**dsh 差异点(允许,契约内明示)**:`store_inbound_message` 的 raw 快照/save_raw_snapshots 为 Python 侧本地存储——dsh 侧等价实现:无快照(save_raw_snapshots 不适用),但 `_log_amail` 日志事件必须保留(三阶段日志判定依赖)。

## 3. 输出契约(富化后 payload,agent 可见)

保留:subject / body / message_id / references / recipients{to,cc} / sender / my_amail_addr / direct_message / mentioned / attachments(local paths)+ 原样保留未列字段(from 等)。
已剥离:mail_id / to / cc / headers / created_at / forwarder / forward_at。
可选:`_preprocess_error`(未配置短路)/ `_whoami_prompt` / `_whoami_update_public` / `_role_prompt` / `_a2a_session_key`。

## 4. ping/pong 契约(核心不可错)

| 项 | 值 |
|----|-----|
| PING_PREFIX | `__agentmail_ping__:`(agent 侧识别入站 ping;ping 进入 agent 预处理链) |
| PONG_PREFIX | `__amail_pong__:`(agent 侧出站 pong 前缀;gateway send.rs P0 **精确匹配**拦截,不一致 pong 永不回环) |
| 拦截时机 | 预处理全链**最后一步**(`handle_ping_pong` 在 `preprocess_mail_payload` 之后调用;中间任何一步失败 → 不回 pong) |
| 判定 | subject 以 PING_PREFIX 开头 = ping;以 PONG_PREFIX 开头 = pong 回环 |
| 响应 | ping → `send_pong(payload, ping_id)`:调 send_mail 回发 subject=`__amail_pong__:{ping_id}`;pong 回环 → 拦截吞掉 |
| 三阶段事件 | `ping_intercepted`(agent 侧拦截入站 ping)→ `pong_sent`(agent 侧发出 pong)→ `pong_returned`(agent 侧收到回环 pong) |
| 日志 | `_log_ping_event(dir, ping_id, payload, pong_status)`:每事件一行 JSON 落 `~/.agentmail/logs/agentmail.{cleaned_addr}.log`(cleaned_addr = 地址清洗,`.` 保留) |
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
| 日志 | `~/.agentmail/logs/agentmail.{cleaned_addr}.log` |
| 附件 | `~/.agentmail/mail/{addr}/{yyyymm}/attch/{sanitized_mid}/`(Python `_agentmail_dir()`) |
| 快照(可选) | `~/.agentmail/mail/{addr}/raw_email/{yyyymm}/in-{mid}.json`(Python `_raw_email_dir()`) |

dsh 侧 TS 实现沿用同一 `~/.agentmail/` 根布局(附件/日志落盘与 Python 共享,便于运维与双测判定)。

## 7. 契约验证(双测锁)

1. `agentmail ping --system-id <dsh>`:三阶段事件齐全且 ping_id 配对 → 预处理链 + pong 出站契约通过。
2. `agentmail welcome --system-id <dsh>`:管理员收到 Re: 回复(头 `X-AIMail-Agent: dsh/{ver}`)→ 完整双向契约通过。
3. 字段级断言(开发期):构造与 Python 相同入站样例,断言输出字段集与 §3 完全一致(recipients/sender/my_amail_addr/direct_message/mentioned/剥离字段)。
