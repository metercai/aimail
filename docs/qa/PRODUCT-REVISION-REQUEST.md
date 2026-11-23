# 产品代码修订建议书（V2 · 由验证方出，开发执行）

- 日期：2026-11-23
- 来源：L2/L3 重构期间的独立质检（真实宿主 + 已发布 gateway v1.0.11 + 小米 LLM）
- 边界：验证方只出建议，不改 `src/`；修完按每条"判据"复验。

---

## REQ-1（P1，阻断）SMTP 入站被转发到出站 relay，未入库

**现象**
外部发件人 → 本地 pull 地址，网关 SMTP 返回 250，但 `emails` / `pending_deliveries` 均 0 行；
同一封信出现在 `[relay] smtp_server` 的 spool。

**分层排查（按规范顺序，逐层实证）**
1. 发件人非内部 host 地址（内部地址会被 `550 Internal sender address rejected on inbound path`，未出现）
2. 白名单已建（`whitelists`：manager 自动一条 + 外部发件人一条）
3. `emails` = 0 ⇒ 入站流程未完成
4. `pending_deliveries` = 0 ⇒ 未到 pull/webhook 层

**环境（确认正确）**：`system_domains` 含域与地址两行；`domain_addr_meta` 有 manager；
`agent_system_addresses` 有归属；`[smtp] hostname` = 测试域；域 id = 域名；注入到网关 SMTP 口。

**判定**：系统设计不存在"SMTP 入站 → SMTP 出站"路径，属缺陷（Owner 判定）。

**建议修法**
- 入站（RCPT 命中 `system_domains`/`domain_addr_meta` 的本地地址）只走 `create_inbound` + 本地队列；
  `relay.smtp_server` 仅用于**出站**（含 DSN/回复），不得承接入站原始邮件。
- 若现有代码在"无 webhook 的地址"分支误落到 relay，请把该分支改为写 `pending_deliveries`。

**判据**
1. 入站后 `emails` 出现 1 行（含 `direction=inbound`），`pending_deliveries` 出现 1 行
2. 宿主流取走后该行消失，且 ack 时间 > turn 完成时间
3. sink spool **不得**出现该入站原始邮件

---

## REQ-2（P2，安全）`aimail_address` 工具结果回传 `raw_key`

**现象**：bind 成功的工具结果包含地址凭据 `raw_key`（实测输出可见）。
**影响**：凭据进入模型上下文/宿主日志，违反"密钥不入对话/日志"的卫生要求。
**建议**：工具结果只回 `{success, action, email, system_id, agent_system_id, workspace_path}`；
密钥仅落 binding 文件（0600），需要时由宿主内部读取。
**判据**：同一 bind 流程的工具结果与宿主日志中均无 `raw_key`/`api_key` 明文；binding 仍为 0600 且可用。

---

## REQ-3（P3，测试稳定性）`listen.test.ts` 端口断言偶发

**现象**：偶发 `expected [p, p+2]`（端口被宿主进程占用）。
**建议**：断言放宽到契约允许的端口集合，或改为"端口来自配置/环境"的语义断言。
**判据**：连续 20 次单测无红。

---

## REQ-4（信息项，pysdk 范围）hermes 的 legacy HMAC 被 gateway 拒

**现象**：L3 hermes cell `Route 'aimail-inbound' sent legacy body-only HMAC … require X-Webhook-Signature-V2` → 404。
**性质**：pysdk 0.2.9 未迁移 SSE-V2（L3 中已标 `SKIP_LEGACY`，不计入 V2 判定）。
**建议**：随 pysdk 迁移一并处理；迁移前不要以 hermes 的成败判定本次交付。

---

## REQ-5（信息项）contract 文档与矩阵口径

**建议**：在 `docs/aimail-host-sse-contract-v2.md` 或验收文档中写明：
L2 覆盖 dsh/pi/openclaw（本机平台、dev 构建）；L3 只覆盖 dsh（跨平台、已发布件）；hermes/deerflow 属 pysdk legacy，不进 V2 判定。
**理由**：避免后续把 L3 的 hermes 红cell 当成本次交付缺陷。

---

## 附：本轮已由验证方修复的夹具侧问题（无需开发处理）

1. 插件必须用 **tarball** 安装（装解包目录会缺 `typebox`，插件不加载）
2. dsh 必须 `DSH_PERMISSION_MODE=danger-full-access`，否则工具调用挂在审批
3. 断言去宿主化：dsh 不持久化 `agent_system_id`；拉起日志为 `SSE reconcile: N gateway stream(s)`
4. L2 gateway fixture 与 L3 cell 对齐（hostname=测试域、域 id=域名、注入走网关 SMTP 口、钉版+sha256）
