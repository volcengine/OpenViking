import { getOvResult } from '#/lib/ov-client'
import { createAdminClient } from '#/lib/admin'
import type { AdminConnection } from '#/lib/admin'

export const editableMemoryTypes = [
  'profile',
  'preferences',
  'entities',
  'events',
  'soul',
  'identity',
] as const

export type EditableMemoryType = (typeof editableMemoryTypes)[number]

export type MemoryTemplateSchema = {
  description: string
  directory: string
  filename_template: string
  fields: Array<{ name: string; description: string; [key: string]: unknown }>
  content_template?: string
  [key: string]: unknown
}

export type MemoryTemplate = {
  memory_type: EditableMemoryType
  status: 'system_default' | 'custom'
  updated_at: string | null
  defaults: MemoryTemplateSchema
  effective: MemoryTemplateSchema
}

const basePath = '/api/v1/admin/accounts/{account_id}/memory-templates'

export async function fetchMemoryTemplates(connection: AdminConnection) {
  return getOvResult<{ account_id: string; templates: MemoryTemplate[] }>(
    createAdminClient(connection).get({
      url: basePath,
      path: { account_id: connection.accountId },
    }),
  )
}

export async function updateMemoryTemplate(
  connection: AdminConnection,
  memoryType: EditableMemoryType,
  schema: MemoryTemplateSchema,
) {
  return getOvResult<{ account_id: string } & MemoryTemplate>(
    createAdminClient(connection).put({
      url: `${basePath}/{memory_type}`,
      path: { account_id: connection.accountId, memory_type: memoryType },
      headers: { 'Content-Type': 'application/json' },
      body: schema,
    }),
  )
}

export async function resetMemoryTemplate(
  connection: AdminConnection,
  memoryType: EditableMemoryType,
) {
  return getOvResult<{ account_id: string } & MemoryTemplate>(
    createAdminClient(connection).delete({
      url: `${basePath}/{memory_type}`,
      path: { account_id: connection.accountId, memory_type: memoryType },
    }),
  )
}

export function withTemplateField(
  schema: MemoryTemplateSchema,
  field: string,
  value: string,
): MemoryTemplateSchema {
  if (field === 'description' || field === 'content_template') {
    return { ...schema, [field]: value }
  }
  const fieldName = field.split('.')[1]
  return {
    ...schema,
    fields: schema.fields.map((item) =>
      item.name === fieldName ? { ...item, description: value } : item,
    ),
  }
}

export function templateFieldValue(
  schema: MemoryTemplateSchema,
  field: string,
) {
  if (field === 'description' || field === 'content_template') {
    return String(schema[field] ?? '')
  }
  return (
    schema.fields.find((item) => item.name === field.split('.')[1])
      ?.description ?? ''
  )
}
