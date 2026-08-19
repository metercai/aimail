# agentmail-dsh-plugin — AgentMail 插件(独立仓库)

AgentMail ↔ dsh(deepseek-harness)集成插件。**独立发布**,经 dsh 官方插件命令安装:

```bash
# 安装(幂等)
dsh plugin --profile web add dsh-amail

# 卸载(幂等)
dsh plugin --profile web remove dsh-amail
```

安装后 profile 自动挂载 bundle 层(`dsh.bundle.patch` = `cordis.patch.yml`):
- `@agentmail/mail` — host 服务(ctx.mail:session_id → agentmail.json 绑定解析)
- `@agentmail/mail-inbound` — 入站端点(node:http:验签 → TS 预处理 → ping/pong → followup)
- `@agentmail/tool-mail` — 12 个裸名工具(send_mail / manage_contacts / contact_profile / set_contact_profile / email_summary / set_email_summary / board_status / board_task_list / board_task_show / board_heartbeat / board_members / set_public_whoami)
- persona(邮箱 agent 人设)

## 包结构

| 包 | 职责 | 依赖 |
|---|---|---|
| `@agentmail/mail-core` | TS 共享核心(零框架依赖):gateway client、12 工具函数、入站预处理 13 步全链、agentmail.json 读写 | 无 |
| `@agentmail/mail` | host 层:ctx.mail 服务(session/agent/email 反查,AMAIL_SYSTEM_ID 限域) | cordis(peer) |
| `@agentmail/tool-mail` | 12 defineTool(裸名,dsh-tools 裸 JSON-Schema DSL) | cordis/dsh-tools(peer) |
| `@agentmail/mail-inbound` | node:http 入站端点(默认 127.0.0.1:9099/agentmail/deliver) | cordis/dsh-agent/dsh-llm(peer) |
| `agentmail`(入口) | bundle 聚合:`dsh.bundle.patch` + cordis.patch.yml + 导出 mail-core | 4 包 |

## 契约基准

- 预处理/富化/ping-pong 逐行契约:agentmail 仓库 `DSH-PREPROCESS-CONTRACT.md`
- 配置规范:agentmail 仓库 `AGENTMAIL-JSON-REFERENCE.md`(agentmail.json 唯一信任源;webhook_url/secret 成对;webhook_host 三态)

## 绑定流程

1. `agentmail install`(agentmail 仓 CLI;dsh 分支安装 skill)
2. 在 dsh 中创建 session(加入 mail preset / 或已装 bundle 的 profile)
3. `python3 scripts/dsh/bind_agent.py`(agentmail 仓):注册地址(三态参数)→ agentmail.json 落盘(session_id/preset/webhook_url/secret)→ 注册 bridge 路由

## 开发

```bash
pnpm install
npx tsc -b packages/mail-core packages/mail packages/tool-mail packages/mail-inbound packages/agentmail
```

## 不发布 npm 的验证(实测通过,2026-08-18)

`dsh plugin add agentmail` = profile 目录 pnpm add + bundle reconcile。不发布时用**本地路径安装**模拟全流程:

```bash
# 1. 临时把 5 包 dependencies 的 workspace:^ 改为 file: 相对路径
#    (验证后改回;pnpm 对 link 包内的 file: 依赖按包目录解析)
# 2. 模拟 dsh plugin add:
cd ~/.dsh/profiles/web
pnpm add /path/to/agentmail-dsh-plugin/packages/agentmail
#    → dependencies 写入 link: 路径,node_modules/agentmail 符号链接

# 3. 运行时加载验证:
node -e "import('agentmail').then(async m => {
  const cfg = await m.loadAgentConfig('<sid>', '<addr>'); console.log(cfg.email) })"

# 4. reconcile 模拟(dsh plugin 的 bundle 识别逻辑):
#    entry 包 package.json 的 dsh.bundle.patch 非空 → 加入
#    profile 的 dsh.profile.bundles(实测:agentmail 加入,4 条目 patch 就位)

# 5. 模拟卸载(幂等):
pnpm remove agentmail
#    → dependencies 清空 + bundles 移除 agentmail
```

发布前完整链路验证(可选):本地 verdaccio(registry 代理),5 包 `pnpm publish --registry http://127.0.0.1:4873`,再 `dsh plugin --profile web add dsh-amail --registry http://127.0.0.1:4873`。

发布(需 npm 账号):`pnpm -r publish`(先发布 4 个 @agentmail/*,再发布入口 `agentmail`)。
