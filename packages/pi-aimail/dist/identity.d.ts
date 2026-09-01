import { type AgentConfig } from '@aimail/mail-core';
export interface SystemPointer {
    system_id?: string;
    email?: string;
}
export declare function agentIdentity(): string;
/** Install the outbound identity once at extension load. */
export declare function initIdentity(): void;
/** Read the ~/.pi/.agentmail pointer. Missing/corrupt → {}. */
export declare function readPointer(): SystemPointer;
/**
 * Resolve the AgentMail config for the running pi agent.
 * Order: pointer email (system-scoped) → agent_id 'main' within the
 * pointer's system. Throws loud when unbound.
 */
export declare function resolveConfig(): Promise<AgentConfig>;
//# sourceMappingURL=identity.d.ts.map