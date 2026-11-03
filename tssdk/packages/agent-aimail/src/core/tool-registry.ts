/**
 * MAIL_TOOLS — the 15 AIMail tool semantic definitions, projected from the
 * shared SSOT spec.
 *
 * Single source of truth (2026-11-03): `pysdk/tool-spec.json` — one text,
 * three projections:
 *   - TS (this file):  scripts/gen-tool-spec.mjs emits src/generated/tool-spec.ts
 *                      (build artifact), MAIL_TOOLS = projection + handler binding.
 *   - Python MCP:      pysdk/tool_spec.mcp_tools() → aimail_mcp_server.TOOLS
 *   - hermes adapter:  pysdk/tool_spec.hermes_schemas() → registry.register schemas
 * Drift is structurally impossible; test/core/tool-registry.test.ts asserts
 * this projection equals the spec file (and the Py side asserts its two
 * projections against the same file).
 *
 * Parameter schema: the spec stores the platform-neutral JSON Schema form
 * (`properties` + `required` array); this file flattens it to the
 * `MailToolParam` shape the platform adapters have always consumed (dsh
 * `toDshParam`, openclaw/pi `toTypeBoxParams`) — the adapters are untouched.
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
  boardRoles,
  type BoardStatusArgs,
  type BoardTaskListArgs,
  type BoardTaskShowArgs,
  type BoardHeartbeatArgs,
  type BoardMembersArgs,
  type BoardRolesArgs,
} from './board.js'
import { aimailAddressTool, roleCalibrator, type AimailAddressToolArgs } from './tools.js'
import { TOOL_SPEC, type ToolSpecEntry } from '../generated/tool-spec.js'

/** Platform-neutral parameter descriptor (no framework types). */
export interface MailToolParam {
  type: 'string' | 'array' | 'integer' | 'number' | 'boolean'
  items?: { type: 'string' }
  enum?: readonly string[]
  default?: string | number | boolean
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

/** Execution handlers, bound by tool name (the only TS-side data). */
const HANDLERS: Record<string, (ctx: ToolCtx, args: Record<string, unknown>) => Promise<ToolResult>> = {
  send_mail: (ctx, args) => sendMail(ctx, args as unknown as SendMailArgs),
  manage_contacts: (ctx, args) => manageContacts(ctx, args as unknown as ManageContactsArgs),
  contact_profile: (ctx, args) => contactProfile(ctx, args as unknown as ContactProfileArgs),
  set_contact_profile: (ctx, args) => setContactProfile(ctx, args as { address: string; profile: string }),
  email_summary: (ctx, args) => emailSummary(ctx, args as { message_id: string }),
  set_email_summary: (ctx, args) => setEmailSummary(ctx, args as { message_id: string; summary: string }),
  search_mail: (ctx, args) => searchMail(ctx, args as unknown as SearchMailArgs),
  board_status: (ctx, args) => boardStatus(ctx, args as unknown as BoardStatusArgs),
  board_task_list: (ctx, args) => boardTaskList(ctx, args as unknown as BoardTaskListArgs),
  board_task_show: (ctx, args) => boardTaskShow(ctx, args as unknown as BoardTaskShowArgs),
  board_heartbeat: (ctx, args) => boardHeartbeat(ctx, args as unknown as BoardHeartbeatArgs),
  board_members: (ctx, args) => boardMembers(ctx, args as unknown as BoardMembersArgs),
  board_roles: (ctx, args) => boardRoles(ctx, args as unknown as BoardRolesArgs),
  aimail_address: (_ctx, args) => aimailAddressTool(args as unknown as AimailAddressToolArgs),
  role_calibrator: (ctx) => roleCalibrator(ctx),
}

/** Flatten one spec entry (JSON Schema form) into the adapter-facing shape. */
function toMailToolDef(entry: ToolSpecEntry): MailToolDef {
  const handler = HANDLERS[entry.name]
  if (!handler) throw new Error(`tool spec entry has no handler: ${entry.name}`)
  const parameters: Record<string, MailToolParam> = {}
  const required = new Set(entry.parameters.required)
  for (const [key, p] of Object.entries(entry.parameters.properties)) {
    const param: MailToolParam = { type: p.type }
    if (p.items) param.items = { type: p.items.type }
    if (p.enum !== undefined) param.enum = [...p.enum]
    if (p.default !== undefined) param.default = p.default
    if (p.description !== undefined) param.description = p.description
    if (required.has(key)) param.required = true
    parameters[key] = param
  }
  return { name: entry.name, description: entry.description, parameters, handler }
}

export const MAIL_TOOLS: readonly MailToolDef[] = TOOL_SPEC.map(toMailToolDef)
