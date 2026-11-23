/**
 * @aimail/mail-core — AIMail shared TS core (framework-agnostic).
 *
 * Zero Cordis/dsh dependencies. Future TS agents (e.g. OpenClaw migration)
 * can import this package directly without touching dsh adapter packages.
 */
export * from './types.js'
export * from './typebox-params.js'
export * from './contract.js'
export * from './listen.js'
export { GatewayClient, SseParser, type SseEvent } from './gateway.js'
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
export { ensureAgentBinding } from './ensure-binding.js'
export type { EnsureAgentBindingOptions, EnsureAgentBindingResult } from './ensure-binding.js'
export {
  activateAddressCode,
  activateAddressCodePersist,
  aimailAddress,
  setProfileDirResolver,
  findBindingByEmail,
  setBindingPaused,
  archiveBinding,
  restoreBinding,
  clearPointerIfEmailMatch,
  BINDING_ARCHIVED_SUFFIX,
  pullList,
  pullAck,
  startPolling,
  PullAuthError,
  AUTH_FATAL_STREAK,
} from './address-code.js'
export {
  PULL_STREAM_PATH,
  PULL_HOLD_MAX_MS,
  PULL_HTTP_TIMEOUT_MS,
  PULL_STREAM_READ_TIMEOUT_MS,
  PULL_STREAM_PING_TIMEOUT_MS,
  PULL_REPROBE_INTERVAL_MS,
  PULL_MODE_ENV,
  parsePullMode,
  pullStreamBackoffSecs,
  pullPollBackoffSecs,
  pullDecideNextMode,
} from './pull-contract.js'
export type { PullMode, StreamRoundResult } from './pull-contract.js'
export { isAuthResult } from './pull-contract.js'
export type { PullDeliveryResult } from './address-code.js'
export {
  AGENT_TOOL_NAMES,
  agentTools,
  installAgentSkill,
  startAgentLevel,
} from './agent-level.js'
export {
  ALL_AGENT_TOOL_NAMES,
  MAIL_TOOL_NAMES,
  MAIL_BOARD_NAMES,
  MAIL_ADMIN_NAMES,
} from './agent-tools.js'
export type {
  AgentLevelOptions,
  AgentLevelRuntime,
  InstallAgentSkillResult,
} from './agent-level.js'
export {
  AGENT_SCOPE_SYSTEM_PREFIX,
  DEFAULT_PULL_INTERVAL_MS,
  DEFAULT_PULL_LIMIT,
  isAgentScopeBinding,
  isV1AgentScopeBinding,
  isV2AgentScopeBinding,
  resolveAgentPullSettings,
  startAgentPullEntries,
  stopAgentPullEntries,
  startAgentSystemGroups,
  buildAgentSystemClient,
  freshDeliveryTarget,
  deliverySkipCheck,
  countActiveAgentSystemBindings,
} from './poll-entry.js'
export type {
  AgentPullHandle,
  AgentPullOverrides,
  AgentPullSettings,
  PullDecisionReason,
  PulledMail,
  PullInboundDelivery,
  StartAgentPullEntriesOptions,
  StartAgentSystemGroupsOptions,
} from './poll-entry.js'
export type {
  RequestClient,
  ActivateAddressCodeResult,
  ActivateAddressCodePersistOptions,
  ActivateAddressCodePersistResult,
  AimailAddressArgs,
  AimailAddressResult,
  BindingLocate,
  LifecycleResult,
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
  aimailAddressTool,
  roleCalibrator,
} from './tools.js'
export type { ToolCtx, ToolResult, SendMailArgs, ManageContactsArgs, ContactProfileArgs, EmailSummaryArgs, SearchMailArgs, ActivateAddressCodeArgs, AimailAddressToolArgs } from './tools.js'
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
  saveInboundSnapshot,
} from './meta.js'
export type { LocalMeta } from './meta.js'
export {
  registerBoardGateway,
  boardGatewayUrl,
  deriveBoardIdFromAddress,
  normalizeBoardHandle,
  boardStatus,
  boardTaskList,
  boardTaskShow,
  boardHeartbeat,
  boardMembers,
  boardRoles,
} from './board.js'
export type { BoardStatusArgs, BoardTaskListArgs, BoardTaskShowArgs, BoardHeartbeatArgs, BoardMembersArgs, BoardRolesArgs } from './board.js'
export { MAIL_TOOLS, MAIL_BOARD, MAIL_ADMIN, AGENT_TOOL_DEFS } from './tool-registry.js'
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
  setSoulProvider,
  setSkillsProvider,
  setToolsetProvider,
  setPersonaSupport,
  setPersonasProvider,
} from './preprocess.js'
export { opAssemble, opUpdate, opTeardown, opPromptTest, UsageError } from './sdk-ops.js'
export { ensureSkillToolExposure } from './skill-exposure.js'
export { listSkillNames, combineSkillNames } from './skill-roots.js'
export { logAimailDispatch } from './log.js'

/** Hot-add seam: fired after a binding is persisted (activation). */
export { onBindingCreated, fireBindingCreated, onBindingRemoved, fireBindingRemoved, type BindingCreatedHook, type BindingRemovedHook } from './binding-hooks.js'
export { startAgentPullEntry, type StartAgentPullEntryOptions } from './poll-entry.js'

export { packageResourcesRoot } from './resources-root.js'
export { startSystemPull } from './system-pull.js'
export type { SystemPullMail, SystemPullOptions, SystemPullHandle } from './system-pull.js'

export { getAgentSystemId, deriveAgentSystemId, setAgentHomeResolver, resetAgentSystemIdForTests } from './agent-system-id.js'
