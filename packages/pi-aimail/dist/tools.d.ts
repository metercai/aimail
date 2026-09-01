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
import { type TSchema } from 'typebox';
import { type MailToolParam } from '@aimail/mail-core';
/** pi AgentToolResult content block. */
interface TextBlock {
    type: 'text';
    text: string;
}
interface PiToolResult {
    content: TextBlock[];
    details: unknown;
    isError?: boolean;
}
/** Translate one neutral MailToolParam into a TypeBox schema. */
export declare function toTypeBoxParam(p: MailToolParam): TSchema;
/** Build a TypeBox object schema from a MailToolDef's parameters record. */
export declare function toTypeBoxParams(params: Record<string, MailToolParam>): TSchema;
/**
 * Build the 12 tool definitions for pi's registerTool(). Bare names — the
 * SKILL.md bare names resolve exactly on all platforms.
 */
export declare function buildPiTools(): Array<{
    name: string;
    label: string;
    description: string;
    parameters: TSchema;
    execute: (toolCallId: string, params: unknown, signal: AbortSignal | undefined, ctx: unknown) => Promise<PiToolResult>;
}>;
export {};
//# sourceMappingURL=tools.d.ts.map