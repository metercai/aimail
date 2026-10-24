/**
 * agent-tools — the single source for the agent-level tool name list.
 *
 * Dependency-free on purpose: both `agent-level.ts` (the toolset registry)
 * and `preprocess.ts` (the {{TOOLSET_LIST}} board-ctx variable) import it,
 * and `agent-level.ts` already imports `preprocess.ts` — putting the list
 * in either of those two modules would create a cycle.
 */
export const AGENT_TOOL_NAMES = [
  'activate_address_code',
  'role_calibrator',
  'send_mail',
  'manage_contacts',
  'contact_profile',
  'set_contact_profile',
  'email_summary',
  'set_email_summary',
  'search_mail',
  // board tools: usable as a board MEMBER — the invite mail carries the
  // token and the inbound chain stores it (board_creds.json). Creating a
  // board is not an agent-level action.
  'board_status',
  'board_task_list',
  'board_task_show',
  'board_members',
  'board_roles',
  'board_heartbeat',
] as const
