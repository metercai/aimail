/**
 * pi-aimail tools — registers the 12 AgentMail tools by iterating the
 * SINGLE MAIL_TOOLS source from @aimail/mail-core (same as dsh/openclaw:
 * semantic text defined once; the adapter only binds platform identity).
 *
 * pi ToolDefinition: parameters are TypeBox; execute returns
 * AgentToolResult { content, details } — plain values are wrapped via
 * textResult-equivalent here (pi has no jsonResult helper export, so we
 * build the shape directly).
 */
import { Type } from 'typebox';
import { MAIL_TOOLS } from '@aimail/mail-core';
import { resolveConfig } from './identity.js';
/** Translate one neutral MailToolParam into a TypeBox schema. */
export function toTypeBoxParam(p) {
    let base;
    if (p.type === 'array') {
        base = Type.Array(Type.String());
    }
    else if (p.enum && p.enum.length > 0) {
        base = Type.Union([...p.enum.map(e => Type.Literal(e))]);
    }
    else {
        base = Type.String();
    }
    if (p.description) {
        base = { ...base, description: p.description };
    }
    return p.required === true ? base : Type.Optional(base);
}
/** Build a TypeBox object schema from a MailToolDef's parameters record. */
export function toTypeBoxParams(params) {
    const properties = {};
    for (const [key, p] of Object.entries(params)) {
        properties[key] = toTypeBoxParam(p);
    }
    return Type.Object(properties, { additionalProperties: false });
}
/** Wrap a mail-core ToolResult into pi's AgentToolResult shape. */
function toPiResult(result) {
    return {
        content: [{ type: 'text', text: JSON.stringify(result, null, 2) }],
        details: result,
        isError: result['success'] === false,
    };
}
/**
 * Build the 12 tool definitions for pi's registerTool(). Bare names — the
 * SKILL.md bare names resolve exactly on all platforms.
 */
export function buildPiTools() {
    return MAIL_TOOLS.map(tool => ({
        name: tool.name,
        label: tool.name,
        description: tool.description,
        parameters: toTypeBoxParams(tool.parameters),
        async execute(_toolCallId, params, _signal, _ctx) {
            const cfg = await resolveConfig();
            const result = await tool.handler({ systemId: cfg.system_id, email: cfg.email }, (params ?? {}));
            return toPiResult(result);
        },
    }));
}
//# sourceMappingURL=tools.js.map