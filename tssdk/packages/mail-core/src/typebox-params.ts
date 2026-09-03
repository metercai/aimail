/**
 * Platform-neutral MailToolParam → TypeBox schema translation.
 *
 * Shared by openclaw-aimail and pi-aimail (their tool registries both
 * translate the single MAIL_TOOLS source to TypeBox at assembly time;
 * the implementations were duplicated line-for-line — AUDIT-1 E6).
 */
import { Type, type TSchema } from 'typebox'
import type { MailToolParam } from './tool-registry.js'

/** Translate one neutral MailToolParam into a TypeBox schema. */
export function toTypeBoxParam (p: MailToolParam): TSchema {
  let base: TSchema
  if (p.type === 'array') {
    base = Type.Array(Type.String())
  } else if (p.enum && p.enum.length > 0) {
    base = Type.Union([...p.enum.map(e => Type.Literal(e))])
  } else {
    base = Type.String()
  }
  if (p.description) {
    base = { ...base, description: p.description }
  }
  return p.required === true ? base : Type.Optional(base)
}

/** Build a TypeBox object schema from a MailToolDef's parameters record. */
export function toTypeBoxParams (
  params: Record<string, MailToolParam>,
): TSchema {
  const properties: Record<string, TSchema> = {}
  for (const [key, p] of Object.entries(params)) {
    properties[key] = toTypeBoxParam(p)
  }
  return Type.Object(properties, { additionalProperties: false })
}
