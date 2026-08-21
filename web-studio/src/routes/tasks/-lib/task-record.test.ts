import { describe, expect, it } from 'vitest'

import {
  hasTaskResult,
  isActiveTaskStatus,
  normalizeTaskRecord,
  normalizeTasks,
} from './task-record'

describe('task record helpers', () => {
  it('normalizes task records from API payloads', () => {
    expect(
      normalizeTaskRecord({
        stage: 'processing_queue',
        status: 'running',
        task_id: 'task-1',
      }),
    ).toMatchObject({
      stage: 'processing_queue',
      status: 'running',
      task_id: 'task-1',
    })
  })

  it('drops invalid list entries', () => {
    expect(normalizeTasks([null, 'invalid', { task_id: 'task-1' }])).toEqual([
      { task_id: 'task-1' },
    ])
  })

  it('only reports meaningful results', () => {
    expect(hasTaskResult(undefined)).toBe(false)
    expect(hasTaskResult({})).toBe(false)
    expect(hasTaskResult([])).toBe(false)
    expect(hasTaskResult({ archive_uri: 'viking://archive' })).toBe(true)
  })

  it('recognizes task statuses that still require polling', () => {
    expect(isActiveTaskStatus('pending')).toBe(true)
    expect(isActiveTaskStatus('running')).toBe(true)
    expect(isActiveTaskStatus('cancelling')).toBe(true)
    expect(isActiveTaskStatus('completed')).toBe(false)
    expect(isActiveTaskStatus('failed')).toBe(false)
  })
})
