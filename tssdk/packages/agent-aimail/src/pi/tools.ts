/**
 * pi-aimail tools — registers the 15 AIMail tools by iterating the
 * SINGLE AGENT_TOOL_DEFS source from @aimail/mail-core (same as dsh/openclaw:
 * semantic text defined once; the adapter only binds platform identity).
 *
 * pi ToolDefinition: parameters are TypeBox; execute returns
 * AgentToolResult { content, details } — plain values are wrapped via
 * textResult-equivalent here (pi has no jsonResult helper export, so we
 * build the shape directly).
 */
import type { TSchema } from 'typebox'
import { AGENT_TOOL_DEFS, toTypeBoxParams } from '../core/index.js'
import { resolveConfig } from './identity.js'
import { setAgentModel } from '../core/index.js'
import { pinWorkspaceToBinding } from '../runtime/index.js'

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


/** Wrap a mail-core ToolResult into pi's AgentToolResult shape. */
function toPiResult(result: Record<string, unknown>): PiToolResult {
  return {
    content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
    details: result,
    isError: result['success'] === false,
  }
}

/**
 * Build the tool definitions for pi's registerTool(). Bare names — the
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
  const tools: Array<{
    name: string
    label: string
    description: string
    parameters: TSchema
    execute: (toolCallId: string, params: unknown, signal: AbortSignal | undefined, ctx: unknown) => Promise<PiToolResult>
  }> = AGENT_TOOL_DEFS.map(tool => ({
    name: tool.name,
    label: tool.name,
    description: tool.description,
    parameters: toTypeBoxParams(tool.parameters),
    async execute(_toolCallId, params, _signal, ctx) {
      // Primary model at call time (pi ExtensionContext.model.id)
      const m = (ctx as { model?: { id?: string } } | undefined)?.model
      setAgentModel(m?.id)
      const args = (params ?? {}) as Record<string, unknown>
      // Activation tools must work before a binding exists.
      const needsConfig = tool.name !== 'aimail_address'
      const cfg = needsConfig ? await resolveConfig() : undefined
      const result = await tool.handler(
        cfg ? { systemId: cfg.system_id, email: cfg.email } : { systemId: '' },
        args,
      )
      if (tool.name === 'aimail_address' && result.success && args.action === 'bind' && args.email) {
        const cwd = (ctx as { cwd?: string } | undefined)?.cwd || process.cwd()
        await pinWorkspaceToBinding(String(args.email), { id: cwd, path: cwd })
      }
      return toPiResult(result)
    },
  }))
  return tools
}
