/**
 * contract.ts — AIMail agent 侧契约常量(TS 侧唯一副本)。
 *
 * ⚠ 值必须等于仓根 `contract/aimail-contract.json`(单一真源)。
 * 本文件是**硬编码常量 + 门禁断言**形态(不是运行时读文件):npm 产物是自包含的
 * (bundleDependencies / files glob 不含仓根 contract/),运行时读清单在发布形态
 * 下必然 ENOENT —— 所以真源落盘、常量硬编码,由
 * `tests/contract/check-contract-single-source.py` 在 L0 门禁里逐项断言两者相等
 * (漂移即红并给 file:line)。
 *
 * 边界(owner 裁决 2026-09-27):合法的 `aimail` 面 = 产品/仓库/框架/CLI 名 +
 * `~/.aimail` + `AIMAIL_*` 环境变量 —— 这些**不是** agent 内部契约名, 不要按
 * 本模块判违约。本模块只约束 agent 内部契约面: skill/toolset 注册名、绑定
 * 文件名、指针文件名、入站路径。
 */

/** 四个平台(openclaw / dsh / pi / deer-flow)的固定入站路径。 */
export const INBOUND_PATH = '/aimail/inbound'

/** hermes 是唯一例外:入站挂在 hermes 网关自身的 webhook 路由上。 */
export const HERMES_INBOUND_PATH = '/webhooks/aimail-inbound'

/** hermes 网关路由名(webhook_subscriptions.json 的 route 键)。 */
export const HERMES_ROUTE_NAME = 'aimail-inbound'

/** agent 内部 skill 注册名 == SKILL.md frontmatter `name:`(目录名亦同)。 */
export const AGENT_SKILL_NAME = 'agentmail'

/** agent 内部 toolset 注册名 == SKILL.md frontmatter `toolset:`。 */
export const AGENT_TOOLSET_NAME = 'agentmail'

/** 每个地址的绑定文件名(systems/{sid}/{cleaned_addr}/agentmail.json)。 */
export const BINDING_FILE = 'agentmail.json'

/** 系统指针文件名(如 ~/.pi/.agentmail)。 */
export const POINTER_FILE = '.agentmail'

/** 入站监听端口默认值 —— **端口可配, 路径不可变**。 */
export const INBOUND_PORTS: Readonly<Record<string, number>> = { dsh: 9099, pi: 9101, deerflow: 8001 }

/**
 * 桥(bridge)默认转发路径 —— **已退役(SDK 去桥化, owner 裁决 2026-09-28)**。
 * 路由是 CLI 的环境职责(`cli/bridge_wire.py`, 由宿主通知的 `aimail address
 * --inbound-live` 触发), SDK 对桥无感, 契约真源同样不再含桥键(见
 * contract/aimail-contract.json + pysdk/aimail_contract.py, 三者同批收敛)。
 */

/** 本机入站接收端点(非 hermes 平台)。 */
export function inboundUrl (port: number, host = '127.0.0.1'): string {
  return `http://${host}:${port}${INBOUND_PATH}`
}
