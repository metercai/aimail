/**
 * pi-aimail identity — resolves the AgentMail binding for a pi agent.
 *
 * Identity chain (single source of truth, mirrors openclaw-aimail):
 *   ~/.pi/.agentmail pointer ({system_id, email})
 *   → system_id scoped → @aimail/mail-core loadConfigByAgentId/loadConfigByEmail
 *
 * Outbound X-AIMail-Agent: walk up from this module to the installed pi
 * package.json (@earendil-works/pi-coding-agent); detect, never guess.
 */
import * as fs from 'node:fs';
import * as path from 'node:path';
import { loadConfigByAgentId, loadConfigByEmail, setAgentIdentity } from '@aimail/mail-core';
const POINTER_PATH = path.join(process.env.HOME ?? process.env.USERPROFILE ?? '', '.pi', '.agentmail');
/** Outbound X-AIMail-Agent identity (cached on success). */
let _identity = '';
export function agentIdentity() {
    if (_identity)
        return _identity;
    try {
        let dir = path.dirname(new URL(import.meta.url).pathname);
        for (let i = 0; i < 8; i++) {
            for (const pkg of ['@earendil-works/pi-coding-agent', 'pi']) {
                const p = path.join(dir, 'node_modules', pkg, 'package.json');
                if (fs.existsSync(p)) {
                    const v = String(JSON.parse(fs.readFileSync(p, 'utf-8')).version ?? '');
                    if (v) {
                        _identity = `pi/${v}`;
                        return _identity;
                    }
                }
            }
            const parent = path.dirname(dir);
            if (parent === dir)
                break;
            dir = parent;
        }
    }
    catch {
        /* fallthrough */
    }
    return 'pi/unknown';
}
/** Install the outbound identity once at extension load. */
export function initIdentity() {
    setAgentIdentity(agentIdentity());
}
/** Read the ~/.pi/.agentmail pointer. Missing/corrupt → {}. */
export function readPointer() {
    try {
        const raw = fs.readFileSync(POINTER_PATH, 'utf-8');
        const parsed = JSON.parse(raw);
        if (parsed && typeof parsed === 'object')
            return parsed;
    }
    catch {
        /* missing or unreadable → empty pointer */
    }
    return {};
}
/**
 * Resolve the AgentMail config for the running pi agent.
 * Order: pointer email (system-scoped) → agent_id 'main' within the
 * pointer's system. Throws loud when unbound.
 */
export async function resolveConfig() {
    const ptr = readPointer();
    const systemId = ptr.system_id ?? '';
    if (ptr.email) {
        const byEmail = await loadConfigByEmail(ptr.email, systemId);
        if (byEmail)
            return byEmail;
    }
    if (systemId) {
        const byAgent = await loadConfigByAgentId(systemId, 'main');
        if (byAgent)
            return byAgent;
    }
    throw new Error(`agentmail not configured for pi — no binding for pointer ${POINTER_PATH} (email ${ptr.email || '-'}, system ${systemId || '-'})`);
}
//# sourceMappingURL=identity.js.map