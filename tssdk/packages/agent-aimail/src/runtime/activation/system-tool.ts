import type { MailToolParam } from '../../core/tool-registry.js'
import { provisionSystem, type SystemArgs, type SystemWorkspaceHost } from './system.js'
import { loadAgentConfig } from '../../core/index.js'
import { fireBindingCreated } from '../../core/binding-hooks.js'

export const SYSTEM_TOOL_PARAMS: Record<string, MailToolParam> = {
  action: { type: 'string', enum: ['bind', 'status'], required: true, description: 'bind: onboard; status: read current system' },
  code: { type: 'string', description: 'One-time system activation code claimed on the gateway /apply page' },
  gateway_url: { type: 'string', description: 'AIMail gateway URL; required on first onboarding' },
  system_name: { type: 'string', description: 'Shared-domain system name; exactly one of system_name/domain when the code is unbound' },
  domain: { type: 'string', description: 'Dedicated domain (base may have multiple); exactly one when unbound' },
  manager: { type: 'string', description: 'Optional system default manager; defaults to the code recipient' },
}

export const SYSTEM_TOOL_DESCRIPTION =
  'Onboard this host to an AIMail system (no CLI) with a one-time system activation code claimed on the gateway /apply page. Shared-domain: code + system_name; dedicated/independent gateway: code + domain. Exactly one of system_name/domain when the code is not bound. On success the system config is written and the default worker is bound to the host default workspace.'

export async function runSystemTool(
  args: Record<string, unknown>,
  host: SystemWorkspaceHost,
): Promise<Record<string, unknown>> {
  const r = await provisionSystem(undefined, (args ?? {}) as SystemArgs, host)
  if (r.success && r.system_id && r.default_email) {
    const cfg = await loadAgentConfig(String(r.system_id), String(r.default_email)).catch(() => undefined)
    if (cfg) await fireBindingCreated(cfg)
  }
  return r
}
