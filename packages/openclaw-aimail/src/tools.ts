/**
 * openclaw-aimail tools — registers the 12 AgentMail tools by iterating the
 * SINGLE MAIL_TOOLS source from @aimail/mail-core (D7: semantic text defined
 * once; each adapter only binds the platform execute/identity context).
 *
 * Tool parameters are translated from the platform-neutral MailToolParam
 * shape to TypeBox schemas at registration time (mirrors dsh's toDshParam).
 */
import { Type, type TSchema } from 'typebox'
import { MAIL_TOOLS, type MailToolParam } from '@aimail/mail-core'
import { jsonResult } from 'openclaw/plugin-sdk/tool-results'
import type {
  AnyAgentTool,
  OpenClawPluginToolContext,
} from 'openclaw/plugin-sdk/plugin-entry'
import { resolveConfigForAgent } from './identity.js'
import { setAgentModel } from '@aimail/mail-core'

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

/**
 * Tool factory: identity available at assembly time (ctx.agentId), bound to
 * the same mail-core handlers as dsh. Bare names — no prefix (SKILL.md bare
 * names resolve exactly on both platforms).
 */
export function createMailTools(
  ctx: OpenClawPluginToolContext,
): AnyAgentTool[] {
  return MAIL_TOOLS.map(tool => ({
    name: tool.name,
    label: tool.name,
    description: tool.description,
    parameters: toTypeBoxParams(tool.parameters),
    async execute(_toolCallId, params) {
      // Primary model at call time (runtime-supplied metadata, informational)
      const am = (ctx as { activeModel?: { modelId?: string; modelRef?: string } }).activeModel
      setAgentModel(am?.modelId ?? am?.modelRef)
      const cfg = await resolveConfigForAgent(ctx.agentId)
      const result = await tool.handler(
        { systemId: cfg.system_id, email: cfg.email },
        (params ?? {}) as Record<string, unknown>,
      )
      return jsonResult(result)
    },
  }))
}
