import { describe, expect, it } from 'vitest'

import { getTaskPipelineSteps } from './task-pipeline'

describe('task pipeline queue status', () => {
  it('keeps missing queue entries optional', () => {
    const steps = getTaskPipelineSteps(
      {
        result: {
          queue_status: {
            Semantic: { processed: 3 },
          },
        },
        status: 'running',
        task_type: 'resource_import',
      },
      'en',
    )

    expect(steps).toEqual([
      { name: 'Document Parsing', state: 'completed' },
      { count: 3, name: 'Semantic Processing', state: 'completed' },
      { count: undefined, name: 'Vector Embedding', state: 'running' },
    ])
  })
})
