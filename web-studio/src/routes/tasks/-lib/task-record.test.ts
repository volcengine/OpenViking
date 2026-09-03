import { describe, expect, it } from 'vitest'

import {
  getTaskFailureCode,
  getTaskFailureGuidance,
  hasTaskFailureGuidance,
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

  it('shows failure guidance only when the task has a failure signal', () => {
    expect(hasTaskFailureGuidance({ error_info: {} })).toBe(false)
    expect(hasTaskFailureGuidance({ error: 'legacy failure' })).toBe(true)
    expect(
      hasTaskFailureGuidance({ error_info: { code: 'AUTH_EXPIRED' } }),
    ).toBe(true)
  })

  it('uses a fallback failure code for legacy tasks', () => {
    expect(getTaskFailureCode(undefined)).toBe('TASK_FAILURE')
    expect(getTaskFailureCode({ code: 'AUTH_EXPIRED' })).toBe('AUTH_EXPIRED')
  })

  it('keeps backend guidance for unknown Chinese error codes', () => {
    expect(
      getTaskFailureGuidance(
        { action: 'Run the repair first.', code: 'NEW_FAILURE' },
        'zh-CN',
      ),
    ).toBe('Run the repair first.')
  })

  it('recognizes task statuses that still require polling', () => {
    expect(isActiveTaskStatus('pending')).toBe(true)
    expect(isActiveTaskStatus('running')).toBe(true)
    expect(isActiveTaskStatus('cancelling')).toBe(true)
    expect(isActiveTaskStatus('completed')).toBe(false)
    expect(isActiveTaskStatus('failed')).toBe(false)
  })
})
