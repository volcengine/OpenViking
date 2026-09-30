import { describe, expect, it } from 'vitest'
import { templateFieldValue, withTemplateField } from './memory-templates'
import type { MemoryTemplateSchema } from './memory-templates'

const schema: MemoryTemplateSchema = {
  memory_type: 'events',
  description: 'Event extraction',
  directory: 'viking://user/{{ user_space }}/memories/events',
  filename_template:
    '{{ extract_context.get_year(ranges) }}/{{ extract_context.get_month(ranges) }}/{{ extract_context.get_day(ranges) }}/{{ event_name }}.md',
  enabled: true,
  fields: [
    { name: 'event_name', description: 'Name', merge_op: 'replace' },
    { name: 'summary', description: 'Summary', merge_op: 'patch' },
  ],
  content_template: '{{ summary }}',
}

describe('memory template edits', () => {
  it('changes only the selected description and preserves locked schema fields', () => {
    const updated = withTemplateField(
      schema,
      'fields.summary.description',
      'New summary rule',
    )

    expect(templateFieldValue(updated, 'fields.summary.description')).toBe(
      'New summary rule',
    )
    expect(updated.fields[0]).toEqual(schema.fields[0])
    expect(updated.fields[1].merge_op).toBe('patch')
    expect(updated.enabled).toBe(true)
    expect(schema.fields[1].description).toBe('Summary')
  })

  it('updates a type description without dropping the other fields', () => {
    const updated = withTemplateField(schema, 'description', 'New rule')
    expect(updated.description).toBe('New rule')
    expect(updated.fields).toEqual(schema.fields)
    expect(updated.content_template).toBe('{{ summary }}')
  })
})
