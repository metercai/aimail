/**
 * MAIL_TOOLS — the single TS source of truth for the 12 AIMail tool
 * semantic definitions (names, descriptions, parameter descriptions).
 *
 * Contract: text is verbatim from tools/amail_mcp_server.py TOOLS registry
 * (aimail repo); a vitest parity case pins TS↔Python so the two cannot
 * drift. Platform adapters (dsh-aimail, openclaw-aimail) iterate this array
 * and bind each entry's `handler` to their own identity resolution — no
 * adapter re-declares semantic text.
 *
 * Parameter schema uses the platform-neutral shape below; platform-specific
 * details (dsh output.schema/render, openclaw TypeBox) are translated by the
 * adapter at registration time.
 */
import {
  sendMail,
  manageContacts,
  contactProfile,
  setContactProfile,
  emailSummary,
  setEmailSummary,
  searchMail,
  type SendMailArgs,
  type ManageContactsArgs,
  type ContactProfileArgs,
  type SearchMailArgs,
  type ToolCtx,
  type ToolResult,
} from './tools.js'
import {
  boardStatus,
  boardTaskList,
  boardTaskShow,
  boardHeartbeat,
  boardMembers,
  setPublicWhoami,
  type BoardStatusArgs,
  type BoardTaskListArgs,
  type BoardTaskShowArgs,
  type BoardHeartbeatArgs,
  type BoardMembersArgs,
  type SetPublicWhoamiArgs,
} from './board.js'

/** Platform-neutral parameter descriptor (no framework types). */
export interface MailToolParam {
  type: 'string' | 'array'
  items?: { type: 'string' }
  enum?: readonly string[]
  required?: boolean
  description?: string
}

/** One AIMail tool: semantic text + its execution handler. */
export interface MailToolDef {
  name: string
  description: string
  parameters: Record<string, MailToolParam>
  /** Execute the tool for a resolved agent context. */
  handler: (ctx: ToolCtx, args: Record<string, unknown>) => Promise<ToolResult>
}

