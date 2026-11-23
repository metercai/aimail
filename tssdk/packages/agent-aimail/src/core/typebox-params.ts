/**
 * Platform-neutral MailToolParam → JSON Schema translation.
 *
 * Shared by openclaw-aimail and pi-aimail (their tool registries both
 * translate the single MAIL_TOOLS source at assembly time).
 *
 * REQ-11: this module must NOT import `typebox` at runtime. Pi refuses to
 * load a plugin whose `dependencies` contain a host-provided extension
 * package, while DSH does not provide `typebox` at all — a static import here
 * was loaded by every host through `core/index`, so either declaration broke
 * one side. TypeBox schemas are JSON Schema plus symbol metadata; the host
 * tool registries only need the JSON Schema shape, so this module emits plain
 * JSON Schema and the package has no runtime typebox dependency.
 */
import type { MailToolParam } from './tool-registry.js'

/** Minimal JSON Schema shape accepted by pi / openclaw tool registration. */
export interface JsonSchema {
  type?: 'object' | 'array' | 'string' | 'integer' | 'number' | 'boolean'
  properties?: Record<string, JsonSchema>
  required?: string[]
  items?: JsonSchema
  enum?: readonly (string | number | boolean)[]
  description?: string
  default?: string | number | boolean
  additionalProperties?: boolean
  [k: string]: unknown
}

/** Translate one neutral MailToolParam into a JSON Schema property. */
export function toTypeBoxParam (p: MailToolParam): JsonSchema {
  let base: JsonSchema
  if (p.type === 'array') {
    base = { type: 'array', items: { type: 'string' } }
  } else if (p.type === 'integer') {
    // 2026-11-02 L3: limit (search_mail) was exposed as a string — the model
    // was told to pass a string for a numeric field.
    base = { type: 'integer' }
  } else if (p.type === 'number') {
    base = { type: 'number' }
  } else if (p.type === 'boolean') {
    base = { type: 'boolean' }
  } else if (p.enum && p.enum.length > 0) {
    base = { type: 'string', enum: [...p.enum] }
  } else {
    base = { type: 'string' }
  }
  if (p.description) base.description = p.description
  // `default` must be passed through: otherwise the default value of the
  // schema seen by the model side is inconsistent with the Python source of
  // truth (Audit 2026-09-21: manage_contacts.direction default=all was
  // previously discarded here).
  if (p.default !== undefined) base.default = p.default
  return base
}

/** Build a JSON Schema object from a MailToolDef's parameters record. */
export function toTypeBoxParams (
  params: Record<string, MailToolParam>,
): JsonSchema {
  const properties: Record<string, JsonSchema> = {}
  const required: string[] = []
  for (const [key, p] of Object.entries(params)) {
    properties[key] = toTypeBoxParam(p)
    if (p.required === true) required.push(key)
  }
  return {
    type: 'object',
    properties,
    additionalProperties: false,
    ...(required.length > 0 ? { required } : {}),
  }
}
