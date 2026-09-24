import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  formatTaskDuration,
  formatTaskProcessingDuration,
  formatTaskWaitingDuration,
  getAverageTaskDurationSeconds,
  getTaskDate,
  getTaskDurationSeconds,
} from './task-time'

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

describe('task total duration', () => {
  afterEach(() => vi.useRealTimers())

  it.each(['pending', 'running', 'cancelling'])(
    'includes queue time for %s without resetting on stage updates',
    (status) => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-20T04:10:00Z'))
      const task = {
        status,
        created_at_iso: '2026-09-20T04:00:00Z',
        updated_at_iso: '2026-09-20T04:09:00Z',
      }
      expect(formatTaskDuration(task)).toBe('10m')
      expect(
        formatTaskDuration({ ...task, updated_at_iso: '2026-09-20T04:10:00Z' }),
      ).toBe('10m')
      vi.advanceTimersByTime(2000)
      expect(formatTaskDuration(task)).toBe('10m 2s')
    },
  )

  it.each(['completed', 'failed', 'cancelled'])(
    'freezes %s duration after completion',
    (status) => {
      vi.useFakeTimers()
      vi.setSystemTime(new Date('2026-09-20T05:00:00Z'))
      const task = {
        status,
        created_at_iso: '2026-09-20T04:00:00Z',
        updated_at_iso: '2026-09-20T04:10:00Z',
      }
      expect(formatTaskDuration(task)).toBe('10m')
      vi.advanceTimersByTime(60_000)
      expect(formatTaskDuration(task)).toBe('10m')
    },
  )

  it('keeps the same total when a running task completes', () => {
    const task = { created_at: 100, updated_at: 700 }
    expect(
      getTaskDurationSeconds({ ...task, status: 'running' }, 700_000),
    ).toBe(getTaskDurationSeconds({ ...task, status: 'completed' }))
  })

  it('does not invent durations from missing or invalid timestamps', () => {
    for (const task of [
      { status: 'completed', updated_at: 700 },
      { status: 'completed', created_at: 100 },
      { status: 'completed', created_at: 'invalid', updated_at: 700 },
      { status: 'completed', created_at: 700, updated_at: 100 },
      { status: 'unknown', created_at: 100, updated_at: 700 },
    ])
      expect(formatTaskDuration(task)).toBe('-')
  })

  it('averages valid terminal tasks only, with normalized timestamps', () => {
    expect(
      getAverageTaskDurationSeconds([
        { status: 'completed', created_at: 100, updated_at: 160 },
        { status: 'failed', created_at: '100', updated_at: '220' },
        {
          status: 'cancelled',
          created_at_iso: '2026-09-20T04:00:00Z',
          updated_at_iso: '2026-09-20T04:03:00Z',
        },
        { status: 'pending', created_at: 100, updated_at: 100 },
        { status: 'running', created_at: 100, updated_at: 101 },
        { status: 'cancelling', created_at: 100, updated_at: 102 },
        { status: 'completed', created_at: 100 },
      ]),
    ).toBe(120)
    expect(getAverageTaskDurationSeconds([])).toBeUndefined()
    expect(
      getAverageTaskDurationSeconds([{ status: 'pending', created_at: 100 }]),
    ).toBeUndefined()
    expect(
      getAverageTaskDurationSeconds([
        { status: 'completed', created_at: 100, updated_at: 100 },
      ]),
    ).toBe(0)
  })
})

describe('measured processing and waiting time', () => {
  it('separates worker time from queue and other waiting time', () => {
    const task = {
      status: 'completed',
      created_at: 100,
      updated_at: 700,
      processing_seconds: 61,
    }
    expect(formatTaskProcessingDuration(task)).toBe('1m 1s')
    expect(formatTaskWaitingDuration(task)).toBe('8m 59s')
  })

  it('does not substitute total time when measurement is unavailable', () => {
    for (const processing_seconds of [undefined, null, NaN, -1, Infinity]) {
      const task = {
        status: 'completed',
        created_at: 100,
        updated_at: 700,
        processing_seconds,
      }
      expect(formatTaskProcessingDuration(task)).toBeUndefined()
      expect(formatTaskWaitingDuration(task)).toBeUndefined()
    }
    expect(formatTaskProcessingDuration({ processing_seconds: 0 })).toBe('< 1s')
  })
})
