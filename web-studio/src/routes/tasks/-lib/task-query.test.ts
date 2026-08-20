import { describe, expect, it } from 'vitest'

import { buildTaskQuery } from './task-query'

describe('buildTaskQuery', () => {
  it('keeps the recent task query within the server limit', () => {
    expect(buildTaskQuery('24h', 'all').limit).toBe(200)
  })

  it('keeps the all-task query within the server limit', () => {
    expect(buildTaskQuery('all', 'all').limit).toBe(200)
  })
})
