/**
 * dsh-only `aimail_system` tool — registers the onboarding logic from
 * system-core (kept separate so unit tests do not need the dsh-tools runtime).
 */
import type { Context } from '@deepseek-ai/cordis'
import { defineTool, type ParameterPropertySpec } from '@deepseek-ai/dsh-tools'

import { provisionSystem, type Args } from './system-core.js'

export const name = 'mail-system'
export const inject = ['tools']

const params: Record<string, ParameterPropertySpec> = {
  action: { type: 'string', enum: ['bind', 'status'], description: 'bind: onboard; status: read current system', required: true } as ParameterPropertySpec,
  code: { type: 'string', description: 'One-time system activation code (bind only)' } as ParameterPropertySpec,
  gateway_url: { type: 'string', description: 'AIMail gateway URL; required on first onboarding' } as ParameterPropertySpec,
  system_name: { type: 'string', description: 'Shared-domain system name; exactly one of system_name/domain when the code is unbound' } as ParameterPropertySpec,
  domain: { type: 'string', description: 'Dedicated domain (base may have multiple); exactly one when unbound' } as ParameterPropertySpec,
  manager: { type: 'string', description: 'Optional system default manager; defaults to the code recipient' } as ParameterPropertySpec,
}

export function apply(ctx: Context): void {
  ctx.tools.register(defineTool({
    name: 'aimail_system',
    description: 'Onboard this dsh installation to an AIMail system using a one-time system activation code (no CLI). Provide exactly one of system_name/domain when the code is not bound. On success the system config is written and the default worker agent@domain / agent.system_name@shared is bound to the DSH default workspace.',
    parameters: params,
    output: { schema: { type: 'json' as const }, render: (_a: unknown, v: unknown) => [{ type: 'text' as const, text: JSON.stringify(v) }] },
    async execute(args: unknown) {
      return await provisionSystem(ctx, (args ?? {}) as Args) as never
    },
  }))
}
