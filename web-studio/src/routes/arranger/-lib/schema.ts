import { createSchemaUtils } from '@rjsf/utils'
import type { RJSFSchema, RJSFValidationError } from '@rjsf/utils'
import validator from '@rjsf/validator-ajv8'

import type { JsonSchema } from './model'

export type SchemaFormObject = Record<string, unknown>

function isPlainObject(value: unknown): value is Record<string, unknown> {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value)
}

function adaptSchemaValue(value: unknown): unknown {
  if (Array.isArray(value)) return value.map(adaptSchemaValue)
  if (!isPlainObject(value)) return value

  const adapted = Object.fromEntries(
    Object.entries(value).map(([key, child]) => [key, adaptSchemaValue(child)]),
  )
  const alternatives = adapted.anyOf || adapted.oneOf
  if (!Array.isArray(alternatives)) return adapted

  const nonNull = alternatives.filter(
    (candidate) =>
      !isPlainObject(candidate) ||
      (candidate.type !== 'null' &&
        !(Array.isArray(candidate.type) && candidate.type.includes('null'))),
  )
  if (nonNull.length !== 1 || nonNull.length === alternatives.length) {
    return adapted
  }

  const result = {
    ...(nonNull[0] as Record<string, unknown>),
    ...adapted,
  }
  delete result.anyOf
  delete result.oneOf
  if (result.default === null) delete result.default
  return result
}

export function toRjsfSchema(schema: JsonSchema): RJSFSchema {
  return adaptSchemaValue(schema) as RJSFSchema
}

export function initialSchemaArguments(
  schema: JsonSchema,
  current: Record<string, unknown> = {},
): SchemaFormObject {
  const rjsfSchema = toRjsfSchema(schema)
  const value = createSchemaUtils(validator, rjsfSchema).getDefaultFormState(
    rjsfSchema,
    current,
  )
  return isPlainObject(value) ? value : {}
}

function schemaType(schema: RJSFSchema): string | undefined {
  if (typeof schema.type === 'string') return schema.type
  if (Array.isArray(schema.type)) {
    return schema.type.find((type) => type !== 'null')
  }
  if (schema.properties) return 'object'
  if (schema.items) return 'array'
  return undefined
}

function pruneSchemaValue(
  schema: RJSFSchema,
  value: unknown,
  required: boolean,
): unknown {
  if (value === undefined || (!required && value === null)) return undefined

  const type = schemaType(schema)
  if (type === 'object' && isPlainObject(value)) {
    const requiredFields = new Set(schema.required || [])
    const properties = schema.properties || {}
    const result: Record<string, unknown> = {}

    for (const [name, child] of Object.entries(value)) {
      const childSchema = properties[name]
      const next = isPlainObject(childSchema)
        ? pruneSchemaValue(childSchema, child, requiredFields.has(name))
        : child
      if (next !== undefined) result[name] = next
    }
    return !required && Object.keys(result).length === 0 ? undefined : result
  }

  if (type === 'array' && Array.isArray(value)) {
    const itemSchema = isPlainObject(schema.items) ? schema.items : undefined
    const result = itemSchema
      ? value
          .map((item) => pruneSchemaValue(itemSchema, item, true))
          .filter((item) => item !== undefined)
      : value
    return !required && result.length === 0 ? undefined : result
  }

  if (!required && value === '') return undefined
  return value
}

export function cleanSchemaArguments(
  schema: JsonSchema,
  values: SchemaFormObject,
): SchemaFormObject {
  const result = pruneSchemaValue(toRjsfSchema(schema), values, true)
  return isPlainObject(result) ? result : {}
}

export function validateSchemaArguments(
  schema: JsonSchema,
  values: SchemaFormObject,
): {
  arguments: SchemaFormObject
  errors: RJSFValidationError[]
} {
  const rjsfSchema = toRjsfSchema(schema)
  const args = cleanSchemaArguments(schema, values)
  const { errors } = validator.validateFormData(args, rjsfSchema)
  return { arguments: args, errors }
}