export const MAIL_TOOLS: readonly MailToolDef[] = [
  // ── mail tools (6) ──────────────────────────────────────────
  {
    name: 'send_mail',
    description: 'Send an email from your aimail address. Returns delivery status.',
    parameters: {
      to: { type: 'string', description: 'Comma-separated recipients', required: true },
      subject: { type: 'string', required: true },
      body: { type: 'string', description: 'Markdown body', required: true },
      cc: { type: 'string', description: 'Comma-separated CC recipients' },
      attachments: { type: 'array', items: { type: 'string' }, description: 'Local file paths to attach' },
      message_id: { type: 'string', description: 'Inbound message_id to reply to (threads the reply)' },
    },
    handler: (ctx, args) => sendMail(ctx, args as unknown as SendMailArgs),
  },
  {
    name: 'manage_contacts',
    description: 'Manage your contact whitelist: check if an address is allowed, add, remove, or update entries.',
    parameters: {
      action: { type: 'string', enum: ['check', 'add', 'remove', 'update'], description: 'Action to perform', required: true },
      address: { type: 'string', description: 'Email address' },
      direction: { type: 'string', enum: ['from', 'to', 'all'], description: 'Whitelist direction' },
    },
    handler: (ctx, args) => manageContacts(ctx, args as unknown as ManageContactsArgs),
  },
  {
    name: 'contact_profile',
    description: "Look up a contact's profile.",
    parameters: {
      address: { type: 'string', description: 'Email address' },
      name: { type: 'string', description: 'Contact name' },
    },
    handler: (ctx, args) => contactProfile(ctx, args as unknown as ContactProfileArgs),
  },
  {
    name: 'set_contact_profile',
    description: "Store or update a contact's profile.",
    parameters: {
      address: { type: 'string', description: 'Email address', required: true },
      profile: { type: 'string', description: 'Profile fields as JSON string', required: true },
    },
    handler: (ctx, args) => setContactProfile(ctx, args as { address: string; profile: string }),
  },
  {
    name: 'email_summary',
    description: 'Read the stored summary of an email thread.',
    parameters: {
      message_id: { type: 'string', description: 'Thread message_id', required: true },
    },
    handler: (ctx, args) => emailSummary(ctx, args as { message_id: string }),
  },
  {
    name: 'set_email_summary',
    description: 'Save the summary of an email thread.',
    parameters: {
      message_id: { type: 'string', description: 'Thread message_id', required: true },
      summary: { type: 'string', description: 'Thread summary text (max 2000 chars)', required: true },
    },
    handler: (ctx, args) => setEmailSummary(ctx, args as { message_id: string; summary: string }),
  },

  // ── local search ─────────────────────────────────────────────
  {
    name: 'search_mail',
    description: 'Search YOUR OWN locally stored mail (offline, no network). Matches keywords in subject/body/attachment text; filter by mailbox (inbound/outbound/all), time window (since/until YYYY-MM-DD) and sender (from, substring).',
    parameters: {
      query: { type: 'string', description: 'Space-separated keywords (AND); empty = browse by filters' },
      scope: { type: 'string', enum: ['all', 'inbound', 'outbound'], description: 'Mailbox scope (default all)' },
      since: { type: 'string', description: 'Start date YYYY-MM-DD (inclusive)' },
      until: { type: 'string', description: 'End date YYYY-MM-DD (inclusive)' },
      from: { type: 'string', description: 'Sender substring filter (case-insensitive)' },
      limit: { type: 'string', description: 'Max results 1-50 (default 20)' },
    },
    handler: (ctx, args) => searchMail(ctx, args as unknown as SearchMailArgs),
  },
  // ── board tools (6) ─────────────────────────────────────────
  {
    name: 'board_status',
    description: "Get a board's working status: goal, progress per status with assignees, and blockers.",
    parameters: {
      board: { type: 'string', description: 'Board ID (b_ prefix)', required: true },
    },
    handler: (ctx, args) => boardStatus(ctx, args as unknown as BoardStatusArgs),
  },
  {
    name: 'board_task_list',
    description: "List a board's tasks, optionally filtered by status or assignee.",
    parameters: {
      board: { type: 'string', description: 'Board ID (b_ prefix)', required: true },
      status: { type: 'string', description: 'Filter by task status' },
      assignee: { type: 'string', description: 'Filter by assignee email' },
    },
    handler: (ctx, args) => boardTaskList(ctx, args as unknown as BoardTaskListArgs),
  },
  {
    name: 'board_task_show',
    description: "Show one task's full details, including parent-task context.",
    parameters: {
      task_id: { type: 'string', description: 'Task ID (t_<board>_<id>)', required: true },
    },
    handler: (ctx, args) => boardTaskShow(ctx, args as unknown as BoardTaskShowArgs),
  },
  {
    name: 'board_heartbeat',
    description: 'Signal your task is still in progress (a ready task advances to running).',
    parameters: {
      task_id: { type: 'string', description: 'Task ID (t_<board>_<id>)', required: true },
      note: { type: 'string', description: 'Progress note (optional)' },
    },
    handler: (ctx, args) => boardHeartbeat(ctx, args as unknown as BoardHeartbeatArgs),
  },
  {
    name: 'board_members',
    description: "List a board's members and their roles.",
    parameters: {
      board: { type: 'string', description: 'Board ID (b_ prefix)', required: true },
      email: { type: 'string', description: 'Filter by member email' },
    },
    handler: (ctx, args) => boardMembers(ctx, args as unknown as BoardMembersArgs),
  },
  {
    name: 'set_public_whoami',
    description: 'Set the public identity card returned for stranger WHOAMI queries.',
    parameters: {
      text: { type: 'string', description: 'Public identity text', required: true },
    },
    handler: (ctx, args) => setPublicWhoami(ctx, args as unknown as SetPublicWhoamiArgs),
  },
]
