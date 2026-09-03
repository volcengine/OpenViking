import { describe, expect, it } from 'vitest'

import { getTaskPipelineSteps } from './task-pipeline'

describe('task pipeline helpers', () => {
  it('handles a queue status without a Semantic entry', () => {
    const steps = getTaskPipelineSteps({
      result: {
        queue_status: {
          Embedding: { processed: 2 },
        },
      },
      status: 'running',
      task_type: 'resource_import',
    })

    expect(steps[1]?.count).toBeUndefined()
    expect(steps[2]?.count).toBe(2)
  })

  it('handles a queue status without an Embedding entry', () => {
    const steps = getTaskPipelineSteps({
      result: {
        queue_status: {
          Semantic: { processed: 3 },
        },
      },
      status: 'running',
      task_type: 'resource_import',
    })

    expect(steps[1]?.count).toBe(3)
    expect(steps[2]?.count).toBeUndefined()
  })
})
