/**
 * @aimail/mail-core — AgentMail shared TS core (framework-agnostic).
 *
 * Zero Cordis/dsh dependencies. Future TS agents (e.g. OpenClaw migration)
 * can import this package directly without touching dsh adapter packages.
 */
export * from './types.js'
export { GatewayClient } from './gateway.js'
export { computeApiSignature, sha256Hex } from './api-signature.js'
export {
  AIMAIL_HOME,
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
  setAgentModel,
  sendMail,
  manageContacts,
  contactProfile,
  setContactProfile,
  emailSummary,
  setEmailSummary,
} from './tools.js'
export type { ToolCtx, ToolResult, SendMailArgs, ManageContactsArgs, ContactProfileArgs, EmailSummaryArgs } from './tools.js'
export {
  sanitizeMessageId,
  agentMailDir,
  localMetaPath,
  threadPath,
  saveLocalMeta,
  readLocalMeta,
  resolveThreadId,
  saveOutboundSnapshot,
} from './meta.js'
export type { LocalMeta } from './meta.js'
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
export { MAIL_TOOLS } from './tool-registry.js'
export type { MailToolDef, MailToolParam } from './tool-registry.js'
export {
  PING_PREFIX,
  PONG_PREFIX,
  processInboundMail,
  verifySignature,
  logPingEvent,
  logAmailInbound,
  parseAmailPersona,
  baseEmail,
  fillTemplate,
  routeAddressFromHeaders,
} from './preprocess.js'
