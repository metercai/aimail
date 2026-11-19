/**
 * dsh-aimail tools — registers the 15 AIMail bare tools for this profile.
 *
 * Semantic text (names, descriptions, parameter descriptions) comes from
 * the shared AIMail tool registry in @aimail/mail-core (single source of
 * truth, parity-tested against the shared tool spec in
 * pysdk/aimail_mcp_server.py). No MCP service is used by this TS adapter.
 * This adapter only:
 *   - iterates AGENT_TOOL_DEFS, translating each entry to a dsh defineTool
 *   - binds execution: exec.agent.id (dsh session uuid) → ctx.mail.resolveCtx
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type ParameterPropertySpec } from '@deepseek-ai/dsh-tools'
import {
  AGENT_TOOL_DEFS,
  setAgentIdentity,
  setAgentModel,
  setToolsetProvider,
  type MailToolDef,
  type MailToolParam,
  type ToolCtx,
  type ToolResult,
} from '../core/index.js'
import type { MailService } from './mail-service.js'
import { runAddressTool } from './address.js'

export const name = 'tool-mail'
export const inject = ['tools', 'mail']

function textRender(_args: unknown, value: unknown): Array<{ type: 'text'; text: string }> {
  return [{ type: 'text', text: JSON.stringify(value) }]
}

const jsonOutput = {
  schema: { type: 'json' as const },
  render: textRender,
}

/** JSON value — structurally identical to the host's own definition.
 *
 * dsh 0.2 moved `JsonValue` into `@deepseek-ai/dsh-util-values` and
 * `@deepseek-ai/dsh-tools` no longer re-exports it (0.1 did).  Keeping a local
 * alias avoids depending on that internal package while preserving the type
 * that shapes the render value.
 */
type JsonValue = null | boolean | number | string | JsonValue[] | { [key: string]: JsonValue }

/** ToolResult → JsonValue (output.schema contract). */
const run = <T>(p: Promise<T>): Promise<JsonValue> => p as unknown as Promise<JsonValue>

/**
 * Translate the neutral MailToolParam into a dsh ParameterPropertySpec.
 * dsh requires `required?: true` (never false) and per-type literal shapes,
 * so optional fields are omitted rather than set to undefined/false.
 */
function toDshParam(p: MailToolParam): ParameterPropertySpec {
  const base: Record<string, unknown> = {}
  switch (p.type) {
    case 'string':
      base.type = 'string'
      if (p.enum !== undefined) base.enum = p.enum
      break
    case 'integer':
      // 2026-11-02 L3 defect F: search_mail.limit used to be exposed as an
      // array (non-string ⇒ `array`), so the model's scalar limit was rejected
      // with '"limit" must be an array'.
      base.type = 'integer'
      if (p.default !== undefined) base.default = p.default
      break
    case 'number':
      base.type = 'number'
      break
    case 'boolean':
      base.type = 'boolean'
      break
    case 'array':
      base.type = 'array'
      if (p.items !== undefined) base.items = { type: p.items.type }
      break
  }
  if (p.description !== undefined) base.description = p.description
  // Audit 2026-09-21: default must also be passed through to the host schema (same standard as mail-core TypeBox conversion)
  if (p.default !== undefined) base.default = p.default
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

  const registered: string[] = []
  for (const tool of AGENT_TOOL_DEFS) {
    const { handler: _handler, ...semantic } = tool as MailToolDef
    void _handler
    // Translate the neutral parameter schema into dsh's spec shape.
    const parameters: Record<string, ParameterPropertySpec> = {}
    for (const [key, p] of Object.entries(semantic.parameters)) {
      parameters[key] = toDshParam(p)
    }
    let description = semantic.description
    if (semantic.name === 'aimail_address') {
      // dsh override: no manager; binds to the CURRENT workspace.
      parameters.action = { type: 'string', enum: ['bind', 'pause', 'resume', 'unbind', 'move'], description: 'bind: connect this mailbox to the current workspace; pause: stop receiving; resume: resume + drain pending; unbind: disconnect locally; move: move binding to workspace_path', required: true } as ParameterPropertySpec
      parameters.workspace_path = { type: 'string', description: 'Target workspace for move' } as ParameterPropertySpec
      description = 'Manage your own mailbox in this dsh profile. bind: connect the address to the current workspace. Scenario 1 (no system, shared domain): provide the address code. Scenario 6 (after system onboarding): omit the code and use the system domain key; shared-domain systems use name.system_name@shared, dedicated/independent gateways use name@domain. pause/resume/unbind/move: local. No manager parameter.'
    }
    ctx.tools.register(defineTool({
      name: semantic.name,
      description,
      parameters,
      output: jsonOutput,
      async execute(args, exec) {
        // The address lifecycle tool owns its own workspace/session resolution;
        // it must NOT be gated behind mail.resolveCtx (an unbound session has to
        // be able to bind its first address).
        if (semantic.name === 'aimail_address') {
          return run(runAddressTool(ctx, exec, args) as Promise<ToolResult>)
        }
        return run(tool.handler(await resolve(exec), args))
      },
    }))
    registered.push(semantic.name)
  }

  // System onboarding is a DSH-only tool. Register it in this same entry so
  // it is part of the agent-visible tool set (the separate `mail-system`
  // Cordis row was not exposed in the tool list on installed profiles).
  // TOOLSET_LIST: host actual state (same semantics as SOUL/SKILLS). The dsh
  // host exposes no tool-introspection API on ctx.tools, so the adapter
  // reports the tools IT actually registered in THIS apply() — the real
  // registration fact, not a core constant fallback. apply() not reached ⇒
  // provider never set ⇒ empty.
  setToolsetProvider(() => [...registered])
}
