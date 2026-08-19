/**
 * @meterwei/tool-mail — AgentMail 12 tools (bare names) for the mail
 * preset. Names/descriptions/params mirror tools/amail_mcp_server.py
 * (agentmail repo); execution calls @meterwei/mail-core.
 *
 * Identity: exec.agent.id (dsh session uuid) → ctx.mail.resolveCtx →
 * agentmail.json binding. Unbound sessions fail loud.
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type JsonValue } from '@deepseek-ai/dsh-tools'
import {
  sendMail,
  manageContacts,
  contactProfile,
  setContactProfile,
  emailSummary,
  setEmailSummary,
  boardStatus,
  boardTaskList,
  boardTaskShow,
  boardHeartbeat,
  boardMembers,
  setPublicWhoami,
  setAgentIdentity,
  type ToolCtx,
} from '@meterwei/mail-core'
import type { MailService } from '@meterwei/mail'

export const name = 'tool-mail'
export const inject = ['tools', 'mail']

function textRender(_args: unknown, value: unknown): Array<{ type: 'text'; text: string }> {
  return [{ type: 'text', text: JSON.stringify(value) }]
}

const jsonOutput = {
  schema: { type: 'json' as const },
  render: textRender,
}

/** ToolResult → JsonValue (output.schema contract). */
const run = <T>(p: Promise<T>): Promise<JsonValue> => p as unknown as Promise<JsonValue>

export function apply(ctx: Context, config: { identity?: string } = {}): void {
  const mail = ctx.get('mail') as MailService | undefined
  if (mail === undefined) {
    throw new Error('tool-mail requires the mail service: mount @meterwei/mail first')
  }
  if (config.identity) setAgentIdentity(config.identity)

  const resolve = async (exec: { agent?: { id?: string } | null }): Promise<ToolCtx> => {
    const sessionId = String(exec.agent?.id ?? '')
    return mail.resolveCtx(sessionId)
  }

  // ── mail tools (6) ──────────────────────────────────────────
  ctx.tools.register(defineTool({
    name: 'send_mail',
    description: 'Send an email from your agentmail address. Returns delivery status.',
    parameters: {
      to: { type: 'string', description: 'Comma-separated recipients', required: true },
      subject: { type: 'string', required: true },
      body: { type: 'string', description: 'Markdown body', required: true },
      cc: { type: 'string', description: 'Comma-separated CC recipients' },
      attachments: { type: 'array', items: { type: 'string' }, description: 'Local file paths to attach' },
      message_id: { type: 'string', description: 'Inbound message_id to reply to (threads the reply)' },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(sendMail(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'manage_contacts',
    description: 'Manage your contact whitelist: check if an address is allowed, add, remove, or update entries.',
    parameters: {
      action: { type: 'string', enum: ['check', 'add', 'remove', 'update'], description: 'Action to perform', required: true },
      address: { type: 'string', description: 'Email address' },
      direction: { type: 'string', enum: ['from', 'to', 'all'], description: 'Whitelist direction' },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(manageContacts(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'contact_profile',
    description: "Look up a contact's profile.",
    parameters: {
      address: { type: 'string', description: 'Email address' },
      name: { type: 'string', description: 'Contact name' },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(contactProfile(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'set_contact_profile',
    description: "Store or update a contact's profile.",
    parameters: {
      address: { type: 'string', description: 'Email address', required: true },
      profile: { type: 'string', description: 'Profile fields as JSON string', required: true },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(setContactProfile(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'email_summary',
    description: 'Read the stored summary of an email thread.',
    parameters: {
      message_id: { type: 'string', description: 'Thread message_id', required: true },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(emailSummary(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'set_email_summary',
    description: 'Save the summary of an email thread.',
    parameters: {
      message_id: { type: 'string', description: 'Thread message_id', required: true },
      summary: { type: 'string', description: 'Thread summary text (max 2000 chars)', required: true },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(setEmailSummary(await resolve(exec), args))
    },
  }))

  // ── board tools (6) ─────────────────────────────────────────
  ctx.tools.register(defineTool({
    name: 'board_status',
    description: "Get a board's working status: goal, progress per status with assignees, and blockers.",
    parameters: {
      board: { type: 'string', description: 'Board ID (b_ prefix)', required: true },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(boardStatus(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'board_task_list',
    description: "List a board's tasks, optionally filtered by status or assignee.",
    parameters: {
      board: { type: 'string', description: 'Board ID (b_ prefix)', required: true },
      status: { type: 'string', description: 'Filter by task status' },
      assignee: { type: 'string', description: 'Filter by assignee email' },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(boardTaskList(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'board_task_show',
    description: "Show one task's full details, including parent-task context.",
    parameters: {
      task_id: { type: 'string', description: 'Task ID (t_<board>_<id>)', required: true },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(boardTaskShow(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'board_heartbeat',
    description: 'Signal your task is still in progress (a ready task advances to running).',
    parameters: {
      task_id: { type: 'string', description: 'Task ID (t_<board>_<id>)', required: true },
      note: { type: 'string', description: 'Progress note (optional)' },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(boardHeartbeat(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'board_members',
    description: "List a board's members and their roles.",
    parameters: {
      board: { type: 'string', description: 'Board ID (b_ prefix)', required: true },
      email: { type: 'string', description: 'Filter by member email' },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(boardMembers(await resolve(exec), args))
    },
  }))

  ctx.tools.register(defineTool({
    name: 'set_public_whoami',
    description: 'Set the public identity card returned for stranger WHOAMI queries.',
    parameters: {
      text: { type: 'string', description: 'Public identity text', required: true },
    },
    output: jsonOutput,
    async execute(args, exec) {
      return run(setPublicWhoami(await resolve(exec), args))
    },
  }))
}
