import { describe, expect, it } from 'vitest'

import {
  cleanSchemaArguments,
  initialSchemaArguments,
  toRjsfSchema,
  validateSchemaArguments,
} from './schema'

const schema = {
  type: 'object',
  properties: {
    a: { type: 'number', description: 'First number' },
    b: { type: 'number', description: 'Second number' },
    note: { type: 'string', default: 'sum' },
  },
  required: ['a', 'b'],
} as const

describe('MCP input schema forms', () => {
  it('uses RJSF defaults without synthesizing missing values', () => {
    expect(initialSchemaArguments(schema, { a: 1 })).toEqual({
      a: 1,
      note: 'sum',
    })
  })

  it('validates and preserves typed form data with AJV', () => {
    expect(validateSchemaArguments(schema, { a: 1.5, b: 2 })).toEqual({
      arguments: { a: 1.5, b: 2 },
      errors: [],
    })

    const result = validateSchemaArguments(schema, { a: 1 })
    expect(result.arguments).toEqual({ a: 1 })
    expect(result.errors).toEqual(
      expect.arrayContaining([
        expect.objectContaining({
          name: 'required',
          params: { missingProperty: 'b' },
        }),
      ]),
    )
  })

  it('flattens nullable unions while retaining genuine alternatives', () => {
    const nullableSchema = {
      properties: {
        limit: {
          anyOf: [{ type: 'integer' }, { type: 'null' }],
          default: 10,
        },
        start_time: {
          anyOf: [{ type: 'integer' }, { type: 'null' }],
          default: null,
        },
        filter: {
          oneOf: [{ type: 'string' }, { type: 'number' }],
        },
      },
      type: 'object',
    } as const

    expect(toRjsfSchema(nullableSchema)).toMatchObject({
      properties: {
        limit: { type: 'integer', default: 10 },
        start_time: { type: 'integer' },
        filter: {
          oneOf: [{ type: 'string' }, { type: 'number' }],
        },
      },
    })
  })

  it('builds defaults and validates nested objects and arrays', () => {
    const metricSchema = {
      properties: {
        region: { type: 'string' },
        request: {
          properties: {
            Instances: {
              anyOf: [
                {
                  items: {
                    properties: {
                      Dimensions: {
                        items: {
                          properties: {
                            Name: { type: 'string' },
                            Value: { type: 'string' },
                          },
                          required: ['Name', 'Value'],
                          type: 'object',
                        },
                        type: 'array',
                      },
                    },
                    required: ['Dimensions'],
                    type: 'object',
                  },
                  type: 'array',
                },
                { type: 'null' },
              ],
              default: null,
            },
            MetricName: { type: 'string' },
            Namespace: { type: 'string' },
            Period: { type: 'string', default: '1m' },
            StartTime: {
              anyOf: [{ type: 'integer' }, { type: 'null' }],
              default: null,
            },
            SubNamespace: { type: 'string' },
          },
          required: ['MetricName', 'Namespace', 'SubNamespace'],
          type: 'object',
        },
      },
      required: ['region', 'request'],
      type: 'object',
    } as const

    expect(initialSchemaArguments(metricSchema)).toEqual({
      request: { Period: '1m' },
    })

    const values = {
      region: 'cn-beijing',
      request: {
        Instances: [
          {
            Dimensions: [{ Name: 'ResourceID', Value: 'mysql-c31d' }],
          },
        ],
        MetricName: 'ThreadsConnected',
        Namespace: 'VCM_RDS_MySQL',
        Period: '1m',
        StartTime: 1792310712,
        SubNamespace: 'engine_monitor_new',
      },
    }
    expect(validateSchemaArguments(metricSchema, values)).toEqual({
      arguments: values,
      errors: [],
    })
  })

  it('omits empty optional values while retaining required containers', () => {
    const nestedSchema = {
      properties: {
        optional: { type: 'string' },
        request: {
          properties: {
            filter: { type: 'object', properties: {} },
            tags: { type: 'array', items: { type: 'string' } },
          },
          type: 'object',
        },
      },
      required: ['request'],
      type: 'object',
    } as const

    expect(
      cleanSchemaArguments(nestedSchema, {
        optional: '',
        request: { filter: {}, tags: [] },
      }),
    ).toEqual({ request: {} })
  })

  it('resolves definitions and validates referenced fields', () => {
    const referencedSchema = {
      $defs: {
        dimension: {
          properties: { Name: { type: 'string', minLength: 1 } },
          required: ['Name'],
          type: 'object',
        },
      },
      properties: {
        dimension: { $ref: '#/$defs/dimension' },
      },
      required: ['dimension'],
      type: 'object',
    } as const

    expect(
      validateSchemaArguments(referencedSchema, { dimension: { Name: '' } })
        .errors,
    ).toEqual(
      expect.arrayContaining([expect.objectContaining({ name: 'minLength' })]),
    )
  })
})
