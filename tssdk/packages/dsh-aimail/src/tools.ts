/**
 * dsh-aimail tools — registers the 12 AIMail bare tools for this profile.
 *
 * Semantic text (names, descriptions, parameter descriptions) comes from
 * the shared MAIL_TOOLS registry in @aimail/mail-core (single source of
 * truth, parity-tested against amail_mcp_server.py). This adapter only:
 *   - iterates MAIL_TOOLS, translating each entry to a dsh defineTool
 *   - binds execution: exec.agent.id (dsh session uuid) → ctx.mail.resolveCtx
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type JsonValue, type ParameterPropertySpec } from '@deepseek-ai/dsh-tools'
import {
  MAIL_TOOLS,
  setAgentIdentity,
  setAgentModel,
  type MailToolDef,
  type MailToolParam,
  type ToolCtx,
} from '@aimail/mail-core'
import type { MailService } from './mail-service.js'

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

/**
 * Translate the neutral MailToolParam into a dsh ParameterPropertySpec.
 * dsh requires `required?: true` (never false) and per-type literal shapes,
 * so optional fields are omitted rather than set to undefined/false.
 */
function toDshParam(p: MailToolParam): ParameterPropertySpec {
  const base: Record<string, unknown> = {}
  if (p.type === 'string') {
    base.type = 'string'
    if (p.enum !== undefined) base.enum = p.enum
  } else {
    base.type = 'array'
    if (p.items !== undefined) base.items = { type: p.items.type }
  }
  if (p.description !== undefined) base.description = p.description
  if (p.required === true) base.required = true
  return base as unknown as ParameterPropertySpec
}

export function apply(ctx: Context, config: { identity?: string } = {}): void {
  const mail = ctx.get('mail') as MailService | undefined
  if (mail === undefined) {
    throw new Error('tool-mail requires the mail service: mount dsh-aimail/mail-service first')
  }
  if (config.identity) setAgentIdentity(config.identity)
  // Primary model: same deployment default the inbound router uses for
  // agents.create() (cordis 'agentDefaultModel' service).
  const adm = ctx.get('agentDefaultModel') as { currentSelection?: () => unknown } | undefined
  const sel = adm?.currentSelection?.()
  const modelId = typeof sel === 'string'
    ? sel
    : (sel as { model?: string; id?: string } | null | undefined)?.model ?? (sel as { id?: string } | null | undefined)?.id
  if (modelId) setAgentModel(modelId)

  const resolve = async (exec: { agent?: { id?: string } | null }): Promise<ToolCtx> => {
    const sessionId = String(exec.agent?.id ?? '')
    return mail.resolveCtx(sessionId)
  }

  for (const tool of MAIL_TOOLS) {
    const { handler: _handler, ...semantic } = tool as MailToolDef
    void _handler
    // Translate the neutral parameter schema into dsh's spec shape.
    const parameters: Record<string, ParameterPropertySpec> = {}
    for (const [key, p] of Object.entries(semantic.parameters)) {
      parameters[key] = toDshParam(p)
    }
    ctx.tools.register(defineTool({
      name: semantic.name,
      description: semantic.description,
      parameters,
      output: jsonOutput,
      async execute(args, exec) {
        return run(tool.handler(await resolve(exec), args))
      },
    }))
  }
}
