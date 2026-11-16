# DSH SSE 试点验收反馈（QA，2026-11-15）

- 被测：aimail `35260ea` · gateway `b4d2955` · advanced `1f48d62`
- 环境：Docker 29.1.3 / Compose 2.24.5 / Node 24.15.0 / pnpm 11.22.0 / Rust 1.98.1
- 结论：**HOLD** —— 唯一未闭合项为 F7（advanced 地址码前缀与夹具正则不一致）；其余 F1–F6 已复验通过

## 1 测试结论（脚本级）

| 脚本 | 结果 | 备注 |
|---|---|---|
| run-base-loop | **PASS** (8s) | 含 B1–B10 主链：申领/绑定/SSE 投递/暂停/恢复/解绑 |
| run-base-independent | **PASS** (1s) | system code=`sys-55c79006d5b44e34` |
| run-base-lifecycle | **PASS** (1s) | 绑定后轮换前状态正确 |
| run-base-rotation | **PASS** (1s) | 轮换后旧 key 失效 |
| run-tssdk-tests | **PASS** (18s) | 50 文件 / 472 用例，含契约 ratchet |
| run-gateway-tests | **PASS** (13s) | 145 用例 |
| run-advanced-b2 | **FAIL** (241s) | 邮件 13:06:35.515 已到，reader 报 240s 超时 ⇒ F7 |

## 2 已通过的关键行为

```
S2 固定 SSE pull：hub status mode=sse · connected=true · streams=["system:system-8ee3"]
B1 系统码申领：sys-55c79006d5b44e34（apply_ms 正常）
B2 系统对接：domain_keys + 默认工作员 + 默认工作区
B3 地址绑定：alice@test.local，mode=code，kind=new
B4 多 domain：bob@second.test 选中 second.test 的 key
G1 存量 host 无回归：openclaw + pi + hosts = 11 文件 / 50 用例全绿
F5 投递时延：apply_ms=78；邮件 Created 13:06:35.515（与 apply 同秒级）
```

## 3 缺陷台账 F1–F7

| 编号 | 状态 | 说明 |
|---|---|---|
| F1 advanced 镜像构建/运行 | **已修复 PASS** | 按仓内 Dockerfile `up --build` rc=0，无需补丁 |
| F2 base 卷权限/PID 判活 | **已修复 PASS** | base Up(healthy)，无重启循环 |
| F3 claim 用本地域邮箱 | **已修复 PASS** | 改用 qa-admin@/qa-user@example.com |
| F4 whitelist 401 | **已修复 PASS** | base loop 不再依赖 whitelist |
| F5 投递延迟 | **已修复 PASS** | apply_ms=78；邮件与 apply 同秒级 |
| F6 契约 ratchet | **已修复 PASS** | tssdk 全绿 |
| **F7 advanced 地址码前缀** | **未闭合 HOLD** | 见下 |

### F7 明细
```
夹具期望：^((?:sys|addr)-[A-Za-z0-9-]+)  (harness/mailpit_code.py:47)
产品实发：shared_a-9162-9909-230f-4fb9-434c-a4be
成因：advanced product_code_prefix(id) = id 首段.take(8)+"-"（activation.rs:43-47）
      地址码传入 system_id="shared_addr_*" ⇒ 截成 "shared_a"
影响：仅前缀展示；两仓无任何代码解析码前缀（已核），scope 由 system_id 前缀判定
性质：**夹具预期错**（旧码格式从未是 addr-），非本次改动引入（该逻辑自 05f911d 起存在）
```

## 4 修订建议（统一口径，5 项）

