/**
 * openclaw-aimail tools — registers the 15 AIMail tools by iterating the
 * SINGLE AGENT_TOOL_DEFS source from @aimail/mail-core (D7: semantic text defined
 * once; each adapter only binds the platform execute/identity context).
 *
 * Tool parameters are translated from the platform-neutral MailToolParam
 * shape to TypeBox schemas at registration time (mirrors dsh's toDshParam).
 */
import * as fs from 'node:fs'
import * as path from 'node:path'
import { AGENT_TOOL_DEFS, setToolsetProvider, toTypeBoxParams } from '../core/index.js'
import { SYSTEM_TOOL_DESCRIPTION, SYSTEM_TOOL_PARAMS, pinWorkspaceToBinding, runSystemTool } from '../runtime/index.js'
import { jsonResult } from 'openclaw/plugin-sdk/tool-results'
import type {
  AnyAgentTool,
  OpenClawPluginToolContext,
} from 'openclaw/plugin-sdk/plugin-entry'
import { resolveConfigForAgent } from './identity.js'
import { setAgentModel } from '../core/index.js'


/**
 * Tool factory: identity available at assembly time (ctx.agentId), bound to
 * the same mail-core handlers as dsh. Bare names — no prefix (SKILL.md bare
 * names resolve exactly on both platforms).
 */
export function createMailTools(
  ctx: OpenClawPluginToolContext,
  workspaceFor?: (agentId: string) => string,
): AnyAgentTool[] {
  const agentId = ctx.agentId ?? 'main'
  const workspace = workspaceFor ? workspaceFor(agentId) : process.cwd()
  // OpenClaw's agentId is the isolated workspace/container; the address
  // binding points at that workspace, while the path is the agent workspace.
  const workspaceRef = { id: agentId, path: workspace, title: agentId }
  const tools: AnyAgentTool[] = AGENT_TOOL_DEFS.map(tool => ({
    name: tool.name,
    label: tool.name,
    description: tool.description,
    parameters: toTypeBoxParams(tool.parameters),
    async execute(_toolCallId, params) {
      // Primary model at call time (runtime-supplied metadata, informational)
      const am = (ctx as { activeModel?: { modelId?: string; modelRef?: string } }).activeModel
      setAgentModel(am?.modelId ?? am?.modelRef)
      const args = (params ?? {}) as Record<string, unknown>
      const needsConfig = tool.name !== 'aimail_address'
      const cfg = needsConfig ? await resolveConfigForAgent(ctx.agentId) : undefined
      const result = await tool.handler(
        cfg ? { systemId: cfg.system_id, email: cfg.email } : { systemId: '' },
        args,
      )
      if (tool.name === 'aimail_address' && result.success && args.action === 'bind' && args.email) {
        await pinWorkspaceToBinding(String(args.email), workspaceRef)
      }
      return jsonResult(result)
    },
  }))
  tools.push({
    name: 'aimail_system',
    label: 'aimail_system',
    description: SYSTEM_TOOL_DESCRIPTION,
    parameters: toTypeBoxParams(SYSTEM_TOOL_PARAMS),
    async execute(_toolCallId: string, params: unknown) {
      fs.mkdirSync(workspace, { recursive: true })
      const r = await runSystemTool((params ?? {}) as Record<string, unknown>, {
        defaultWorkspace: async () => workspaceRef,
        ensureWorkspace: async (p) => ({ id: agentId, path: p, title: agentId }),
        systemHome: () => path.join(process.env.HOME || '', '.openclaw'),
      })
      return jsonResult(r)
    },
  })
  // TOOLSET_LIST: host actual state (same semantics as SOUL/SKILLS). The
  // openclaw plugin SDK exposes no tool-introspection API on the plugin
  // entry, so the adapter reports the tools IT actually produced when the
  // host invoked this registration factory — the real registration fact,
  // not a core constant fallback. Host never invoking the factory (tools not
  // registered) ⇒ provider never set ⇒ empty.
  setToolsetProvider(() => tools.map(t => t.name))
  return tools
}
