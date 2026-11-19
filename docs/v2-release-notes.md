# AIMail Host SSE V2 — Release Notes

版本：`agent-aimail 0.5.0`  
契约：[`aimail-host-sse-contract-v2.md`](aimail-host-sse-contract-v2.md)

## 主要变化

- 地址激活只保留 `aimail_address`；移除独立 `aimail_system` 工具；
- 激活 prompt 使用工具名触发 SKILL，不再引用旧 CLI 指引；
- 地址激活后由 Gateway 返回 `GatewaySystemId`，本地按它分组；
- 新增本地 `AgentSystemId`，按 `(AgentSystemId, Gateway)` 聚合拉取；
- Gateway 新增 AgentSystemId 归属、聚合 pending/ack 和聚合 SSE；
- base 申请页只保留地址申请；
- advanced 申请页支持共享域、带系统标识名共享域、独享域；
- standalone 只允许独享域地址申请；
- `aimail_address` 支持 pause/resume/unbind/move；
- activation SKILL 独立安装；
- DSH/Pi/OpenClaw 生产 pull 支持聚合路径。

## 验证

```text
L0: PASS
contract: PASS
L2 dsh: PASS
L2 pi: PASS
L2 openclaw: PASS
L2 two gateways: PASS
OpenClaw 双地址聚合 L2: PASS
DSH 双地址聚合 L2: PASS
A/B pending/ack 隔离: PASS
```

## 升级注意

- 旧 binding 没有 `agent_system_id` 时，启动补本地派生值；
- advanced/base 需要新版 Gateway 才支持聚合端点；
- 旧 Gateway 仍走单地址拉取作为过渡；
- 旧 `docs/agent-self-setup.md` 与 CLI/pysdk 安装路径不在 V2 主路径，作为 legacy 保留；
- OpenClaw 需要正确的 plugins.allow / tools.alsoAllow 和真实工具名。

## 已知限制

- 两 Gateway 隔离 L2 尚未自动化；
- 三类地址端到端 L2 尚未自动化；
- OpenClaw 多地址真实 sessionKey 路由未做 L2；
- DSH/OpenClaw/Pi 的部分旧代码仍保留 legacy 分支；
- 发布推送受本机 GitHub 凭据影响时需先恢复凭据。

## 一键发布门禁

```bash
./tests/host-acceptance/run-v2-release-gate.sh
```

覆盖 L0、三宿主 L2、真实双 Gateway L2；本机已通过。
