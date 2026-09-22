import { describe, expect, it } from 'vitest'

import { mergeServerTasks } from './resource-upload-tasks'
import type { ResourceUploadTask } from '../-hooks/use-resource-upload'
import type { TaskRecord } from '@ov-server/api/v1/tasks'

function serverRecord(error?: string): TaskRecord {
  return {
    error,
    status: 'failed',
    task_id: 'task-1',
    task_type: 'add_resource',
  } as TaskRecord
}

function previousTask(
  errorMessage: string,
  errorMessageOrigin?: ResourceUploadTask['errorMessageOrigin'],
  errorDetail?: string,
): ResourceUploadTask {
  return {
    createdAt: 1,
    errorCode: 'SERVER_TASK_FAILED',
    errorDetail,
    errorMessage,
    errorMessageOrigin,
    fileName: 'document.pdf',
    fileSize: null,
    fileType: null,
    finishedAt: 2,
    id: 'server-task-1',
    progress: null,
    rootUri: null,
    serverTaskId: 'task-1',
    source: 'server',
    status: 'failed',
  }
}

describe('mergeServerTasks', () => {
  it('preserves an existing server error when a later record omits it', () => {
    const [task] = mergeServerTasks(
      [previousTask('provider overloaded', 'server', 'provider overloaded')],
      [serverRecord()],
      { cancelled: 'Cancelled', failed: 'Processing failed' },
    )

    expect(task.errorMessage).toBe('provider overloaded')
    expect(task.errorMessageOrigin).toBe('server')
    expect(task.errorDetail).toBe('provider overloaded')
  })

  it('refreshes a generated fallback when the locale changes', () => {
    const [task] = mergeServerTasks(
      [previousTask('Processing failed', 'fallback')],
      [serverRecord()],
      { cancelled: '已取消', failed: '处理失败' },
    )

    expect(task.errorMessage).toBe('处理失败')
    expect(task.errorMessageOrigin).toBe('fallback')
    expect(task.errorDetail).toBeNull()
  })

  it('localizes the generic server processing error', () => {
    const [task] = mergeServerTasks(
      [],
      [serverRecord('resource processing failed')],
      { cancelled: '已取消', failed: '处理失败' },
    )

    expect(task.errorMessage).toBe('处理失败')
    expect(task.errorMessageOrigin).toBe('fallback')
    expect(task.errorDetail).toBe('resource processing failed')
  })

  it('preserves a detailed server error', () => {
    const [task] = mergeServerTasks([], [serverRecord('provider overloaded')], {
      cancelled: '已取消',
      failed: '处理失败',
    })

    expect(task.errorMessage).toBe('provider overloaded')
    expect(task.errorMessageOrigin).toBe('server')
    expect(task.errorDetail).toBe('provider overloaded')
  })

  it('keeps a detailed raw error when a later record is generic', () => {
    const [task] = mergeServerTasks(
      [previousTask('provider overloaded', 'server', 'provider overloaded')],
      [serverRecord('resource processing failed')],
      { cancelled: '已取消', failed: '处理失败' },
    )

    expect(task.errorMessage).toBe('provider overloaded')
    expect(task.errorDetail).toBe('provider overloaded')
  })
})
