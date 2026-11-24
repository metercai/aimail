# 真机安装验收（PI-Desktop 插件）— 每版必做一次

- 适用：`aimail.desktop-<version>.piplug`（来自 `plugin-v*` release）
- 为什么必须人工做：宿主**故意**不给自动化安装入口。见 `apps/desktop/electron/main/mcp-control.ts`
  的 `MCP_CONTROL_BLOCKED_CHANNEL_KEYS`，其中包含
  `pluginInstallFromPath`、`pluginInstallFromPackage`、`pluginLoadDev`、`pluginCreateFromTemplate`
  —— 也就是说 MCP/控制面被明确禁止安装插件（这是安全属性，不是缺功能）。官方 devkit
  （`@pi-desktop/plugin-devkit`，含权威的 `pack.ts`/`check.ts`）**未发布到 npm**（`npm view` 404），
  所以 CI 也无法用宿主自己的打包器交叉校验。
- 因此本项是**发版后的人工验收**，不能并入现有 L3 矩阵（L3 跑的是 CLI/SSE 形态的 agent 宿主）。
  要自动化只有两条路：CI 里从源码构建宿主调其内部安装 API，或宿主方提供 headless 安装钩子。

## 前置

1. 与 release 上一字不差地取件：
   ```bash
   gh release download plugin-v<ver> -p "aimail.desktop-<ver>.piplug*"
   sha256sum -c aimail.desktop-<ver>.piplug.sha256
   ```
   **hash 不符立即停止**，不要"先装上看看"。
2. 记录环境：宿主版本（应用"关于"里的版本号）、OS、是否首次安装（有无旧版插件）。

## 步骤与判据（逐条留证）

| # | 动作 | 通过判据 | 留什么证据 |
|---|---|---|---|
| 1 | 插件中心 → 导入本地 `.piplug` | 显示 `aimail.desktop 0.1.0`，无"格式不支持/签名"报错 | 截图 + 安装器日志原文 |
| 2 | 权限确认页 | 恰为 6 项：`net.fetch`、`net.anyHost`、`session.read.own`、`session.update.all`、`session.import`、`background.service`；少一项或多一项都要记 | 截图（逐项可见） |
| 3 | 启用插件 | `contributes.services` 被启动（宿主在 `onLoad` 返回后启动声明的 service） | 宿主日志里与插件/service 相关的行 |
| 4 | 插件设置里填网关地址 + 激活码对应的邮箱 | 保存后不报 schema 错（`apiKey` 明文存放属已知限制） | 设置页截图（遮住 code/key） |
| 5 | 发一封激活邮件到该地址 | 邮件出现在宿主会话里，且是**同一个会话**继续后续来回 | 会话截图 + `openclaw`/宿主侧 session id |
| 6 | 拒绝路径（最关键）：断开网关或填错网关 | 邮件**留在 pending 不被吃掉**；恢复后能重投 | 网关 pending 计数截图前后各一张 |
| 7 | 重启宿主 | 插件仍加载、服务仍启动、未重复要求授权 | 重启后截图 + 日志 |
| 8 | 卸载 | 服务停止、设置被清理、会话不被删除 | 卸载后截图 |

第 6 条是**唯一会伤数据**的路径（拒绝被当成功 ack）：冒烟里已有对应断言，但那是假宿主；
真机必须亲眼看一次 pending 不为 0。

## 结论记录

在 `docs/qa/REVIEW-PI-DESKTOP-<version>.md` 追加一节：

```
真机验收：宿主 <name> <ver> / <OS> / <首次|升级>
  1-8 全过 | 失败项: <编号 + 原始报错 + 截图路径>
  取件 hash 校验: OK（sha256 与 .sha256 文件一致）
```
