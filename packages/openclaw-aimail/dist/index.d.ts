/**
 * openclaw-aimail — AgentMail plugin for OpenClaw.
 *
 * definePluginEntry: 12 tools (factory form, iterating MAIL_TOOLS) + inbound
 * HTTP route + register/deregister/status commands. No plugin-level config
 * schema — identity stays pointer + agentmail.json (single source of truth).
 */
import { type OpenClawPluginDefinition } from 'openclaw/plugin-sdk/plugin-entry';
declare const entry: OpenClawPluginDefinition;
export default entry;
//# sourceMappingURL=index.d.ts.map