/**
 * openclaw-aimail tools — registers the 12 AgentMail tools by iterating the
 * SINGLE MAIL_TOOLS source from @aimail/mail-core (D7: semantic text defined
 * once; each adapter only binds the platform execute/identity context).
 *
 * Tool parameters are translated from the platform-neutral MailToolParam
 * shape to TypeBox schemas at registration time (mirrors dsh's toDshParam).
 */
import { type TSchema } from 'typebox';
import { type MailToolParam } from '@aimail/mail-core';
import type { AnyAgentTool, OpenClawPluginToolContext } from 'openclaw/plugin-sdk/plugin-entry';
/** Translate one neutral MailToolParam into a TypeBox schema. */
export declare function toTypeBoxParam(p: MailToolParam): TSchema;
/** Build a TypeBox object schema from a MailToolDef's parameters record. */
export declare function toTypeBoxParams(params: Record<string, MailToolParam>): TSchema;
/**
 * Tool factory: identity available at assembly time (ctx.agentId), bound to
 * the same mail-core handlers as dsh. Bare names — no prefix (SKILL.md bare
 * names resolve exactly on both platforms).
 */
export declare function createMailTools(ctx: OpenClawPluginToolContext): AnyAgentTool[];
//# sourceMappingURL=tools.d.ts.map