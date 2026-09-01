/**
 * openclaw-aimail inbound — registers an HTTP route inside the gateway
 * process (no new port; bridge push target unchanged).
 *
 * Handler chain (mirrors dsh inbound):
 *   recipient resolution (resolveByRecipient: exact → persona-strip fallback,
 *   so mail to a role alias routes to the owning agent)
 *   → verifySignature (byte-exact HMAC, mail-core)
 *   → processInboundMail (13-step chain + ping/pong intercept)
 *   → agent turn with the full enriched JSON payload (rendering parity =
 *     json.dumps equivalence, established acceptance bar).
 *
 * Delivery: api.runtime.subagent.run (session-scoped run) primary,
 * api.runtime.gateway.request (explicit agent targeting) fallback (R3).
 */
import type { IncomingMessage, ServerResponse } from 'node:http';
import type { OpenClawPluginApi } from 'openclaw/plugin-sdk/plugin-entry';
export declare const INBOUND_PATH = "/agentmail/deliver";
/** Build the inbound HTTP route handler for this plugin entry. */
export declare function createInboundHandler(api: OpenClawPluginApi): (req: IncomingMessage, res: ServerResponse) => Promise<void>;
//# sourceMappingURL=inbound.d.ts.map