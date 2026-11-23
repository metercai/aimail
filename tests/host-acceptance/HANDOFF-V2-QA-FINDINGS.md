# AIMail V2 — SDK L2/L3 质检结论（当前状态）

- 日期：2026-11-23
- 结论：**HOLD**（1 个产品阻断缺陷 ING-1；其余为已修/已撤回项）
- 基线 tag：`qa-l2l3-baseline-2026-11-23`（aimail `14ce537` · gateway `d2af10a`/v1.0.11 · advanced `e270a48`）
- 被测：`agent-aimail 0.5.1`（dev 构建）· gateway **v1.0.11（已发布二进制）** · 真实宿主镜像 dsh 0.2.2-rc.1 / pi 1.5.2 / openclaw 2026.11.5 · 小米 `mimo-v2.6-flash`

---

## 1. 阻断缺陷（交开发修）

### ING-1（P1）SMTP 入站被转发到 SMTP 出站，未进入本地入库/队列

**现象**：外部发件人 → 本地 pull 地址的邮件，网关 SMTP 返回 250 接受，但
`emails` 表 0 行、`pending_deliveries` 0 行；同一封信出现在 `[relay] smtp_server`（sink）的 spool 里。

**按规范的四层排查（逐层实证）**
| 层 | 结果 |
|---|---|
| ① 发件人是否内部 host 地址 | `ext-sender@example.com` = 外部（内部会被 `550 Internal sender …` 拒，未出现） |
| ② 白名单 | `whitelists` 两条：manager 自动一条 + `ext-sender@example.com` 一条 |
| ③ `emails` | **0 行** ⇒ 入站流程未完成 |
| ④ `pending_deliveries` | 0 行（因此也没到 pull/webhook 层） |

**环境（全部正确）**：`system_domains` 同时含 `test.local` 与 `l2dsh@test.local`；
`domain_addr_meta(l2dsh@test.local, manager=l2-dsh-…@example.com)`；`agent_system_addresses` 有归属行；
`[smtp] hostname=test.local`；域名 id 与域一致；注入端口为网关 SMTP 监听口。

**设计判定（Owner）**：系统不存在"SMTP 入站 → SMTP 出站 relay"这条路径，属**缺陷**。

**修复后判据**：入站后 `emails` 出现 1 行 → `pending_deliveries` 出现 1 行 → 宿主流取走 → 行消失（ack 在 turn 之后）。
**影响面**：L2 S3（投递）与 L3 welcome 段无法判绿；与 SSE-V2 的 pull/push 交互改造无关（入站流程本不应改变）。

---

## 2. 已关闭项（验证方复核）

| 项 | 状态 | 证据 |
|---|---|---|
| B1 `isAgentScopeBinding` 判 V1 前缀 | 0.5.1 已修 | `poll-entry.ts` 现同时接受 V1/V2 形态；实验包改判据后 `polling enabled` 立即出现 |
| B2 适配器 pull client 缺 agent-scope 路径/头 | 0.5.1 已修 | 新增 `buildAgentSystemClient` + `startAgentPullWithClient`；dsh 实测 `SSE reconcile: 1 gateway stream(s) from 1 binding(s)` |
| B3 OpenClaw 入站未进 session | **待复验** | 原证据产生于旧夹具；须在 ING-1 修复后重跑 S3 再判 |
| B4 DSH 会话无工具帧 | **撤回（夹具问题）** | 真因是我方夹具：①装解包目录导致 `typebox` 缺失、插件未加载（改 tarball 安装）；②未设 `DSH_PERMISSION_MODE=danger-full-access`，工具调用挂在审批上。修正后 dsh S1 8/8 绿 |
| B5 V2 自助激活不写 manager/白名单 | v1.0.11 已修 | `d5afefa`：apply 记 requester → 激活写 `domain_addr_meta` + 自动白名单 + 响应含 `manager_address`；实测两条 whitelist 与 manager 均在库 |
| F1 `aimail_address` 返回 `raw_key` | **未修（P2）** | 工具结果仍带地址凭据，建议脱敏后再回填 binding |

---

## 3. L2 夹具现状（`tests/host-acceptance/v2/`）

**口径（Owner 五条规则已写入 `pins.env` 顶部与 `v2/README.md`）**
L2 = 当前 dev 构建、预装；只跑本机 OS+CPU；覆盖 **dsh / pi / openclaw** 三个 tssdk 宿主。
L3 = 已发布 SDK、由 agent 流程中自装；只测 **dsh**（跨平台）；hermes(pysdk) 标 legacy。
两层都用**已发布 gateway 二进制**（`GATEWAY_VERSION` + 平台 sha256 校验）。

**结构**
```
lib/gw.sh        下载+校验已发布 gateway、起停、sqlite 断言
lib/gw.py        SDK 无关的签名客户端：/apply 取码、白名单、admin 码
lib/gw_admin.py  域 offer（id=domain，与 L3 cell 一致）
lib/assert.sh    PASS / RED / GAP / SKIP_LEGACY + 退出码
lib/evidence.sh  run.json / host.log / 脱敏 binding.json
hosts/{dsh,pi,openclaw}.sh   仅此处出现宿主名；单生命期旅程
cases/s1..s5 + negatives
run-matrix.sh    --host all --stages "s1 s2 s3 s4 s5 neg"
```

**实测（本轮）**
```
dsh      : S1 ×8 PASS · S2 ×2 PASS · S3 白名单 PASS / 入库 RED(ING-1)
pi       : S1 ×7 PASS · S2 ×2 PASS（交付轮次早于 S3 落地）
openclaw : S1 ×7 PASS · S2 ×2 PASS
```

---

## 4. L3 现状

- `run-l3.sh`：新增 pinn 块 —— 按 `pins.env` 取 `GATEWAY_VERSION` + 平台 sha256，与 latest 不一致时改下钉版并校验，日志落 `gateway.json`。
- `run-v2.sh`：V2 gate = **dsh × 4 平台**；`pi/openclaw` 明确属 L2 范围，不进 L3 矩阵；`hermes × 4` 为 `SKIP_LEGACY`（`--include-legacy` 才执行，失败也不计入绿）。
- 未实跑：全矩阵只能在 GH runner 上跑；本机可 `--cell linux-amd64/dsh`。

---

## 5. 复验顺序（修完 ING-1 后）

1. `bash tests/host-acceptance/v2/run-matrix.sh --host all --stages "s1 s2 s3"` → 3 宿主全绿
2. S4 多地址 / S5 生命周期 / 负例 全绿
3. `bash tests/l3/run-v2.sh`（GH）→ dsh×4 绿
4. 我出签字结论
