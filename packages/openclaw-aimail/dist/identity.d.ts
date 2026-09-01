import { type AgentConfig } from '@aimail/mail-core';
export interface SystemPointer {
    system_id?: string;
    email?: string;
}
export declare function agentIdentity(): string;
/** Read the ~/.openclaw/.agentmail pointer. Missing/corrupt → {}. */
export declare function readPointer(): Promise<SystemPointer>;
/**
 * Resolve the AgentMail config for an openclaw agent id.
 * Throws when the pointer is missing or the agent is unbound.
 */
export declare function resolveConfigForAgent(agentId: string | undefined): Promise<AgentConfig>;
//# sourceMappingURL=identity.d.ts.map