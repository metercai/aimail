import * as fs from 'node:fs';
import * as path from 'node:path';
import { verifySignature, processInboundMail, routeAddressFromHeaders } from '@aimail/mail-core';
import { resolveByRecipient } from '@aimail/mail';
import { readPointer } from './identity.js';
export const INBOUND_PATH = '/aimail/inbound';
function writeJson(res, code, body) {
    res.writeHead(code, { 'Content-Type': 'application/json' });
    res.end(JSON.stringify(body));
}
function readBody(req) {
    return new Promise((resolve, reject) => {
        const chunks = [];
        req.on('data', (c) => chunks.push(c));
        req.on('end', () => resolve(Buffer.concat(chunks)));
        req.on('error', reject);
    });
}
/**
 * Deliver an enriched inbound payload to the owning agent's session via the
 * gateway's internal /hooks/agent endpoint (loopback + hook token; fixed
 * sessionKey so all mail converges on one agent session; deliver:false —
 * the agent replies via send_mail). subagent.run/chat.send were tried first
 * historically but require operator.write scope the plugin does not have.
 */
async function deliverToAgent(api, opts) {
    void api;
    const hooksToken = readHooksToken();
    const r = await fetch('http://127.0.0.1:18789/hooks/agent', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            ...(hooksToken ? { Authorization: `Bearer ${hooksToken}` } : {}),
        },
        body: JSON.stringify({
            message: opts.message,
            name: 'agentmail',
            sessionKey: 'agent:main:hook:amail',
            deliver: false,
        }),
    });
    if (!r.ok) {
        return { status: 'dispatch_failed', detail: `hooks/agent HTTP ${r.status}` };
    }
    return { status: 'delivered', detail: 'hooks/agent accepted' };
}
function readHooksToken() {
    try {
        const home = process.env.HOME ?? process.env.USERPROFILE ?? '';
        const cfgPath = path.join(home, '.openclaw', 'openclaw.json');
        const cfg = JSON.parse(fs.readFileSync(cfgPath, 'utf-8'));
        return String(cfg.hooks?.token ?? '');
    }
    catch {
        return '';
    }
}
export function createInboundHandler(api) {
    return async (req, res) => {
        try {
            if (req.method !== 'POST') {
                writeJson(res, 405, { status: 'method_not_allowed' });
                return;
            }
            const rawBody = await readBody(req);
            let payload;
            try {
                payload = JSON.parse(rawBody.toString('utf-8'));
            }
            catch {
                writeJson(res, 400, { status: 'bad_json' });
                return;
            }
            // Inbound routing (Q3 — mirror Python bridge routing): the per-delivery
            // target is authoritative. The bridge injects X-AIMail-Email (legacy
            // X-Amail-Email fallback) on each single-delivery POST; payload.to is
            // the FILTERED full list (external recipients first), so to[0] is often
            // an external address. Use the header when present; only iterate toRaw
            // when the header is absent (batch deliveries carry no such header).
            const headers = {
                ...req.headers,
                ...(payload.headers ?? {}),
            };
            const routeAddr = routeAddressFromHeaders(headers);
            const toRaw = Array.isArray(payload.to)
                ? payload.to
                : typeof payload.to === 'string'
                    ? [payload.to]
                    : [];
            const routeCandidates = routeAddr ? [routeAddr] : toRaw;
            let agentAddr = '';
            let cfg;
            for (const t of routeCandidates) {
                const addr = String(t).trim();
                if (!addr.includes('@'))
                    continue;
                const c = await resolveByRecipient(addr);
                if (c) {
                    cfg = c;
                    agentAddr = addr;
                    break;
                }
            }
            if (!cfg) {
                // Fall back to the pointer email when no recipient matched (e.g. the
                // bridge forwarded a bare address) — keep no_agent intercept semantics.
                const ptr = await readPointer();
                if (ptr.email)
                    cfg = await resolveByRecipient(ptr.email);
                if (cfg && !agentAddr)
                    agentAddr = ptr.email ?? '';
            }
            if (!cfg) {
                writeJson(res, 200, {
                    status: 'no_agent',
                    detail: `no binding for ${routeAddr || toRaw.join(',')}`,
                });
                return;
            }
            // HMAC verify (per-address webhook_secret)
            const sig = req.headers['x-webhook-signature'] ?? '';
            if (!verifySignature(rawBody, sig, cfg.webhook_secret ?? '')) {
                writeJson(res, 401, { status: 'bad_signature' });
                return;
            }
            // TS preprocess chain (13 steps) + ping/pong intercept
            const result = await processInboundMail(payload, headers, { systemId: cfg.system_id, email: cfg.email });
            if (result === null) {
                writeJson(res, 200, { status: 'intercepted' });
                return;
            }
            // Agent turn with the full enriched JSON payload
            const agentId = cfg.agent_id || 'main';
            const out = await deliverToAgent(api, {
                agentId,
                message: JSON.stringify({ ...result, to: agentAddr }),
            });
            writeJson(res, 200, out);
        }
        catch (e) {
            writeJson(res, 500, {
                status: 'error',
                detail: e instanceof Error ? e.message : String(e),
            });
        }
    };
}
//# sourceMappingURL=inbound.js.map