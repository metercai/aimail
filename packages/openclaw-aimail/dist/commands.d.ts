import type { GatewayResponse } from '@aimail/mail-core';
import type { OpenClawPluginCommandDefinition } from 'openclaw/plugin-sdk/plugin-entry';
/** Minimal admin client surface the chains depend on (MockClient-friendly). */
export interface AdminClient {
    request(method: string, path: string, body?: Record<string, unknown>, headers?: Record<string, string>, rawBody?: Uint8Array): Promise<GatewayResponse>;
}
export interface RegisterOptions {
    systemId: string;
    email: string;
    webhookUrl: string;
    webhookSecret: string;
    managerAddress?: string;
}
export interface RegisterResult {
    api_key?: string | undefined;
    activation_code?: string;
    exists?: boolean;
}
/**
 * 4-step idempotent registration chain (port of Python register_agent_email).
 * Returns {api_key} when activation completed; {} when pending/existing.
 */
export declare function registerAgentEmail(client: AdminClient, opts: RegisterOptions): Promise<RegisterResult>;
export interface DeregisterResult {
    api_key: string;
    domain: string;
    whitelist: string;
}
/** 3-step idempotent deregistration chain (api-key → domain → whitelist). */
export declare function deregisterAgentEmail(client: AdminClient, opts: {
    systemId: string;
    email: string;
    domainAddr: string;
}): Promise<DeregisterResult>;
/** The three aimail commands (name "aimail" + subcommands). */
export declare function createAimailCommands(): OpenClawPluginCommandDefinition[];
//# sourceMappingURL=commands.d.ts.map