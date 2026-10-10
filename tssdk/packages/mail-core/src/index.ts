/**
 * @aimail/mail-core — AIMail shared TS core (framework-agnostic).
 *
 * Zero Cordis/dsh dependencies. Future TS agents (e.g. OpenClaw migration)
 * can import this package directly without touching dsh adapter packages.
 */
export * from './types.js'
export * from './typebox-params.js'
export * from './contract.js'
export { GatewayClient } from './gateway.js'
export { computeApiSignature, sha256Hex } from './api-signature.js'
export {
  AIMAIL_HOME,
  systemDir,
  cleanAddr,
  systemIdForEmail,
  agentConfigPath,
  loadAgentConfig,
  loadConfigBySessionId,
  loadConfigByAgentId,
  loadConfigByEmail,
  saveAgentConfig,
  updateAgentConfig,
} from './config.js'
export * from './release-resources.js'
export {
  listSystemDirs,
  readSystemConfig,
  emailForAgent,
  resolveRegisterEmail,
  registerAddress,
  saveBinding,
  resolveRegisterWebhook,
  ensureBindingWebhookSecret,
  syncAddressWebhook,
  autoBind,
} from './auto-bind.js'
export {
  INBOUND_STATE_FLAG,
  resolveAimailBin,
  notifyInboundState,
  notifyInboundForSystem,
  formatInboundNotifyLine,
  isInboundNotifyWarning,
  spawnRunner,
} from './inbound-notify.js'
export type {
  InboundState,
  InboundNotifyOutcome,
  NotifyOptions,
  CommandRunner,
} from './inbound-notify.js'
export { listAgentConfigs } from './config.js'
export { detectSystemForHome, ensureSystem } from './ensure-system.js'
export type { EnsureSystemOptions, EnsureSystemResult } from './ensure-system.js'
export {
  activateAddressCode,
  activateAddressCodePersist,
  pullList,
  pullAck,
  startPolling,
} from './address-code.js'
export {
  AGENT_SCOPE_SYSTEM_PREFIX,
  DEFAULT_PULL_INTERVAL_MS,
  DEFAULT_PULL_LIMIT,
  isAgentScopeBinding,
  resolveAgentPullSettings,
  startAgentPullEntries,
  stopAgentPullEntries,
} from './poll-entry.js'
export type {
  AgentPullHandle,
  AgentPullOverrides,
  AgentPullSettings,
  PullDecisionReason,
  PulledMail,
  PullInboundDelivery,
  StartAgentPullEntriesOptions,
} from './poll-entry.js'
export type {
  RequestClient,
  ActivateAddressCodeResult,
  ActivateAddressCodePersistOptions,
  ActivateAddressCodePersistResult,
  PullBatch,
  PullDelivery,
  PollStats,
  StartPollingOptions,
} from './address-code.js'
export type {
  SystemGatewayConfig,
  AdminClientLike,
  RegisterAddressOptions,
  RegisterAddressResult,
  SaveBindingOptions,
  AutoBindOptions,
  AutoBindResult,
} from './auto-bind.js'
export {
  setAgentIdentity,
  setAgentModel,
  sendMail,
  manageContacts,
  contactProfile,
  setContactProfile,
  emailSummary,
  setEmailSummary,
  searchMail,
} from './tools.js'
export type { ToolCtx, ToolResult, SendMailArgs, ManageContactsArgs, ContactProfileArgs, EmailSummaryArgs, SearchMailArgs } from './tools.js'
export {
  indexSnapshotRecord,
  collectAttachmentsMd,
  searchLocalMail,
  openSearchIndex,
} from './search.js'
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
  boardRoles,
  setPublicWhoami,
} from './board.js'
export type { BoardStatusArgs, BoardTaskListArgs, BoardTaskShowArgs, BoardHeartbeatArgs, BoardMembersArgs, BoardRolesArgs, SetPublicWhoamiArgs } from './board.js'
export { MAIL_TOOLS } from './tool-registry.js'
export type { MailToolDef, MailToolParam } from './tool-registry.js'
export {
  PING_PREFIX,
  PONG_PREFIX,
  processInboundMail,
  verifySignature,
  logPingEvent,
  logAimailInbound,
  parseAimailPersona,
  baseEmail,
  fillTemplate,
  routeAddressFromHeaders,
  promptRuleNameOk,
  readPromptRules,
  promptRuleMatches,
} from './preprocess.js'
export { opAssemble, opUpdate, opTeardown, opPromptTest, UsageError } from './sdk-ops.js'
export { logAimailDispatch } from './log.js'
