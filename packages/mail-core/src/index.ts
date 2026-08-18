/**
 * @agentmail/mail-core — AgentMail shared TS core (framework-agnostic).
 *
 * Zero Cordis/dsh dependencies. Future TS agents (e.g. OpenClaw migration)
 * can import this package directly without touching dsh adapter packages.
 */
export * from './types.js'
export { GatewayClient } from './gateway.js'
export {
  AMAIL_HOME,
  systemDir,
  cleanAddr,
  agentConfigPath,
  loadAgentConfig,
  loadConfigBySessionId,
  loadConfigByAgentId,
  loadConfigByEmail,
  saveAgentConfig,
  updateAgentConfig,
} from './config.js'
export {
  setAgentIdentity,
  sendMail,
  manageContacts,
  contactProfile,
  setContactProfile,
  emailSummary,
  setEmailSummary,
  sanitizeMessageId,
} from './tools.js'
export type { ToolCtx, ToolResult, SendMailArgs, ManageContactsArgs, ContactProfileArgs, EmailSummaryArgs } from './tools.js'
export {
  registerBoardGateway,
  boardGatewayUrl,
  resolveBoard,
  boardStatus,
  boardTaskList,
  boardTaskShow,
  boardHeartbeat,
  boardMembers,
  setPublicWhoami,
} from './board.js'
export type { BoardStatusArgs, BoardTaskListArgs, BoardTaskShowArgs, BoardHeartbeatArgs, BoardMembersArgs, SetPublicWhoamiArgs } from './board.js'
export {
  PING_PREFIX,
  PONG_PREFIX,
  processInboundMail,
  verifySignature,
  logPingEvent,
  logAmailInbound,
  parseAmailPersona,
  baseEmail,
} from './preprocess.js'
