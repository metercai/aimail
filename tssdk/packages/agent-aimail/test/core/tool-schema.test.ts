import { describe, expect, it } from 'vitest'
import { toTypeBoxParam, toTypeBoxParams } from '../../src/core/typebox-params.js'
import type { MailToolParam } from '../../src/core/tool-registry.js'

/**
 * REQ-11: tool parameter schemas are plain JSON Schema, not TypeBox objects.
 * The package must not require `typebox` at runtime (pi refuses a runtime
 * dependency on a host-provided extension package; dsh does not provide it).
 */
describe('tool parameter JSON Schema', () => {
  it('maps scalar/array/enum types to JSON Schema', () => {
    expect(toTypeBoxParam({ type: 'string' })).toEqual({ type: 'string' })
    expect(toTypeBoxParam({ type: 'integer' })).toEqual({ type: 'integer' })
    expect(toTypeBoxParam({ type: 'number' })).toEqual({ type: 'number' })
    expect(toTypeBoxParam({ type: 'boolean' })).toEqual({ type: 'boolean' })
    expect(toTypeBoxParam({ type: 'array', items: { type: 'string' } }))
      .toEqual({ type: 'array', items: { type: 'string' } })
    expect(toTypeBoxParam({ type: 'string', enum: ['a', 'b'] }))
      .toEqual({ type: 'string', enum: ['a', 'b'] })
  })

  it('passes description/default through', () => {
    expect(toTypeBoxParam({
      type: 'string',
      description: 'direction filter',
      default: 'all',
    })).toEqual({ type: 'string', description: 'direction filter', default: 'all' })
  })

  it('builds an object schema with a required list only', () => {
    const params: Record<string, MailToolParam> = {
      action: { type: 'string', required: true },
      email: { type: 'string', required: true },
      code: { type: 'string' },
    }
    const schema = toTypeBoxParams(params)
    expect(schema).toMatchObject({
      type: 'object',
      additionalProperties: false,
      required: ['action', 'email'],
    })
    expect(schema.properties?.action).toEqual({ type: 'string' })
    expect(schema.properties?.code).toEqual({ type: 'string' })
    expect(schema.properties?.code).not.toHaveProperty('required')
  })

  it('omits required when every parameter is optional', () => {
    expect(toTypeBoxParams({ code: { type: 'string' } }))
      .not.toHaveProperty('required')
  })
})
