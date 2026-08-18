# @deepseek-ai/dsh-mail-core

AgentMail 共享 TS 核心(框架无关,零 Cordis 依赖)。

dsh mail 集成四包之一(`packages/email/`):
- `mail-core`(本包,共享库):gateway client + 12 工具函数 + 入站预处理全链 + agentmail.json 读写
- `mail`(host 层):ctx.mail 服务,包装 mail-core
- `tool-mail`(preset 层):12 defineTool(裸名),包装 mail-core
- `mail-inbound`(host 层):node:http 端点,验签 + 预处理 + ping/pong + followup

未来 OpenClaw 等 TS agent 可直接 import 本包(不碰 dsh 适配包)。

## 契约基准

- 工具/预处理契约:agentmail 仓库 `DSH-PREPROCESS-CONTRACT.md`(逐行对照表)
- 配置契约:agentmail 仓库 `AGENTMAIL-JSON-REFERENCE.md`(agentmail.json 唯一信任源)

## 现状(开发中)

- [x] types(AgentConfig/InboundPayload/EnrichedPayload/GatewayResponse)
- [x] config(agentmail.json 读写:load/save/原子写/cleanAddr/session_id 反查)
- [x] gateway client(HTTP 封装:send/upload/download/whitelist/contacts/agent-state/thread-summary/board API)
- [ ] 12 工具函数(包装 gateway client)
- [ ] 入站预处理全链(13 步,契约对齐)

## Known Limitations

- 预处理链的 raw 快照(save_raw_snapshots)为 Python 侧本地存储,dsh 不实现;日志事件(ping_intercepted/pong_sent/pong_returned)必须保留。