```
R1 advanced 前缀统一：scope_prefix → sys- / addr-（4 个调用点），不再由标识符截断派生
   panel 可按该前缀分组/筛选（owner 已确认用途）
R2 base 码体格式统一：random_code 改为 6×4 hex（与 advanced 同构）；
   地址码复用同一函数，删除 inline 重复实现；统一正则 ^(sys|addr)-([0-9a-f]{4}-){5}[0-9a-f]{4}$
R3 base 语义澄清：保留"码有效期"（与 advanced 一致）；不引入"产品有效期/续期"；
   manager 缺省=收件人（删除 activation.rs:275-277 硬错误，对齐 advanced 回落逻辑）
R4 base 三封邮件与 advanced 对齐：主题/骨架/措辞一致，英文、无 CJK；
   含"粘贴给 agent 的 prompt 句"与 safety officer 子句（缺省收件人）；
   不含产品有效期/续期段落；去掉 "dsh aimail_* tool" 这类 host 相关措辞
R5 base /apply 页英文化：与 advanced 申请页（见 §5）布局与格式一致
```

## 5 advanced 申请页事实（更正我在 PR#2 中的错误陈述）

```
我此前称"advanced 无 HTML 申请页"——错误，实测：
  GET http://<advanced>:18081/apply/system   → 200
  GET http://<advanced>:18081/apply/address  → 200
  GET /apply-license（machine 参数入口）     → 亦存在
页面由 frontend/dist SPA 提供，路由 /apply/{system,address}（含 license 注册页）
⇒ base /apply 的英文化与版式，应以这两个页面为基准，而不是 licenses_html.rs 的设计常量
（PR#2 文档 §6 中"以 licenses_html 为基准"的说法作废）
```

## 6 验收断言（修订后）

```
V1 码格式      ^(sys|addr)-([0-9a-f]{4}-){5}[0-9a-f]{4}$（base 与 advanced 各 1 单测）
V2 前缀        advanced：地址码 addr-、系统码 sys-（共享域/独享域同）；断言无 shared_a-/p-defaul-
V3 邮件快照    base 三封 ×（有/无 name）= 6 例：
               无 CJK · 无 "dsh" · 无 "renew"/"valid for the period"
               含 "within 7 days"（系统）/ "within 24 hours"（地址）
               地址邮件含 "My safety officer (manager) is <收件人>"
V4 manager     未配置 manager_address 时地址码可签发，邮件 manager=收件人
V5 页面        base GET /apply 200 · lang="en" · 0 CJK · 版式对齐 /apply/system 与 /apply/address
V6 旧码        清表后旧码激活返回 400/404
V7 端到端      run-base-loop、run-advanced-b2 通过（F7 关闭）
```

## 7 环境与偏离记录

```
未改三仓任何源码；仅新增本地覆盖文件（不入库）：
  DNS：compose build 使用 host 网络（容器内 127.0.0.53 不可达）
  否则全部按仓内配置与脚本执行
tssdk 需先 pnpm install --frozen-lockfile && pnpm build（dist 不存在时 harness 无法运行）
gateway-tests 需同级仓布局（../../../aimail-gateway），worktree 下用 symlink 补齐
```

## 8 V6 复验与夹具修复（QA 自查，2026-11-15）

```
N1 reset-codes.sh 原实现：容器内 apk add sqlite（无网络/DNS）⇒ rc=1  ⇒ 已修
   改为 python:3.11-alpine（stdlib 自带 sqlite3）+ 从运行容器读取 compose project 推导卷名，
   不再依赖容器内联网
N2 旧码断言：原脚本无端点  ⇒ 已补
   端点 /api/v1/public/activation-code/preflight
   语义：无效码返回 **HTTP 200 + {"valid":false,"error":"unknown activation code"}**
        （不是 400/404）⇒ 断言改为校验 JSON body.valid === false
结果：reset-codes.sh 实跑 rc=0，"V6 PASS: old code rejected (valid=false)"
```

验证输出：
```
cleared /base/aimail.db 1 rows · cleared /adv/aimail.db 0 rows
preflight(sys-55c79006d5b44e34) -> 200 {"error":"unknown activation code","valid":false}
V6 PASS: old code rejected (valid=false)
```
