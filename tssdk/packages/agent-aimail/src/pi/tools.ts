/**
 * pi-aimail tools — registers the 15 AIMail tools by iterating the
 * SINGLE AGENT_TOOL_DEFS source from @aimail/mail-core (same as dsh/openclaw:
 * semantic text defined once; the adapter only binds platform identity).
 * pi ToolDefinition: parameters are JSON Schema (REQ-11: no runtime
 * `typebox` import); execute returns AgentToolResult { content, details } —
 * plain values are wrapped via textResult-equivalent here (pi has no
 * jsonResult helper export, so we build the shape directly).
 */
import { AGENT_TOOL_DEFS, toTypeBoxParams, type JsonSchema } from '../core/index.js'
import { piAnchor, resolveConfig } from './identity.js'
import { setAgentModel } from '../core/index.js'
import { findOtherActiveAddress, pinWorkspaceToBinding } from '../runtime/index.js'
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
/** Read a host getter without letting a host-side throw break a tool call. */
function safeCall<T>(fn: () => T | undefined): T | undefined {
  try {
    return fn()
  } catch {
    return undefined
  }
}

export function buildPiTools(): Array<{
  name: string
  label: string
  description: string
  parameters: JsonSchema
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
    parameters: JsonSchema
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
      const rawArgs = (params ?? {}) as Record<string, unknown>
      // The anchor (pi: the WHOLE AGENT, `~/.pi/agent`) must be known AT activation: it is hashed
      // with the address and registered with the gateway, which re-checks it on every pull
      // (contract §7.1). It is deliberately NOT the session cwd — a cwd-anchored address would be
      // refused the moment the agent is started from another directory (owner ruling 2026-11-25).
      const args: Record<string, unknown> =
        tool.name === 'aimail_address' && rawArgs.action === 'bind'
          ? { ...rawArgs, workspacePath: piAnchor() }
          : rawArgs
      if (tool.name === 'aimail_address' && args.action === 'bind' && args.email) {
        const other = await findOtherActiveAddress(String(args.email))
        if (other) {
          return toPiResult({
            success: false, action: 'bind', email: String(args.email),
            error: `Pi is single-address: already receiving ${other}; pause/unbind it first`,
          })
        }
      }
      // Activation tools must work before a binding exists.
      const needsConfig = tool.name !== 'aimail_address'
      const cfg = needsConfig ? await resolveConfig() : undefined
      // Session identity where the host exposes it. pi 1.5.2 puts a `sessionManager` on the tool
      // context (`ReadonlySessionManager.getSessionId()/getSessionFile()`), and the bash tool
      // mirrors it into PI_SESSION_ID/PI_SESSION_FILE. Recording it on the message lets anyone
      // auditing an approval request see WHICH session asked — the background pull loop has no
      // context handle, so the reply side can only be delivered to whatever session is running.
      const sessionManager = (ctx as {
        sessionManager?: { getSessionId?: () => string; getSessionFile?: () => string | undefined }
      } | undefined)?.sessionManager
      const sessionId = safeCall(() => sessionManager?.getSessionId?.()) || ''
      const sessionKey = safeCall(() => sessionManager?.getSessionFile?.()) || ''
      const result = await tool.handler(
        cfg
          ? {
              systemId: cfg.system_id,
              email: cfg.email,
              ...(sessionId ? { sessionId } : {}),
              ...(sessionKey ? { sessionKey } : {}),
            }
          : { systemId: '' },
        args,
      )
      if (tool.name === 'aimail_address' && result.success && args.action === 'bind' && args.email) {
        // Pin the SAME anchor that was just registered: a cwd here would make the pull hash a
        // different value than the gateway stored and every pull would be refused (contract §13.3).
        const anchor = piAnchor()
        await pinWorkspaceToBinding(String(args.email), { id: anchor, path: anchor })
      }
      return toPiResult(result)
    },
  }))
  return tools
}
