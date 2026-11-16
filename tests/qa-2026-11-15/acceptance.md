# DSH SSE 试点验收交接文档（正式）

- 出具：Verifier　日期：2026-11-15
- 被测版本：aimail `f967293` · aimail-gateway `8e0a0a5` · aimail-advanced `369efc0`
- 环境：Docker 29.1.3 / Compose 2.24.5 / Node 24.15.0 / pnpm 11.22.0 / Rust 1.98.1 / Linux 5.15
- 验收依据：`tests/dsh-acceptance/{QA-PROMPT,README,SCENARIOS,CLI-COVERAGE}.md`

## 1 结论

**PASS（通过）**

```
F1–F7 全部关闭 · R1–R5 全部落地 · V1–V7 全部通过
base 侧 B 系列（10 项）· tssdk 472 用例 · gateway 145 用例 · 存量 host 无回归
```

## 2 本次架构变更与验证结论

| 变更 | 结论 | 证据 |
|---|---|---|
| 取消 webhook push 端点 | PASS | 域创建响应 `webhook_url: null`；宿主/容器无 9099 监听 |
| 固定 SSE pull | PASS | hub status：`mode=sse · connected=true · streams=["system:system-8ee3"]` |
| 地址级激活（场景 1） | PASS | `alice@test.local` bind，`mode=code`，`kind=new` |
| 系统级对接（场景 5/6） | PASS | `domain_keys` + 默认工作员 + 默认工作区；多 domain 选对 key |
| 插件内闭环（双配置文件） | PASS | 插件自维护 `aimail_gateway.json` + `agentmail.json`，不 spawn CLI |
| 不影响其他 host 模式 | PASS | tssdk 全量 472 用例（含 openclaw/pi/hosts 目录）全绿 |

## 3 验收矩阵（V1–V7）

| 项 | 判据 | 实测 | 结论 |
|---|---|---|---|
| V1 码格式 | `^(sys\|addr)-([0-9a-f]{4}-){5}[0-9a-f]{4}$` | `sys-119e-45c6-3781-189c-7094-15d0` · `addr-e43b-77ce-1217-7a64-4556-f38c` · `addr-58c7-129e-2d75-dc62-0111-0546` | PASS |
| V2 前缀 | advanced 地址/系统统一 addr-/sys-，无派生前缀 | 实测邮件码均为 addr-；无 `shared_a-` | PASS |
| V3 邮件快照 | 英文 · 无 CJK · 无 dsh/renew/valid for the period · 含码有效期 | 三封实测正文符合（base 系统 7 days / 地址 24 hours；advanced 地址 24 hours） | PASS |
| V4 manager 缺省 | 未配 manager 时地址码可签发，manager=收件人 | advanced：`My safety officer (manager) is applicant@example.com`；base：`qa-user@example.com` | PASS |
| V5 /apply 页面 | 200 · lang=en · 0 CJK · 版式对齐 advanced | `lang="en"` · CJK=false · title `Claim Activation Code - AIMail` | PASS |
| V6 旧码作废 | 清表后旧码被拒 | `preflight(sys-55c79006d5b44e34) → 200 {"valid":false}`（脚本断言 rc=0） | PASS |
| V7 端到端 | base-loop / advanced-b2 通过 | 两条脚本 rc=0 | PASS |

## 4 脚本执行结果

| 脚本 | 结果 | 说明 |
|---|---|---|
| run-base-loop.sh | PASS | B1–B10 主链（申领/绑定/SSE 投递/暂停/恢复/解绑） |
| run-advanced-b2.sh | PASS | advanced 申请→取码→激活全链，F7 关闭验证 |
| run-tssdk-tests.sh | PASS | 50 文件 / 472 用例（含契约 ratchet、openclaw/pi/hosts） |
| run-gateway-tests.sh | PASS | 145 用例 |
| scripts/reset-codes.sh | PASS | 我方修复后 rc=0（离线 sqlite + preflight body 断言） |

