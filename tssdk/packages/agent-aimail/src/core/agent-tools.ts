/**
 * agent-tools — the single source for the agent-level tool name list.
 *
 * Dependency-free on purpose: `agent-level.ts` (the toolset registry)
 * imports it, and `agent-level.ts` already imports `preprocess.ts` —
 * putting the list in either of those two modules would create a cycle.
 *
 * This list is the registry-side single source (used to filter the
 * grouped tool definitions in agent-level.ts and adapter-layer toolset providers). It is NOT
 * the source of the {{TOOLSET_LIST}} board-ctx variable — that value is
 * host actual state injected via setToolsetProvider (empty when the host
 * has no stable anchor).
 */
export const MAIL_TOOL_NAMES = [
  'send_mail',
  'manage_contacts',
  'contact_profile',
  'set_contact_profile',
  'email_summary',
  'set_email_summary',
  'search_mail',
] as const

export const MAIL_BOARD_NAMES = [
  'board_status',
  'board_task_list',
  'board_task_show',
  'board_heartbeat',
  'board_members',
  'board_roles',
  'role_calibrator',
] as const

export const MAIL_ADMIN_NAMES = [
  'aimail_address',
] as const

/** Agent-level toolset: mail + board + address (system onboarding is
 *  DSH-only and intentionally not an agent-level tool). */
export const AGENT_TOOL_NAMES = [
  ...MAIL_TOOL_NAMES,
  ...MAIL_BOARD_NAMES,
  'aimail_address',
] as const

/** Complete DSH-facing set, including the admin/system onboarding tool. */
export const ALL_AGENT_TOOL_NAMES = [
  ...MAIL_TOOL_NAMES,
  ...MAIL_BOARD_NAMES,
  ...MAIL_ADMIN_NAMES,
] as const
