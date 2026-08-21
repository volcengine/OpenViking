import { describe, expect, it } from 'vitest'

import { getTaskDate } from './task-time'

describe('getTaskDate', () => {
  it('prefers the ISO timestamp returned by the task API', () => {
    const date = getTaskDate({
      created_at: 1_774_516_075,
      created_at_iso: '2026-03-26T07:47:55+00:00',
    })

    expect(date?.toISOString()).toBe('2026-03-26T07:47:55.000Z')
  })

  it('converts legacy epoch seconds to milliseconds', () => {
    const date = getTaskDate({
      created_at: 1_774_516_075,
    })

    expect(date?.getUTCFullYear()).toBe(2026)
  })
})