## 5 缺陷闭环（F1–F7）

| 编号 | 首轮现象 | 修复 | 复验 |
|---|---|---|---|
| F1 | advanced 镜像构建失败（缺 libssl-dev/make）；补后运行缺 libcrypto.so.3 | 构建阶段加依赖、运行阶段改 Debian slim + libssl3 | 仓内 Dockerfile `up --build` rc=0 |
| F2 | base 卷属主 ROOT vs 非 root；崩溃后 PID 判活死循环 | compose 显式 user 0:0；PID 指向当前进程视为陈旧 | base `Up (healthy)` |
| F3 | claim 用本地域邮箱 → loopback，Mailpit 收不到 | 夹具改 qa-admin@/qa-user@example.com | base-loop rc=0 |
| F4 | `POST /api/v1/whitelists` 401；外部发信 550 | 默认工作员注册自动加白；loop 不再依赖 whitelist | base-loop rc=0 |
| F5 | apply 到邮件 >150s | 夹具启动顺序（Mailpit healthcheck + depends_on）+ retry poll=1s | `apply_ms=78`，邮件同秒级到达 |
| F6 | 契约 ratchet 越界 15 处 / 7 位置 | 常量单源 + 文档 contract-allowed 标记 | tssdk 472 全绿 |
| F7 | 地址码前缀 `shared_a-` 与夹具 `(sys\|addr)-` 不匹配 | 前缀改为稳定 sys-/addr- | advanced-b2 rc=0；邮件码 `addr-...` |

## 6 R1–R5 落地确认

```
R1 前缀统一     advanced scope_prefix → sys-/addr-（4 个调用点）；panel 可据此分组
R2 码体格式     base random_code → 6×4 hex；地址码复用同一函数；两仓形态一致
R3 语义澄清     码有效期保留（7d / 24h）；产品有效期与续期 base 不引入；
                manager 缺省=收件人（删除硬错误）
R4 三封邮件     主题/骨架/措辞与 advanced 对齐；英文、0 CJK；
                含可粘贴 prompt + gateway URL + docs 直链；无 dsh 措辞
R5 /apply 页    lang="en"、0 CJK、标题与版式对齐 advanced /apply/system 与 /apply/address
```

## 7 复现步骤

```bash
# 0 三仓取到被测 commit
aimail f967293 · aimail-gateway 8e0a0a5 · aimail-advanced 369efc0

# 1 起栈（本机需 host 网络构建以解析 DNS）
cd tests/dsh-acceptance
docker compose -f docker-compose.yml up -d --build
curl -s -o /dev/null -w '%{http_code}\n' http://127.0.0.1:18080/health   # 200

# 2 tssdk 先构建（harness 依赖 dist）
cd ../../tssdk && pnpm install --frozen-lockfile && pnpm build
cd ../tests/dsh-acceptance

# 3 按序执行
./scripts/reset-codes.sh
./scripts/run-base-loop.sh
./scripts/run-advanced-b2.sh
./scripts/run-tssdk-tests.sh
./scripts/run-gateway-tests.sh
```

## 8 残留风险与建议

```
1 gateway-tests 依赖同级仓布局（../../../aimail-gateway）；worktree 下需 symlink
2 契约 ratchet 基线现覆盖 sys-/addr-；后续若再改码格式，须同步 fixture 正则与基线
3 base 无产品有效期语义，仅码有效期；上线时按 plan 清空 activation_codes，避免旧码残留
4 SSE 重连未做长时压测（本轮仅验证连接建立、streams 存在、投递与暂停/恢复）
```

## 9 签字

```
判定：PASS
范围：DSH SSE 试点（base + advanced 双入口；不影响其他 host 模式）
签字：Verifier　2026-11-15
证据：本目录 feedback.md（测试结论与缺陷台账）、plan.md（变更细节）、
      上述脚本输出与 Mailpit 实发样本（文中已引码值）
```
