/**
 * pi-aimail tools — registers the 12 AIMail tools by iterating the
 * SINGLE MAIL_TOOLS source from @aimail/mail-core (same as dsh/openclaw:
 * semantic text defined once; the adapter only binds platform identity).
 *
 * pi ToolDefinition: parameters are TypeBox; execute returns
 * AgentToolResult { content, details } — plain values are wrapped via
 * textResult-equivalent here (pi has no jsonResult helper export, so we
 * build the shape directly).
 */
import { Type, type TSchema } from 'typebox'
import { MAIL_TOOLS, type MailToolParam } from '@aimail/mail-core'
import { resolveConfig } from './identity.js'
import { setAgentModel } from '@aimail/mail-core'

/** pi AgentToolResult content block. */
interface TextBlock {
  type: 'text'
  text: string
}

interface PiToolResult {
  content: TextBlock[]
  details: unknown
  isError?: boolean
}

/** Translate one neutral MailToolParam into a TypeBox schema. */
export function toTypeBoxParam(p: MailToolParam): TSchema {
  let base: TSchema
  if (p.type === 'array') {
    base = Type.Array(Type.String())
  } else if (p.enum && p.enum.length > 0) {
    base = Type.Union([...p.enum.map(e => Type.Literal(e))])
  } else {
    base = Type.String()
  }
  if (p.description) {
    base = { ...base, description: p.description }
  }
  return p.required === true ? base : Type.Optional(base)
}

/** Build a TypeBox object schema from a MailToolDef's parameters record. */
export function toTypeBoxParams(
  params: Record<string, MailToolParam>,
): TSchema {
  const properties: Record<string, TSchema> = {}
  for (const [key, p] of Object.entries(params)) {
    properties[key] = toTypeBoxParam(p)
  }
  return Type.Object(properties, { additionalProperties: false })
}

/** Wrap a mail-core ToolResult into pi's AgentToolResult shape. */
function toPiResult(result: Record<string, unknown>): PiToolResult {
  return {
    content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
    details: result,
    isError: result['success'] === false,
  }
}

/**
 * Build the 12 tool definitions for pi's registerTool(). Bare names — the
 * SKILL.md bare names resolve exactly on all platforms.
 */
export function buildPiTools(): Array<{
  name: string
  label: string
  description: string
  parameters: TSchema
  execute: (
    toolCallId: string,
    params: unknown,
    signal: AbortSignal | undefined,
    ctx: unknown,
  ) => Promise<PiToolResult>
}> {
  return MAIL_TOOLS.map(tool => ({
    name: tool.name,
    label: tool.name,
    description: tool.description,
    parameters: toTypeBoxParams(tool.parameters),
    async execute(_toolCallId, params, _signal, ctx) {
      // Primary model at call time (pi ExtensionContext.model.id)
      const m = (ctx as { model?: { id?: string } } | undefined)?.model
      setAgentModel(m?.id)
      const cfg = await resolveConfig()
      const result = await tool.handler(
        { systemId: cfg.system_id, email: cfg.email },
        (params ?? {}) as Record<string, unknown>,
      )
      return toPiResult(result)
    },
  }))
}
